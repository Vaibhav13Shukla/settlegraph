from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineConfig(BaseSettings):
    """Reviewable configuration; financial thresholds are never magic numbers."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="SETTLEGRAPH_")

    default_currency: str = "INR"
    random_seed: int = 42
    amount_tolerance_paise: int = 100
    date_tolerance_days: int = 3
    auto_match_threshold: float = 0.95
    exception_threshold: float = 0.70
    # "No competing explanation" -- the middle clause of this project's rule
    # for when automation is allowed to act. If the runner-up candidate for
    # the same record, on the same reconciliation leg, scores within this
    # margin of the winner, the win is a tie-break rather than evidence, and
    # the record is held for review instead of auto-booked. Configurable per
    # merchant like every other threshold: one with a higher tolerance for
    # review volume can widen it.
    ambiguity_margin: float = 0.05
    generated_data_directory: Path = Path("data/generated")
    llm_provider: str = "none"
    history_enabled: bool = True
