"""Writer-in-the-loop mode: no Anthropic API key needed.

`w2e brief` gathers data and prints a writing packet. Whoever writes the post
(a scheduled Claude Code session, or you) saves it to a file, then
`w2e submit` runs the same quality gate as the API mode and publishes.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from .charts import render_price_chart
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
    image = None
    if brief.chart is not None:
        image = str(render_price_chart(brief.chart, settings.data_path / "charts", settings.content.watermark))

    pending = {
        "format": brief.format, "coins": brief.coins, "article": brief.article, "data": brief.data,
        "image_path": image, "tradable": sorted(snap.tradable_bases()), "created_at": utcnow().isoformat(),
    }
    p = pending_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pending, ensure_ascii=False, indent=1), encoding="utf-8")
    return BriefResult("ready", str(p), render_packet(settings, store, pending))


def render_packet(settings: Settings, store: Store, pending: dict) -> str:
    kind = "long-form ARTICLE with a title" if pending["article"] else "SHORT POST (no title)"
    recent = "\n".join(f"- {t[:160]!r}" for t in store.recent_texts(60)[-6:]) or "(none yet)"
    chart = (f"A 7-day chart of the main coin will be attached ({pending['image_path']}). You may refer to it."
             if pending["image_path"] else "No image will be attached.")
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
