"""Decide what to write next: format, coin, and the data brief the writer gets."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import timedelta

from .config import Settings
from .market import CoinDetail, MarketClient, MarketSnapshot
from .news import Headline
from .store import HistoryEntry, Store, utcnow

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
             force_coin: str | None = None) -> Brief | None:
        """The brief for the next post, or None when nothing is worth posting right now."""
        fmt = force_format or self.choose_format(headlines)
        if fmt == "weekly_article":
            return self._weekly(snap)
        # Follow-ups on our own earlier calls get more views than fresh posts on quiet coins.
        if fmt == "follow_up" or (not force_format and self.store.last_format() != "follow_up"):
            fu = self.followup_candidate(snap)
            if fu:
                return self._follow_up(snap, *fu)
            if fmt == "follow_up":
                fmt = "coin_focus"
        if fmt == "market_pulse":
            if force_format or self._strongest_move(snap) >= self.s.content.min_move_pct:
                market = snap.brief(self.s.content.min_quote_volume_usdt)
                return Brief("market_pulse", [], {"market": market},
                             movers=market if self.s.content.charts else None)
            fmt = "coin_focus"
        if fmt == "news_take":
            brief = self._news(snap, headlines)
            if brief:
                return brief
            fmt = "coin_focus"
        if fmt == "explainer":
            return self._explainer(snap)
        if force_coin:
            return self._coin_focus(snap, force_coin)
        coin = self.pick_coin(snap)
        if coin:
            return self._coin_focus(snap, coin)
        # Quiet market: a fresh headline is still worth a post; filler on a flat coin is not.
        return self._news(snap, headlines) if headlines else None

    def _strongest_move(self, snap: MarketSnapshot) -> float:
        liquid = snap.liquid(self.s.content.min_quote_volume_usdt)
        return max((abs(t.change_pct) for t in liquid), default=0.0)

    def followup_candidate(self, snap: MarketSnapshot) -> tuple[str, HistoryEntry, float] | None:
        """The coin of an earlier post that has moved most since that post, if it moved enough."""
        c = self.s.content
        if c.follow_up_move_pct <= 0:
            return None
        now = utcnow()
        latest: dict[str, HistoryEntry] = {}
        for h in self.store.published_since(now - timedelta(hours=48)):
            if h.coins:
                latest[h.coins[0]] = h
        best = None
        for coin, h in latest.items():
            ref, t = h.ref or {}, snap.tickers.get(coin)
            if (h.format == "follow_up" or not t or ref.get("coin") != coin or not ref.get("price")
                    or t.quote_volume < c.min_quote_volume_usdt):
                continue
            if (now - h.published_dt).total_seconds() / 3600 < c.follow_up_min_hours:
                continue
            move = (t.price / ref["price"] - 1) * 100
            if abs(move) >= c.follow_up_move_pct and (best is None or abs(move) > abs(best[2])):
                best = (coin, h, move)
        return best

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

    def pick_coin(self, snap: MarketSnapshot) -> str | None:
        """The fresh coin with the biggest 24h move, or None if none moved at least min_move_pct."""
        c = self.s.content
        cooldown = self.store.recent_coins(c.coin_cooldown_hours)
        min_vol = c.min_quote_volume_usdt
        candidates = {b for b in c.watchlist if b in snap.tickers}
        candidates |= {t.base for t in snap.gainers(min_vol, 8) + snap.losers(min_vol, 8)}
        candidates = {b for b in candidates if b not in cooldown and snap.tickers[b].quote_volume >= min_vol
                      and abs(snap.tickers[b].change_pct) >= c.min_move_pct}
        if not candidates:
            if c.min_move_pct > 0:
                return None
            candidates = {b for b in ("BTC", "ETH") if b in snap.tickers}
        # Big moves are what readers open and trade; take the biggest.
        return max(candidates, key=lambda b: abs(snap.tickers[b].change_pct))

    def _follow_up(self, snap: MarketSnapshot, coin: str, prev: HistoryEntry, move: float) -> Brief:
        brief = self._coin_focus(snap, coin)
        ref = prev.ref or {}
        brief.format = "follow_up"
        brief.data["my_previous_post"] = {
            "hours_ago": round((utcnow() - prev.published_dt).total_seconds() / 3600, 1),
            "price_then": ref.get("price"),
            "change_24h_pct_then": ref.get("change_24h_pct"),
            "volume_24h_usdt_then": ref.get("volume_24h_usdt"),
            "price_move_since_post_pct": round(move, 2),
            "what_i_wrote": prev.text,
        }
        return brief

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
        # BTC headlines are the most crowded topic on Square: use them only if nothing else fits.
        headlines = sorted(headlines, key=lambda h: coins_in_headline(h, tradable)[:1] == ["BTC"])
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
