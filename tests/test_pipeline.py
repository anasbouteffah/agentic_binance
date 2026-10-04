import random
from datetime import timedelta
from types import SimpleNamespace

import pytest

from w2e.pipeline import run_tick, schedule_gate
from w2e.planner import Planner
from w2e.square import PublishResult
from w2e.store import HistoryEntry, Store, utcnow
from w2e.writer import PostDraft, Review, Writer

from test_quality import GOOD


class FakeWriter:
    def __init__(self, drafts, reviews):
        self.drafts, self.reviews, self.feedback = list(drafts), list(reviews), []

    def write(self, brief, recent, feedback=None):
        self.feedback.append(feedback)
        return self.drafts.pop(0)

    def review(self, brief, draft):
        return self.reviews.pop(0)


class FakeSquare:
    def __init__(self):
        self.calls = []

    def publish(self, text, title=None, image_path=None):
        self.calls.append((text, title, image_path))
        return PublishResult("42", "https://www.binance.com/square/post/42")


def _entry(minutes_ago, fmt="coin_focus", coins=("SOL",)):
    return HistoryEntry("d", fmt, list(coins), "t", None, "published", (utcnow() - timedelta(minutes=minutes_ago)).isoformat())


def test_gate(settings):
    store = Store(settings.data_path)
    rng = random.Random(0)
    settings.schedule.post_probability = 1.0
    settings.schedule.active_hours_utc = (0, 24)
    now = utcnow()
    assert schedule_gate(settings, store, now, rng) is None
    settings.schedule.active_hours_utc = ((now.hour + 1) % 24, (now.hour + 2) % 24 or 24)
    assert "active hours" in schedule_gate(settings, store, now, rng)
    settings.schedule.active_hours_utc = (0, 24)
    store.append(_entry(10))
    assert "min ago" in schedule_gate(settings, store, utcnow(), rng)
    settings.schedule.min_minutes_between_posts = 5
    settings.schedule.post_probability = 0.0
    assert "random skip" in schedule_gate(settings, store, utcnow(), rng)


def test_daily_cap(settings):
    store = Store(settings.data_path)
    settings.schedule.max_posts_per_day = 1
    settings.schedule.min_minutes_between_posts = 0
    settings.schedule.post_probability = 1.0
    settings.schedule.active_hours_utc = (0, 24)
    store.append(_entry(1))
    assert "daily cap" in schedule_gate(settings, store, utcnow(), random.Random())


def test_planner_avoids_repeat_format_and_cooldown_coin(settings, market):
    store = Store(settings.data_path)
    store.append(_entry(30, fmt="coin_focus", coins=("SOL", "PEPE")))
    planner = Planner(settings, store, market, random.Random(3))
    for _ in range(20):
        assert planner.choose_format([]) != "coin_focus"
    snap = market.snapshot()
    for _ in range(20):
        assert planner.pick_coin(snap) not in {"SOL", "PEPE"}


def test_end_to_end_auto_publish_with_chart(settings, market):
    settings.mode = "auto"
    writer = FakeWriter([PostDraft(title="", text=GOOD)], [Review(score=8, factual_errors=[], issues=[])])
    square = FakeSquare()
    res = run_tick(settings, force=True, force_format="coin_focus", force_coin="SOL", market=market,
                   writer=writer, square=square)
    assert res.status == "published", res.detail
    text, title, image = square.calls[0]
    assert text == GOOD and title is None and image.endswith(".png")
    hist = Store(settings.data_path).history()
    assert hist[-1].link.endswith("/42") and hist[-1].coins == ["SOL", "BTC"]


def test_rewrite_after_failed_checks_then_reject(settings, market):
    settings.mode = "dry-run"
    bad = PostDraft(title="", text="too short $SOL")
    writer = FakeWriter([bad, PostDraft(title="", text=GOOD)],
                        [Review(score=5, factual_errors=["price 150 not in data"], issues=[])])
    res = run_tick(settings, force=True, force_format="coin_focus", force_coin="SOL", market=market, writer=writer)
    assert res.status == "rejected"
    assert any("length" in f for f in writer.feedback[1])
    assert Store(settings.data_path).history() == []


def test_review_mode_queues(settings, market):
    settings.mode = "review"
    settings.content.charts = False
    writer = FakeWriter([PostDraft(title="", text=GOOD)], [Review(score=9, factual_errors=[], issues=[])])
    res = run_tick(settings, force=True, force_format="market_pulse", market=market, writer=writer)
    assert res.status == "queued"
    assert Store(settings.data_path).queued()[0].text == GOOD


def test_writer_request_shape_and_refusal(settings):
    calls = []

    def parse(**kw):
        calls.append(kw)
        return SimpleNamespace(stop_reason=kw.pop("_stop", "end_turn"), stop_details=None,
                               parsed_output=PostDraft(title="should be cleared", text="x $BTC"))

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(parse=parse)))
    w = Writer(settings, client)
    brief = SimpleNamespace(format="market_pulse", article=False, chart=None, data={"a": 1})
    draft = w.write(brief, [])
    assert draft.title == ""
    kw = calls[0]
    assert kw["model"] == settings.llm.model and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["thinking"] == {"type": "adaptive"}
    assert kw["output_format"] is PostDraft and "temperature" not in kw

    client.beta.messages.parse = lambda **kw: SimpleNamespace(stop_reason="refusal", stop_details="x", parsed_output=None)
    with pytest.raises(RuntimeError, match="declined"):
        w.write(brief, [])


