import random
import time

import httpx
import pytest
import respx

from w2e.config import Settings
from w2e.market import FEAR_GREED_URL, FUTURES_HOST, SPOT_HOSTS, MarketClient

SPOT = SPOT_HOSTS[0]


def _ticker(sym, price, chg, qv, count=1000):
    return {"symbol": sym, "lastPrice": str(price), "priceChangePercent": str(chg), "highPrice": str(price * 1.05),
            "lowPrice": str(price * 0.95), "quoteVolume": str(qv), "count": count}


TICKERS = [
    _ticker("BTCUSDT", 64000, 1.2, 2e9),
    _ticker("ETHUSDT", 3100, -0.8, 1e9),
    _ticker("SOLUSDT", 150, 6.5, 4e8),
    _ticker("BNBUSDT", 580, 0.3, 3e8),
    _ticker("PEPEUSDT", 0.00001, 12.0, 1e8),
    _ticker("USDCUSDT", 1.0, 0.0, 5e9),
    _ticker("BTCUPUSDT", 10, 3, 5e7),
    _ticker("DEADUSDT", 1, 50, 1e9, count=0),
    _ticker("TINYUSDT", 1, 80, 1e5),
]


def klines(n=168, start=100.0):
    now = int(time.time() * 1000) - n * 3600_000
    out = []
    price = start
    rng = random.Random(1)
    for i in range(n):
        o = price
        price = max(1.0, price * (1 + rng.uniform(-0.01, 0.011)))
        out.append([now + i * 3600_000, str(o), str(max(o, price) * 1.002), str(min(o, price) * 0.998),
                    str(price), "10", now + (i + 1) * 3600_000 - 1, str(1e6 + i * 1e3), 100, "5", "5", "0"])
    return out


@pytest.fixture
def market_mock():
    with respx.mock(assert_all_called=False) as m:
        m.get(f"{SPOT}/api/v3/ticker/24hr").mock(return_value=httpx.Response(200, json=TICKERS))
        m.get(f"{SPOT}/api/v3/klines").mock(return_value=httpx.Response(200, json=klines()))
        m.get(f"{FUTURES_HOST}/fapi/v1/premiumIndex").mock(return_value=httpx.Response(
            200, json=[{"symbol": "BTCUSDT", "lastFundingRate": "0.0001"}, {"symbol": "SOLUSDT", "lastFundingRate": "0.0005"}]))
        m.get(FEAR_GREED_URL).mock(return_value=httpx.Response(
            200, json={"data": [{"value": "62", "value_classification": "Greed"}, {"value": "55", "value_classification": "Greed"}]}))
        yield m


@pytest.fixture
def market(market_mock):
    return MarketClient(httpx.Client())


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.root = tmp_path
    s.data_dir = str(tmp_path / "data")
    s.news.feeds = []
    s.content.formats = {"market_pulse": 1, "coin_focus": 1, "news_take": 0, "explainer": 1}
    s.content.weekly_article.enabled = False
    return s
