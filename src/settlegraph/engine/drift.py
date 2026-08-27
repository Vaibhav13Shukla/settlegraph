"""ADWIN (Adaptive Windowing) concept drift detection for financial settlement streams.

Theoretical Foundation:
Bifet, A., & Gavaldà, R. (2007). "Learning from Time-Changing Data with Adaptive Windowing."
Proceedings of the 2007 SIAM International Conference on Data Mining (SDM 2007), pp. 443–452.
"""

from __future__ import annotations

import math
from typing import Any


class ADWINDetector:
    """Adaptive Windowing algorithm for detecting distribution drift in financial time series.

    Monitors a sliding stream of numeric observations (e.g. confidence scores, delay days, fee ratios).
    When the difference between sub-windows exceeds the statistical cut threshold derived via
    Hoeffding's inequality, a drift alert is emitted and older data is truncated.
    """

    def __init__(self, delta: float = 0.002, max_window_size: int = 500) -> None:
        self.delta = delta
        self.max_window_size = max_window_size
        self.window: list[float] = []
        self.drift_detected: bool = False
        self.drift_history: list[dict[str, Any]] = []

    def add_element(self, value: float) -> bool:
        """Add a new observation to the sliding window and test for drift.

        Returns True if a concept drift was detected at this step.
        """
        self.window.append(value)
        if len(self.window) > self.max_window_size:
            self.window.pop(0)

        self.drift_detected = False
        n = len(self.window)
        if n < 10:
            return False

        # Evaluate potential partition points
        for i in range(5, n - 5):
            w0 = self.window[:i]
            w1 = self.window[i:]

            n0, n1 = len(w0), len(w1)
            mean0 = sum(w0) / n0
            mean1 = sum(w1) / n1

            m = 1.0 / (1.0 / n0 + 1.0 / n1)
            delta_p = self.delta / n
            eps_cut = math.sqrt((1.0 / (2.0 * m)) * math.log(4.0 / delta_p))

            if abs(mean0 - mean1) > eps_cut:
                self.drift_detected = True
                self.drift_history.append(
                    {
                        "step": len(self.drift_history) + 1,
                        "window_size": n,
                        "split_point": i,
                        "mean_before": round(mean0, 4),
                        "mean_after": round(mean1, 4),
                        "diff": round(abs(mean0 - mean1), 4),
                        "threshold": round(eps_cut, 4),
                    }
                )
                # Drop older subwindow w0
                self.window = self.window[i:]
                break

        return self.drift_detected

    def reset(self) -> None:
        """Reset the detector state."""
        self.window.clear()
        self.drift_detected = False
        self.drift_history.clear()

    @property
    def current_mean(self) -> float:
        return sum(self.window) / len(self.window) if self.window else 0.0
