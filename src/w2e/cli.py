"""Command line entry point: `w2e <command>`."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

from .config import load_settings
from .pipeline import publish_draft, run_tick
from .square import KEY_FILE, SquareClient, mask_key, resolve_api_key
from .store import Store, utcnow

FORMATS = ["market_pulse", "coin_focus", "news_take", "explainer", "weekly_article"]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _print_draft(d) -> None:
    print(f"--- [{d.id}] {d.format}  coins={d.coins}  score={d.score}")
    if d.title:
        print(f"TITLE: {d.title}")
    print(d.text)
    if d.image_path:
        print(f"IMAGE: {d.image_path}")
    if d.review_notes:
        print("editor notes: " + " | ".join(d.review_notes))
    print()


def cmd_run(args, settings) -> int:
    if args.mode:
        settings.mode = args.mode
    res = run_tick(settings, force=args.force, force_format=args.format, force_coin=args.coin)
    print(f"status: {res.status}  {res.detail}")
    if res.draft:
        _print_draft(res.draft)
    return 1 if res.status == "failed" else 0


def cmd_queue(args, settings) -> int:
    drafts = Store(settings.data_path).queued()
    if not drafts:
        print("queue is empty")
    for d in drafts:
        _print_draft(d)
    return 0


def cmd_approve(args, settings) -> int:
    store = Store(settings.data_path)
    draft = store.get_queued(args.id)
    if args.text_file:
        draft.text = Path(args.text_file).read_text(encoding="utf-8").strip()
    entry = publish_draft(settings, store, draft, SquareClient(resolve_api_key()))
    if entry.status == "published":
        store.dequeue(draft.id)
        print(f"published: {entry.link or entry.post_id or '(no link returned)'}")
        return 0
    print(f"failed: {entry.error}  (draft kept in queue)")
    return 1


def cmd_reject(args, settings) -> int:
    Store(settings.data_path).dequeue(args.id)
    print(f"removed {args.id}")
    return 0


def cmd_stats(args, settings) -> int:
    store = Store(settings.data_path)
    hist = store.history()
    pub = store.published()
    week = store.published_since(utcnow() - timedelta(days=7))
    print(f"published total: {len(pub)}   last 7 days: {len(week)}   failed: "
          f"{sum(h.status == 'failed' for h in hist)}   queued: {len(store.queued())}")
    print("formats (7d):", dict(Counter(h.format for h in week)))
    print("coins (7d):", dict(Counter(c for h in week for c in h.coins).most_common(10)))
    for h in pub[-5:]:
        print(f"  {h.published_at[:16]}  {h.format:15} {h.link or h.post_id or ''}")
    print("\nEarnings are only visible in the Binance app: Square > Creator Center > Write to Earn.")
    return 0


def cmd_save_key(args, settings) -> int:
    key = os.environ.get("BINANCE_SQUARE_OPENAPI_KEY", "").strip()
    if not key:
        print("Set BINANCE_SQUARE_OPENAPI_KEY in the environment first (never pass keys as arguments).")
        return 1
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    KEY_FILE.write_text(key + "\n", encoding="utf-8")
    KEY_FILE.chmod(0o600)
    print(f"saved {mask_key(key)} to {KEY_FILE}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="w2e", description="Binance Square Write to Earn automation")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="one scheduler tick (call this from cron)")
    r.add_argument("--force", action="store_true", help="ignore schedule gates")
    r.add_argument("--mode", choices=["auto", "review", "dry-run"], help="override config mode")
    r.add_argument("--format", choices=FORMATS)
    r.add_argument("--coin", help="force a coin for coin_focus, e.g. SOL")
    r.set_defaults(fn=cmd_run)

    sub.add_parser("queue", help="list drafts waiting for review").set_defaults(fn=cmd_queue)
    a = sub.add_parser("approve", help="publish a queued draft")
    a.add_argument("id")
    a.add_argument("--text-file", help="publish your edited version of the text instead")
    a.set_defaults(fn=cmd_approve)
    rj = sub.add_parser("reject", help="delete a queued draft")
    rj.add_argument("id")
    rj.set_defaults(fn=cmd_reject)
    sub.add_parser("stats", help="posting stats").set_defaults(fn=cmd_stats)
    sub.add_parser("save-key", help="store BINANCE_SQUARE_OPENAPI_KEY in ~/.config").set_defaults(fn=cmd_save_key)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config_path = Path(args.config)
    _load_dotenv(config_path.resolve().parent / ".env")
    settings = load_settings(config_path)
    try:
        return args.fn(args, settings)
    except (RuntimeError, KeyError) as e:
        if args.verbose:
            raise
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
