"""One scheduler tick: gate -> data -> plan -> write -> check -> review -> publish/queue."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from .charts import render_price_chart
from .config import Settings
from .market import MarketClient
from .news import fetch_headlines
from .planner import Brief, Planner
from .quality import cashtags, check_draft
from .square import SquareClient, SquareError, resolve_api_key
from .store import Draft, HistoryEntry, Store, utcnow
from .writer import Writer

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2


@dataclass
class TickResult:
    status: str  # skipped | rejected | queued | dry-run | published | failed
    detail: str = ""
    draft: Draft | None = None
    entry: HistoryEntry | None = None


def schedule_gate(settings: Settings, store: Store, now: datetime, rng: random.Random) -> str | None:
    """Return a reason to skip this tick, or None if a post may go out."""
    sch = settings.schedule
    start, end = sch.active_hours_utc
    if not start <= now.hour < end:
        return f"outside active hours {start}-{end} UTC"
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = store.published_since(day_start)
    if len(today) >= sch.max_posts_per_day:
        return f"daily cap reached ({len(today)}/{sch.max_posts_per_day})"
    last = store.last_published()
    if last and now - last.published_dt < timedelta(minutes=sch.min_minutes_between_posts):
        return f"last post was less than {sch.min_minutes_between_posts} min ago"
    if settings.mode == "review" and len(store.queued()) >= sch.max_posts_per_day:
        return "review queue is full; approve or reject drafts first"
    if rng.random() >= sch.post_probability:
        return "random skip (keeps posting times irregular)"
    return None


def produce_draft(settings: Settings, store: Store, writer: Writer, brief: Brief, tradable: set[str]) -> TickResult:
    recent = store.recent_texts(60)
    feedback: list[str] | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        post = writer.write(brief, recent, feedback)
        check = check_draft(post.text, post.title or None, settings.quality, tradable, recent)
        if not check.ok:
            log.info("attempt %d failed checks: %s", attempt, check.problems)
            feedback = check.problems
            continue
        review = writer.review(brief, post)
        if review.score >= settings.quality.min_score and not review.factual_errors:
            draft = Draft(format=brief.format, text=post.text, title=post.title or None,
                          coins=cashtags(post.text), score=review.score, review_notes=review.issues)
            return TickResult("ok", draft=draft)
        log.info("attempt %d scored %d: %s %s", attempt, review.score, review.factual_errors, review.issues)
        feedback = review.factual_errors + review.issues + [f"editor score was {review.score}/10"]
    return TickResult("rejected", detail="; ".join(feedback or []))


def publish_draft(settings: Settings, store: Store, draft: Draft, square: SquareClient | None) -> HistoryEntry:
    entry = HistoryEntry(draft_id=draft.id, format=draft.format, coins=draft.coins, text=draft.text,
                         title=draft.title, status="dry-run", published_at=utcnow().isoformat())
    if square is None:
        store.append(entry)
        return entry
    try:
        result = square.publish(draft.text, title=draft.title, image_path=draft.image_path)
        entry.status, entry.post_id, entry.link = "published", result.post_id, result.link
    except (SquareError, RuntimeError, OSError) as e:
        entry.status, entry.error = "failed", str(e)
        log.error("publish failed: %s", e)
    entry.published_at = utcnow().isoformat()
    store.append(entry)
    return entry


def run_tick(settings: Settings, *, force: bool = False, force_format: str | None = None,
             force_coin: str | None = None, market: MarketClient | None = None,
             writer: Writer | None = None, square: SquareClient | None = None,
             rng: random.Random | None = None) -> TickResult:
    store = Store(settings.data_path)
    rng = rng or random.Random()

    if not force:
        reason = schedule_gate(settings, store, utcnow(), rng)
        if reason:
            return TickResult("skipped", reason)

    # Fail fast on a missing key, before any paid LLM call.
    if settings.mode == "auto" and square is None:
        square = SquareClient(resolve_api_key())

    market = market or MarketClient()
    snap = market.snapshot()
    headlines = []
    if settings.content.formats.get("news_take", 0) > 0 or force_format == "news_take":
        headlines = fetch_headlines(settings.news.feeds, settings.news.max_age_hours)

    brief = Planner(settings, store, market, rng).plan(snap, headlines, force_format, force_coin)
    log.info("plan: %s %s", brief.format, brief.coins)

    result = produce_draft(settings, store, writer or Writer(settings), brief, snap.tradable_bases())
    if result.status == "rejected":
        return result
    draft = result.draft
    assert draft is not None

    if brief.chart is not None:
        draft.image_path = str(render_price_chart(brief.chart, settings.data_path / "charts",
                                                  settings.content.watermark))

    if settings.mode == "review":
        store.enqueue(draft)
        return TickResult("queued", f"draft {draft.id} waiting for approval", draft=draft)

    if settings.mode == "dry-run":
        entry = publish_draft(settings, store, draft, None)
        return TickResult("dry-run", draft=draft, entry=entry)

    entry = publish_draft(settings, store, draft, square)
    return TickResult(entry.status, entry.link or entry.error or "", draft=draft, entry=entry)
