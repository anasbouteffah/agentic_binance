"""Load config.yaml into typed settings."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class LLMSettings(BaseModel):
    model: str = "claude-opus-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"


class ScheduleSettings(BaseModel):
    max_posts_per_day: int = 3
    min_minutes_between_posts: int = 180
    active_hours_utc: tuple[int, int] = (7, 22)
    post_probability: float = 0.6


class WeeklyArticle(BaseModel):
    enabled: bool = True
    weekday: int = 6


class ContentSettings(BaseModel):
    formats: dict[str, int] = Field(
        default_factory=lambda: {"market_pulse": 3, "coin_focus": 4, "news_take": 2, "explainer": 1}
    )
    weekly_article: WeeklyArticle = Field(default_factory=WeeklyArticle)
    watchlist: list[str] = Field(default_factory=lambda: ["BTC", "ETH", "BNB", "SOL"])
    min_quote_volume_usdt: float = 20_000_000
    coin_cooldown_hours: int = 12
    charts: bool = True
    watermark: str = "Binance Square"


class QualitySettings(BaseModel):
    min_score: int = 7
    max_similarity: float = 0.55
    max_cashtags: int = 3
    short_post_chars: tuple[int, int] = (280, 1400)
    article_chars: tuple[int, int] = (2500, 9000)


class NewsSettings(BaseModel):
    feeds: list[str] = Field(default_factory=list)
    max_age_hours: int = 12


class Settings(BaseModel):
    mode: Literal["auto", "review", "dry-run"] = "review"
    language: str = "en"
    llm: LLMSettings = Field(default_factory=LLMSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    content: ContentSettings = Field(default_factory=ContentSettings)
    quality: QualitySettings = Field(default_factory=QualitySettings)
    news: NewsSettings = Field(default_factory=NewsSettings)
    persona_file: str = "persona.md"
    data_dir: str = "data"

    # Directory config.yaml was loaded from; relative paths resolve against it.
    root: Path = Path(".")

    def path(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else self.root / p

    @property
    def data_path(self) -> Path:
        return self.path(self.data_dir)

    def persona(self) -> str:
        p = self.path(self.persona_file)
        return p.read_text(encoding="utf-8") if p.exists() else ""


def load_settings(path: str | Path = "config.yaml") -> Settings:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    settings = Settings.model_validate(raw or {})
    settings.root = path.resolve().parent
    return settings
