"""Trending hashtags from Square's Trending panel, kept by hand in trending.txt."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .store import utcnow

TRENDING_FILE = Path(__file__).resolve().parents[2] / "trending.txt"
LINE = re.compile(r"(#\w\S*)\s+(\d{4}-\d{2}-\d{2})(?:\s+coin:([A-Z0-9]+))?\s+(.+)")


@dataclass
class Trend:
    tag: str
    expires: str
    coin: str | None
    about: str


def trends(path: Path = TRENDING_FILE, today: str | None = None) -> list[Trend]:
    """Unexpired lines: '#Tag YYYY-MM-DD [coin:XXX] what a post must be about'."""
    today = today or utcnow().date().isoformat()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        m = LINE.match(line.strip())
        if m and m.group(2) >= today:
            out.append(Trend(m.group(1), m.group(2), m.group(3), m.group(4)))
    return out


def trending_hashtags(path: Path = TRENDING_FILE, today: str | None = None) -> list[str]:
    return [f"{t.tag}: {t.about}" for t in trends(path, today)]


def trending_coins(path: Path = TRENDING_FILE, today: str | None = None) -> set[str]:
    """Coins that have their own trending topic: a post on them can join that feed."""
    return {t.coin for t in trends(path, today) if t.coin}
