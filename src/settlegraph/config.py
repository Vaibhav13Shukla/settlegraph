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
    generated_data_directory: Path = Path("data/generated")
    llm_provider: str = "none"