def test_session_brief_then_submit(settings, market):
    from w2e.session import pending_path, prepare, submit

    settings.mode = "auto"
    res = prepare(settings, force=True, force_format="coin_focus", force_coin="SOL", market=market)
    assert res.status == "ready" and "DATA (the only source of facts)" in res.packet and '"base": "SOL"' in res.packet

    bad = submit(settings, "too short $SOL", square=FakeSquare())
    assert not bad.ok and any("length" in p for p in bad.problems)
    assert pending_path(settings).exists()

    square = FakeSquare()
    ok = submit(settings, GOOD, square=square)
    assert ok.ok and ok.entry.status == "published"
    assert square.calls[0][2].endswith(".png") and square.calls[0][1] is None
    assert not pending_path(settings).exists()
    assert not submit(settings, GOOD, square=square).ok  # brief consumed


@pytest.mark.parametrize("fmt", ["market_pulse", "explainer"])
def test_posts_without_single_coin_still_get_an_image(settings, market, fmt):
    settings.mode = "auto"
    writer = FakeWriter([PostDraft(title="", text=GOOD)], [Review(score=8, factual_errors=[], issues=[])])
    square = FakeSquare()
    res = run_tick(settings, force=True, force_format=fmt, market=market, writer=writer, square=square)
    assert res.status == "published", res.detail
    image = square.calls[0][2]
    assert image and image.endswith(".png")
    assert ("movers_" in image) == (fmt == "market_pulse")


def test_fmt_price_never_uses_scientific_notation():
    from w2e.charts import fmt_price

    assert fmt_price(87278.54) == "87,279"
    assert fmt_price(272.66) == "272.66"
    assert fmt_price(0.005904) == "0.005904"
    assert fmt_price(0.00000426) == "0.00000426"
    for v in (1e9, 12.3, 0.5, 3e-8):
        assert "e" not in fmt_price(v)


def test_explainer_rotates_away_from_recent_main_coin(settings, market):
    store = Store(settings.data_path)
    store.append(_entry(30, fmt="news_take", coins=("BTC",)))
    planner = Planner(settings, store, market, random.Random(1))
    brief = planner.plan(market.snapshot(), [], force_format="explainer")
    assert brief.coins[0] != "BTC"


def test_news_skips_headline_on_cooldown_coin(settings, market):
    from w2e.news import Headline

    store = Store(settings.data_path)
    store.append(_entry(30, fmt="coin_focus", coins=("BTC", "ETH")))
    h = Headline(title="Bitcoin ETF flows", summary="Bitcoin funds add inflows", source="x", link="", published=utcnow())
    planner = Planner(settings, store, market, random.Random(1))
    assert planner._news(market.snapshot(), [h]) is None


def test_news_uses_headline_when_coin_not_on_cooldown(settings, market):
    from w2e.news import Headline

    store = Store(settings.data_path)
    h = Headline(title="Bitcoin ETF flows", summary="Bitcoin funds add inflows", source="x", link="", published=utcnow())
    brief = Planner(settings, store, market, random.Random(1))._news(market.snapshot(), [h])
    assert brief is not None and brief.coins[0] == "BTC"


def test_news_prefers_non_btc_headline(settings, market):
    from w2e.news import Headline

    store = Store(settings.data_path)
    btc = Headline(title="Bitcoin ETF flows", summary="Bitcoin funds", source="x", link="", published=utcnow())
    sol = Headline(title="Solana upgrade ships", summary="Solana network", source="x", link="", published=utcnow())
    brief = Planner(settings, store, market, random.Random(1))._news(market.snapshot(), [btc, sol])
    assert brief is not None and brief.coins[0] == "SOL"


def test_trending_hashtags_skip_expired_and_comments(tmp_path):
    from w2e.session import trending_hashtags
    f = tmp_path / "trending.txt"
    f.write_text("# comment\n#Live 2026-10-04 macro posts\n#Old 2026-09-01 stale\nbad line\n", encoding="utf-8")
    assert trending_hashtags(f, today="2026-10-02") == ["#Live: macro posts"]
    assert trending_hashtags(tmp_path / "missing.txt") == []


def test_quiet_market_skips_and_biggest_mover_wins(settings, market):
    store = Store(settings.data_path)
    snap = market.snapshot()
    settings.content.min_move_pct = 8.0
    planner = Planner(settings, store, market, random.Random(1))
    assert planner.pick_coin(snap) == "PEPE"  # +12%, SOL +6.5% is below the bar
    store.append(_entry(30, coins=("PEPE",)))
    assert planner.pick_coin(snap) is None
    settings.content.formats = {"coin_focus": 1}
    assert planner.plan(snap, []) is None


def test_follow_up_when_coin_moved_since_post(settings, market):
    store = Store(settings.data_path)
    snap = market.snapshot()
    settings.content.follow_up_move_pct = 5.0
    price = snap.tickers["SOL"].price
    old = _entry(300, coins=("SOL",))
    old.ref = {"coin": "SOL", "price": price / 1.10, "change_24h_pct": 1.0, "volume_24h_usdt": 1e8}
    store.append(old)
    planner = Planner(settings, store, market, random.Random(1))
    brief = planner.plan(snap, [])
    assert brief.format == "follow_up" and brief.coins == ["SOL"]
    assert round(brief.data["my_previous_post"]["price_move_since_post_pct"]) == 10
    # Too recent, or moved too little: no follow-up.
    old.published_at = (utcnow() - timedelta(hours=1)).isoformat()
    store.history_file.unlink(); store.append(old)
    assert planner.followup_candidate(snap) is None
