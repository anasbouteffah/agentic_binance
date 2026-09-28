"""Render a clean 7-day price chart PNG for a coin post."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from .market import CoinDetail  # noqa: E402

BG = "#0b0e11"
GRID = "#2b3139"
TEXT = "#eaecef"
MUTED = "#848e9c"
UP = "#0ecb81"
DOWN = "#f6465d"
ACCENT = "#f0b90b"


def render_price_chart(detail: CoinDetail, out_dir: Path, watermark: str = "") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    times = [datetime.fromtimestamp(t / 1000, tz=timezone.utc) for t in detail.times_1h]
    closes = detail.closes_1h
    color = UP if closes[-1] >= closes[0] else DOWN

    fig, ax = plt.subplots(figsize=(12, 6.75), dpi=100)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    ax.plot(times, closes, color=color, linewidth=2)
    ax.fill_between(times, closes, min(closes), color=color, alpha=0.08)

    ax.axhline(detail.high_7d, color=MUTED, linestyle="--", linewidth=1)
    ax.axhline(detail.low_7d, color=MUTED, linestyle="--", linewidth=1)
    ax.text(times[0], detail.high_7d, f" 7d high {detail.high_7d:,.4g}", color=MUTED, va="bottom", fontsize=11)
    ax.text(times[0], detail.low_7d, f" 7d low {detail.low_7d:,.4g}", color=MUTED, va="top", fontsize=11)

    ax.grid(color=GRID, linewidth=0.6)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors=MUTED, labelsize=11)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.yaxis.tick_right()

    sign = "+" if detail.change_7d_pct >= 0 else ""
    fig.text(0.04, 0.93, f"{detail.base}/USDT", color=TEXT, fontsize=22, fontweight="bold")
    fig.text(0.04, 0.88, f"{detail.price:,.6g}   7d {sign}{detail.change_7d_pct:.2f}%   "
                         f"24h {detail.change_24h_pct:+.2f}%", color=color, fontsize=14)
    if watermark:
        fig.text(0.96, 0.03, watermark, color=ACCENT, fontsize=11, ha="right", alpha=0.8)
    fig.text(0.04, 0.03, "Data: Binance spot, 1h candles", color=MUTED, fontsize=10)

    fig.subplots_adjust(left=0.04, right=0.9, top=0.84, bottom=0.1)
    path = out_dir / f"{detail.base}_{datetime.now(timezone.utc):%Y%m%d%H%M}.png"
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    return path
