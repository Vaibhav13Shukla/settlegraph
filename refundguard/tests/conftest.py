"""Make the package importable without an editable install.

RefundGuard has no runtime dependencies. A reviewer should be able to clone the
repo and run pytest without a build step, so the source directory is put on the
path here rather than requiring `pip install -e .`.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
