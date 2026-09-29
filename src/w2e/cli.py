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


def cmd_brief(args, settings) -> int:
    from .session import prepare

    res = prepare(settings, force=args.force, force_format=args.format, force_coin=args.coin,
                  random_skip=not args.no_random_skip)
    if res.status == "skipped":
        print(f"status: skipped  {res.detail}")
        return 0
    print(res.packet)
    return 0


def cmd_submit(args, settings) -> int:
    from .session import submit

    if args.mode:
        settings.mode = args.mode
    text = Path(args.text_file).read_text(encoding="utf-8")
    res = submit(settings, text, args.title)
    if res.ok:
        e = res.entry
        print(f"status: {e.status}  {e.link or e.post_id or ''}")
        return 0
    print("status: rejected")
    for p in res.problems:
        print(f"- {p}")
    return 1


def cmd_check(args, settings) -> int:
    """Connectivity and key check. Publishes nothing."""
    from .market import MarketClient
    from .news import fetch_headlines
    from .square import BASE_URL_V1, SquareError

    ok = True
    try:
        snap = MarketClient().snapshot()
        btc = snap.tickers.get("BTC")
        print(f"[ok]   Binance spot data: {len(snap.tickers)} USDT pairs, BTC {btc.price if btc else '?'}")
        print(f"[{'ok' if snap.funding else 'warn'}] futures funding rates: {len(snap.funding)} pairs")
        print(f"[{'ok' if snap.fear_greed else 'warn'}] fear & greed: {snap.fear_greed}")
    except Exception as e:  # noqa: BLE001 - report every failure kind
        ok = False
        print(f"[FAIL] Binance spot data: {e}")
    news = fetch_headlines(settings.news.feeds, 48)
    print(f"[{'ok' if news else 'warn'}] news headlines (48h): {len(news)}")
    try:
        key = resolve_api_key()
        # An empty post is rejected before publishing; the error code tells us if the key is valid.
        SquareClient(key)._api("/content/add", {"contentType": 1, "bodyTextOnly": ""}, BASE_URL_V1)
        print("[warn] Square accepted an empty post request; check your Square profile")
    except SquareError as e:
        if e.code in ("20020", "220011"):
            print(f"[ok]   Square key {mask_key(key)} is valid (empty test post rejected as expected)")
        else:
            ok = False
            print(f"[FAIL] Square: {e}")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"[FAIL] Square: {e}")
    return 0 if ok else 1


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

    b = sub.add_parser("brief", help="no-API mode: gather data and print a writing packet")
    b.add_argument("--force", action="store_true", help="ignore schedule gates")
    b.add_argument("--format", choices=FORMATS)
    b.add_argument("--coin")
    b.add_argument("--no-random-skip", action="store_true",
                   help="keep the other gates but never skip at random (for fixed-time schedulers)")
    b.set_defaults(fn=cmd_brief)
    sm = sub.add_parser("submit", help="no-API mode: check and publish a post written for the pending brief")
    sm.add_argument("--text-file", required=True)
    sm.add_argument("--title")
    sm.add_argument("--mode", choices=["auto", "dry-run"], help="override config mode")
    sm.set_defaults(fn=cmd_submit)

    sub.add_parser("check", help="test Binance data access and the Square key (publishes nothing)").set_defaults(fn=cmd_check)
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
