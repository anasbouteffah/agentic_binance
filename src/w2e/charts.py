"""Chart PNGs attached to posts.

Square shows images at roughly a third of their width in the feed, so every
label is sized to stay readable on a phone.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402

from .market import CoinDetail  # noqa: E402

BG = "#0b0e11"
GRID = "#2b3139"
TEXT = "#eaecef"
MUTED = "#848e9c"
UP = "#0ecb81"
DOWN = "#f6465d"
ACCENT = "#f0b90b"

FIGSIZE = (12, 6.75)
TICK_SIZE = 20
LABEL_SIZE = 20


def fmt_price(v: float) -> str:
    """Plain price text, never scientific notation: 83,695 / 272.66 / 0.005904 / 0.00000426."""
    a = abs(v)
    if a >= 1000:
        return f"{v:,.0f}"
    if a >= 1:
        return f"{v:,.2f}"
    if a == 0:
        return "0"
    # Four significant digits after the leading zeros, trailing zeros dropped.
    decimals = min(-math.floor(math.log10(a)) + 3, 12)
    return f"{v:.{decimals}f}".rstrip("0").rstrip(".")


def _pct_color(v: float) -> str:
    return UP if v >= 0 else DOWN


def _text_row(fig, x: float, y: float, parts: list[tuple[str, str]], fontsize: int) -> None:
    """Draw (text, color) parts side by side on one line."""
    renderer = fig.canvas.get_renderer()
    for text, color in parts:
        t = fig.text(x, y, text, color=color, fontsize=fontsize, fontweight="bold")
        x = fig.transFigure.inverted().transform(t.get_window_extent(renderer).corners()[-1])[0]


def _new_figure():
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=100)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors=MUTED, labelsize=TICK_SIZE, length=0)
    return fig, ax


def _footer(fig, source: str, watermark: str) -> None:
    fig.text(0.03, 0.03, source, color=MUTED, fontsize=14)
    # Centered: Square stamps its own "@handle" over the bottom-right corner.
    if watermark:
        fig.text(0.5, 0.03, watermark, color=ACCENT, fontsize=16, ha="center", alpha=0.85)


def _save(fig, out_dir: Path, name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}_{datetime.now(timezone.utc):%Y%m%d%H%M}.png"
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    return path


def render_price_chart(detail: CoinDetail, out_dir: Path, watermark: str = "") -> Path:
    times = [datetime.fromtimestamp(t / 1000, tz=timezone.utc) for t in detail.times_1h]
    closes = detail.closes_1h
    color = UP if closes[-1] >= closes[0] else DOWN

    fig, ax = _new_figure()
    ax.plot(times, closes, color=color, linewidth=3)
    ax.fill_between(times, closes, min(closes), color=color, alpha=0.10)

    ax.axhline(detail.high_7d, color=MUTED, linestyle="--", linewidth=1.2)
    ax.axhline(detail.low_7d, color=MUTED, linestyle="--", linewidth=1.2)
    ax.text(times[0], detail.high_7d, f" 7d high {fmt_price(detail.high_7d)}", color=TEXT,
            va="bottom", fontsize=LABEL_SIZE)
    ax.text(times[0], detail.low_7d, f" 7d low {fmt_price(detail.low_7d)}", color=TEXT,
            va="top", fontsize=LABEL_SIZE)
    ax.plot([times[-1]], [closes[-1]], "o", color=color, markersize=9)

    ax.grid(color=GRID, linewidth=0.8)
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.yaxis.set_major_locator(MaxNLocator(4))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt_price(v)))
    ax.yaxis.tick_right()
    pad = (detail.high_7d - detail.low_7d) * 0.08
    ax.set_ylim(detail.low_7d - pad, detail.high_7d + pad)

    _text_row(fig, 0.03, 0.89, [(f"${detail.base}  ", TEXT), (fmt_price(detail.price), TEXT)], 38)
    _text_row(fig, 0.03, 0.82, [("24h ", MUTED), (f"{detail.change_24h_pct:+.2f}%", _pct_color(detail.change_24h_pct)),
                                ("    7d ", MUTED), (f"{detail.change_7d_pct:+.2f}%", _pct_color(detail.change_7d_pct))],
              24)
    _footer(fig, "Binance spot, 1h, last 7 days", watermark)

    fig.subplots_adjust(left=0.03, right=0.84, top=0.78, bottom=0.12)
    return _save(fig, out_dir, detail.base)


def movers_rows(market: dict, n_each: int = 4) -> list[dict]:
    """Majors plus top gainers and losers from a MarketSnapshot.brief(), no duplicates."""
    rows: list[dict] = []
    seen: set[str] = set()
    for key, n in (("top_gainers", n_each), ("majors", 4), ("top_losers", n_each)):
        for r in market.get(key, [])[:n]:
            if r["coin"] not in seen and r.get("change_24h_pct") is not None:
                seen.add(r["coin"])
                rows.append(r)
    return rows


def render_movers_chart(market: dict, out_dir: Path, watermark: str = "") -> Path | None:
    """Horizontal bars of 24h % change: gainers, majors, losers."""
    rows = movers_rows(market)
    if len(rows) < 3:
        return None
    rows.sort(key=lambda r: r["change_24h_pct"])
    labels = [r["coin"] for r in rows]
    values = [r["change_24h_pct"] for r in rows]
    colors = [UP if v >= 0 else DOWN for v in values]

    fig, ax = _new_figure()
    bars = ax.barh(labels, values, color=colors, height=0.7)
    ax.axvline(0, color=MUTED, linewidth=1.2)
    span = max(abs(v) for v in values) or 1
    ax.set_xlim(-span * 1.35, span * 1.35)
    for bar, v in zip(bars, values):
        x = bar.get_width()
        ax.text(x + (span * 0.03 if v >= 0 else -span * 0.03), bar.get_y() + bar.get_height() / 2,
                f"{v:+.1f}%", va="center", ha="left" if v >= 0 else "right", color=TEXT, fontsize=16)
    ax.tick_params(axis="y", labelsize=16, labelcolor=TEXT)
    ax.set_xticks([])
    ax.grid(False)

    fig.text(0.03, 0.9, "24h movers", color=TEXT, fontsize=36, fontweight="bold")
    fg = market.get("fear_greed")
    if fg:
        fig.text(0.97, 0.91, f"Fear & Greed {fg['value']} ({fg['label']})", color=ACCENT,
                 fontsize=22, ha="right", fontweight="bold")
    _footer(fig, "Binance spot, USDT pairs", watermark)

    fig.subplots_adjust(left=0.12, right=0.97, top=0.85, bottom=0.1)
    return _save(fig, out_dir, "movers")


def render_brief_image(brief, out_dir: Path, watermark: str = "") -> Path | None:
    """The image for a planner Brief: coin price chart, else movers chart, else none."""
    if brief.chart is not None:
        return render_price_chart(brief.chart, out_dir, watermark)
    if getattr(brief, "movers", None):
        return render_movers_chart(brief.movers, out_dir, watermark)
    return None
