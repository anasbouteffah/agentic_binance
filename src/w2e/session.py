"""Writer-in-the-loop mode: no Anthropic API key needed.

`w2e brief` gathers data and prints a writing packet. Whoever writes the post
(a scheduled Claude Code session, or you) saves it to a file, then
`w2e submit` runs the same quality gate as the API mode and publishes.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

from .charts import render_brief_image
from .config import Settings
from .market import MarketClient
from .pipeline import gather_brief, publish_draft, schedule_gate
from .quality import cashtags, check_draft
from .square import SquareClient, resolve_api_key
from .store import Draft, HistoryEntry, Store, utcnow
from .writer import FORMAT_GUIDE, REVIEW_SYSTEM, system_prompt


def pending_path(settings: Settings) -> Path:
    return settings.data_path / "pending" / "brief.json"


@dataclass
class BriefResult:
    status: str  # skipped | ready
    detail: str = ""
    packet: str = ""


def prepare(settings: Settings, *, force: bool = False, force_format: str | None = None,
            force_coin: str | None = None, market: MarketClient | None = None,
            rng: random.Random | None = None, random_skip: bool = True) -> BriefResult:
    store = Store(settings.data_path)
    rng = rng or random.Random()
    if not force:
        reason = schedule_gate(settings, store, utcnow(), rng, random_skip)
        if reason:
            return BriefResult("skipped", reason)

    brief, snap = gather_brief(settings, store, market, rng, force_format, force_coin)
    img = render_brief_image(brief, settings.data_path / "charts", settings.content.watermark)
    image = str(img) if img is not None else None

    pending = {
        "format": brief.format, "coins": brief.coins, "article": brief.article, "data": brief.data,
        "image_path": image, "tradable": sorted(snap.tradable_bases()), "created_at": utcnow().isoformat(),
    }
    p = pending_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pending, ensure_ascii=False, indent=1), encoding="utf-8")
    return BriefResult("ready", str(p), render_packet(settings, store, pending))


TRENDING_FILE = Path(__file__).resolve().parents[2] / "trending.txt"


def trending_hashtags(path: Path = TRENDING_FILE, today: str | None = None) -> list[str]:
    """Unexpired lines of trending.txt: '#Tag YYYY-MM-DD when to use it'."""
    today = today or utcnow().date().isoformat()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        m = re.match(r"(#\w\S*)\s+(\d{4}-\d{2}-\d{2})\s+(.+)", line.strip())
        if m and m.group(2) >= today:
            out.append(f"{m.group(1)}: {m.group(3)}")
    return out


def render_packet(settings: Settings, store: Store, pending: dict) -> str:
    kind = "long-form ARTICLE with a title" if pending["article"] else "SHORT POST (no title)"
    recent = "\n".join(f"- {t[:160]!r}" for t in store.recent_texts(60)[-6:]) or "(none yet)"
    chart = (f"This image will be attached ({pending['image_path']}): "
             f"{'a 24h movers bar chart' if 'movers_' in pending['image_path'] else 'a 7-day price chart of the main coin'}. "
             "You may refer to it."
             if pending["image_path"] else "No image will be attached.")
    tags = trending_hashtags()
    trending = ("Trending tags put a post in a feed many more people read. If, and only if, this post is "
                "really about one of these topics, use that tag as one of your 1-2 hashtags. Never add a "
                "trending tag to an unrelated post (that is tag spam):\n" + "\n".join(f"- {t}" for t in tags)
                if tags else "(none listed)")
    return f"""# WRITING TASK
Write one {kind}. Format: {pending['format']}.
{chart}

## Writer rules
{system_prompt(settings)}

## Format guide
{FORMAT_GUIDE[pending['format']]}

## DATA (the only source of facts)
{json.dumps(pending['data'], ensure_ascii=False, indent=1)}

## Author's recent posts (do not repeat their openings, angles or structure)
{recent}

## Trending hashtags on Square now
{trending}

## Self-review before submitting
{REVIEW_SYSTEM}
Only submit a draft you would score 7 or higher with zero factual errors.

## Submit
Save the body to a text file, then run:
  w2e submit --text-file <file>{' --title "<title>"' if pending['article'] else ''}
If the quality gate prints problems, fix them and submit again.
"""


@dataclass
class SubmitResult:
    ok: bool
    problems: list[str]
    entry: HistoryEntry | None = None


def submit(settings: Settings, text: str, title: str | None = None,
           square: SquareClient | None = None) -> SubmitResult:
    p = pending_path(settings)
    if not p.exists():
        return SubmitResult(False, ["no pending brief: run `w2e brief` first"])
    pending = json.loads(p.read_text(encoding="utf-8"))
    text = text.strip()
    title = (title or "").strip() or None
    if pending["article"] and not title:
        return SubmitResult(False, ["this brief is an article: pass --title"])
    if not pending["article"]:
        title = None

    store = Store(settings.data_path)
    check = check_draft(text, title, settings.quality, set(pending["tradable"]), store.recent_texts(60))
    if not check.ok:
        return SubmitResult(False, check.problems)

    draft = Draft(format=pending["format"], text=text, title=title, coins=cashtags(text),
                  image_path=pending["image_path"])
    if settings.mode == "auto" and square is None:
        square = SquareClient(resolve_api_key())
    entry = publish_draft(settings, store, draft, square if settings.mode == "auto" else None)
    if entry.status != "failed":
        p.unlink()
    return SubmitResult(entry.status != "failed", [entry.error] if entry.error else [], entry)
