from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from w2e.market import rsi
from w2e.news import parse_feed


def test_snapshot_filters_stables_leveraged_and_dead(market):
    snap = market.snapshot()
    assert {"BTC", "ETH", "SOL", "PEPE"} <= set(snap.tickers)
    assert not {"USDC", "BTCUP", "DEAD"} & set(snap.tickers)
    assert snap.funding["SOL"] == 0.05
    assert snap.fear_greed == {"value": 62, "label": "Greed", "yesterday": 55}
    assert snap.gainers(2e7, 1)[0].base == "PEPE"  # TINY is filtered by volume


def test_coin_detail(market):
    snap = market.snapshot()
    d = market.coin_detail(snap, "SOL")
    assert d.low_7d <= min(d.closes_1h) and d.high_7d >= max(d.closes_1h)
    assert d.funding_rate_pct == 0.05 and 0 <= d.rsi_14h <= 100
    assert "closes_1h" not in d.brief()


def test_rsi_extremes():
    assert rsi(list(range(1, 40))) == 100.0
    assert rsi(list(range(40, 1, -1))) < 5
    assert rsi([1, 2, 3]) is None


def test_parse_rss():
    now = datetime.now(timezone.utc)
    xml = f"""<rss><channel>
      <item><title>Bitcoin ETF inflows jump</title><link>https://a/1</link>
        <description>&lt;p&gt;Big &amp;amp; fast&lt;/p&gt;</description><pubDate>{format_datetime(now)}</pubDate></item>
      <item><title>Old</title><pubDate>{format_datetime(now - timedelta(days=3))}</pubDate></item>
    </channel></rss>"""
    items = parse_feed(xml, "example.com")
    assert items[0].title == "Bitcoin ETF inflows jump" and items[0].summary == "Big & fast"
    assert len(items) == 2
