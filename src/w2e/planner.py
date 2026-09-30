"""Decide what to write next: format, coin, and the data brief the writer gets."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import timedelta

from .config import Settings
from .market import CoinDetail, MarketClient, MarketSnapshot
from .news import Headline
from .store import Store, utcnow

EXPLAINER_TOPICS = [
    "what funding rates tell you about crowd positioning",
    "why volume confirms (or kills) a breakout",
    "how to read RSI without getting trapped by it",
    "the Fear & Greed index: when it is useful and when it lies",
    "why the 7-day high and low matter as reference levels",
    "position sizing: why risk per trade matters more than the entry",
    "how Bitcoin moves drag altcoins, and when they decouple",
    "what a volume spike without price movement usually means",
]

# Common names in headlines -> tickers.
COIN_NAMES = {
    "bitcoin": "BTC", "ethereum": "ETH", "ether": "ETH", "solana": "SOL", "ripple": "XRP", "xrp": "XRP",
    "dogecoin": "DOGE", "cardano": "ADA", "bnb": "BNB", "binance coin": "BNB", "toncoin": "TON",
    "avalanche": "AVAX", "chainlink": "LINK", "sui": "SUI", "tron": "TRX", "polkadot": "DOT",
    "litecoin": "LTC", "shiba": "SHIB", "pepe": "PEPE", "arbitrum": "ARB", "optimism": "OP",
    "aptos": "APT", "near": "NEAR", "uniswap": "UNI", "aave": "AAVE", "hyperliquid": "HYPE",
}


@dataclass
class Brief:
    format: str
    coins: list[str]
    data: dict
    chart: CoinDetail | None = None
    # Market snapshot brief to draw as a 24h movers chart when there is no single-coin chart.
    movers: dict | None = None
    article: bool = False
    notes: list[str] = field(default_factory=list)


def coins_in_headline(h: Headline, tradable: set[str]) -> list[str]:
    text = f"{h.title} {h.summary}"
    found: list[str] = []
    lower = text.lower()
    for name, ticker in COIN_NAMES.items():
        if re.search(rf"\b{re.escape(name)}\b", lower) and ticker in tradable and ticker not in found:
            found.append(ticker)
    for tag in re.findall(r"\b[A-Z]{2,6}\b", text):
        if tag in tradable and tag not in found and tag not in {"CEO", "SEC", "ETF", "USD", "US", "AI"}:
            found.append(tag)
    return found


class Planner:
    def __init__(self, settings: Settings, store: Store, market: MarketClient, rng: random.Random | None = None):
        self.s = settings
        self.store = store
        self.market = market
        self.rng = rng or random.Random()

    def plan(self, snap: MarketSnapshot, headlines: list[Headline], force_format: str | None = None,
             force_coin: str | None = None) -> Brief:
        fmt = force_format or self.choose_format(headlines)
        if fmt == "weekly_article":
            return self._weekly(snap)
        if fmt == "market_pulse":
            market = snap.brief(self.s.content.min_quote_volume_usdt)
            return Brief("market_pulse", [], {"market": market},
                         movers=market if self.s.content.charts else None)
        if fmt == "news_take":
            brief = self._news(snap, headlines)
            if brief:
                return brief
            fmt = "coin_focus"
        if fmt == "explainer":
            return self._explainer(snap)
        return self._coin_focus(snap, force_coin)

    def choose_format(self, headlines: list[Headline]) -> str:
        wa = self.s.content.weekly_article
        now = utcnow()
        if wa.enabled and now.weekday() == wa.weekday:
            week_ago = now - timedelta(days=6)
            if not any(h.format == "weekly_article" for h in self.store.published_since(week_ago)):
                return "weekly_article"

        weights = dict(self.s.content.formats)
        last = self.store.last_format()
        if last in weights and len([w for w in weights.values() if w > 0]) > 1:
            weights[last] = 0
        if not headlines:
            weights["news_take"] = 0
        options = [(k, w) for k, w in weights.items() if w > 0]
        if not options:
            return "coin_focus"
        return self.rng.choices([k for k, _ in options], weights=[w for _, w in options])[0]

    def pick_coin(self, snap: MarketSnapshot) -> str:
        cooldown = self.store.recent_coins(self.s.content.coin_cooldown_hours)
        min_vol = self.s.content.min_quote_volume_usdt
        candidates = {b for b in self.s.content.watchlist if b in snap.tickers}
        candidates |= {t.base for t in snap.gainers(min_vol, 5) + snap.losers(min_vol, 5)}
        candidates = {c for c in candidates if c not in cooldown and snap.tickers[c].quote_volume >= min_vol}
        if not candidates:
            candidates = {b for b in ("BTC", "ETH") if b in snap.tickers}
        # Most interesting first: biggest absolute move today.
        ranked = sorted(candidates, key=lambda b: abs(snap.tickers[b].change_pct), reverse=True)
        return self.rng.choice(ranked[:3])

    def _coin_focus(self, snap: MarketSnapshot, coin: str | None) -> Brief:
        coin = coin or self.pick_coin(snap)
        detail = self.market.coin_detail(snap, coin)
        btc = snap.tickers.get("BTC")
        data = {"coin": detail.brief(),
                "btc_24h_change_pct": round(btc.change_pct, 2) if btc else None,
                "fear_greed": snap.fear_greed}
        return Brief("coin_focus", [coin], data, chart=detail if self.s.content.charts else None)

    def _news(self, snap: MarketSnapshot, headlines: list[Headline]) -> Brief | None:
        tradable = snap.tradable_bases()
        used = " ".join(self.store.recent_texts(30)).lower()
        cooldown = self.store.recent_main_coins(self.s.content.coin_cooldown_hours)
        for h in headlines:
            coins = coins_in_headline(h, tradable)
            if not coins or h.title.lower()[:60] in used or coins[0] in cooldown:
                continue
            detail = self.market.coin_detail(snap, coins[0])
            data = {"headline": h.brief(), "coin": detail.brief(),
                    "market": {"fear_greed": snap.fear_greed,
                               "btc_24h_change_pct": round(snap.tickers["BTC"].change_pct, 2)
                               if "BTC" in snap.tickers else None}}
            return Brief("news_take", coins[:2], data, chart=detail if self.s.content.charts else None)
        return None

    def _explainer(self, snap: MarketSnapshot) -> Brief:
        recent = " ".join(self.store.recent_texts(20)).lower()
        topics = [t for t in EXPLAINER_TOPICS if t.split(":")[0][:25].lower() not in recent] or EXPLAINER_TOPICS
        topic = self.rng.choice(topics)
        # Rotate the worked example so the feed is not BTC every time.
        cooldown = self.store.recent_main_coins(self.s.content.coin_cooldown_hours)
        majors = [b for b in ("BTC", "ETH", "SOL", "BNB", "XRP") if b in snap.tickers]
        fresh = [b for b in majors if b not in cooldown]
        coin = (fresh or majors or list(snap.tickers))[0]
        detail = self.market.coin_detail(snap, coin)
        data = {"topic": topic, "live_example": detail.brief(),
                "market": snap.brief(self.s.content.min_quote_volume_usdt)}
        return Brief("explainer", [coin], data, chart=detail if self.s.content.charts else None)

    def _weekly(self, snap: MarketSnapshot) -> Brief:
        min_vol = self.s.content.min_quote_volume_usdt
        details = [self.market.coin_detail(snap, b) for b in ("BTC", "ETH") if b in snap.tickers]
        data = {"market": snap.brief(min_vol), "weekly": [d.brief() for d in details]}
        return Brief("weekly_article", [d.base for d in details], data,
                     chart=details[0] if details and self.s.content.charts else None, article=True)
