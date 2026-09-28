"""Public Binance market data. No API key needed.

Every number that ends up in a post comes from here, so the writer model
cannot invent prices. Spot data uses the market-data-only host first
(data-api.binance.vision), which works from more regions than api.binance.com.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

import httpx

log = logging.getLogger(__name__)

SPOT_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com", "https://api1.binance.com"]
FUTURES_HOST = "https://fapi.binance.com"
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=2"

STABLE_BASES = {
    "USDC", "FDUSD", "TUSD", "DAI", "USDP", "EUR", "AEUR", "USDE", "PYUSD",
    "BFUSD", "RLUSD", "XUSD", "USD1", "EURI", "USDS", "BUSD", "UST", "PAXG",
}
LEVERAGED_SUFFIXES = ("UP", "DOWN", "BULL", "BEAR")


@dataclass
class Ticker:
    base: str
    symbol: str
    price: float
    change_pct: float
    high: float
    low: float
    quote_volume: float


@dataclass
class CoinDetail:
    base: str
    price: float
    change_24h_pct: float
    change_7d_pct: float
    high_7d: float
    low_7d: float
    pct_from_7d_high: float
    volume_24h_usdt: float
    volume_ratio_vs_7d_avg: float
    rsi_14h: float | None
    funding_rate_pct: float | None
    closes_1h: list[float] = field(default_factory=list, repr=False)
    times_1h: list[int] = field(default_factory=list, repr=False)

    def brief(self) -> dict:
        d = asdict(self)
        d.pop("closes_1h")
        d.pop("times_1h")
        return {k: _round(v) for k, v in d.items()}


@dataclass
class MarketSnapshot:
    tickers: dict[str, Ticker]
    fear_greed: dict | None
    funding: dict[str, float]

    def liquid(self, min_quote_volume: float) -> list[Ticker]:
        return [t for t in self.tickers.values() if t.quote_volume >= min_quote_volume]

    def gainers(self, min_quote_volume: float, n: int = 5) -> list[Ticker]:
        return sorted(self.liquid(min_quote_volume), key=lambda t: t.change_pct, reverse=True)[:n]

    def losers(self, min_quote_volume: float, n: int = 5) -> list[Ticker]:
        return sorted(self.liquid(min_quote_volume), key=lambda t: t.change_pct)[:n]

    def top_volume(self, n: int = 5) -> list[Ticker]:
        return sorted(self.tickers.values(), key=lambda t: t.quote_volume, reverse=True)[:n]

    def tradable_bases(self) -> set[str]:
        return set(self.tickers)

    def brief(self, min_quote_volume: float) -> dict:
        def row(t: Ticker) -> dict:
            return {"coin": t.base, "price": _round(t.price), "change_24h_pct": _round(t.change_pct),
                    "volume_24h_usdt_m": _round(t.quote_volume / 1e6)}

        majors = [self.tickers[b] for b in ("BTC", "ETH", "BNB", "SOL") if b in self.tickers]
        return {
            "majors": [row(t) for t in majors],
            "top_gainers": [row(t) for t in self.gainers(min_quote_volume)],
            "top_losers": [row(t) for t in self.losers(min_quote_volume)],
            "top_volume": [row(t) for t in self.top_volume()],
            "fear_greed": self.fear_greed,
            "btc_funding_rate_pct": _round(self.funding.get("BTC")),
            "eth_funding_rate_pct": _round(self.funding.get("ETH")),
        }


def _round(v):
    if isinstance(v, float):
        if abs(v) >= 100:
            return round(v, 2)
        if abs(v) >= 1:
            return round(v, 3)
        return float(f"{v:.4g}")
    return v


def _is_tradable_base(base: str) -> bool:
    if base in STABLE_BASES:
        return False
    return not (base.endswith(LEVERAGED_SUFFIXES) and len(base) > 4)


class MarketClient:
    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=20, headers={"User-Agent": "w2e/0.1"})
        self._spot_host: str | None = None

    def _spot_get(self, path: str, params: dict | None = None):
        hosts = [self._spot_host] if self._spot_host else SPOT_HOSTS
        last_error: Exception | None = None
        for host in hosts:
            try:
                r = self.http.get(f"{host}{path}", params=params)
                r.raise_for_status()
                self._spot_host = host
                return r.json()
            except httpx.HTTPError as e:
                log.warning("spot host %s failed: %s", host, e)
                last_error = e
        raise RuntimeError(f"All Binance spot hosts failed for {path}: {last_error}")

    def snapshot(self) -> MarketSnapshot:
        tickers: dict[str, Ticker] = {}
        for t in self._spot_get("/api/v3/ticker/24hr"):
            sym = t.get("symbol", "")
            if not sym.endswith("USDT") or int(t.get("count", 0)) == 0:
                continue
            base = sym[: -len("USDT")]
            if not _is_tradable_base(base):
                continue
            tickers[base] = Ticker(
                base=base,
                symbol=sym,
                price=float(t["lastPrice"]),
                change_pct=float(t["priceChangePercent"]),
                high=float(t["highPrice"]),
                low=float(t["lowPrice"]),
                quote_volume=float(t["quoteVolume"]),
            )
        return MarketSnapshot(tickers=tickers, fear_greed=self.fear_greed(), funding=self.funding_rates())

    def funding_rates(self) -> dict[str, float]:
        """Last funding rate per base coin, in percent. Empty if futures API is unreachable."""
        try:
            r = self.http.get(f"{FUTURES_HOST}/fapi/v1/premiumIndex")
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("futures funding unavailable: %s", e)
            return {}
        out = {}
        for row in r.json():
            sym = row.get("symbol", "")
            if sym.endswith("USDT") and row.get("lastFundingRate") not in (None, ""):
                out[sym[: -len("USDT")]] = float(row["lastFundingRate"]) * 100
        return out

    def fear_greed(self) -> dict | None:
        try:
            r = self.http.get(FEAR_GREED_URL)
            r.raise_for_status()
            data = r.json()["data"]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            log.warning("fear & greed unavailable: %s", e)
            return None
        today = data[0]
        out = {"value": int(today["value"]), "label": today["value_classification"]}
        if len(data) > 1:
            out["yesterday"] = int(data[1]["value"])
        return out

    def coin_detail(self, snap: MarketSnapshot, base: str) -> CoinDetail:
        t = snap.tickers[base]
        klines = self._spot_get("/api/v3/klines", {"symbol": t.symbol, "interval": "1h", "limit": 168})
        times = [int(k[0]) for k in klines]
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        quote_vols = [float(k[7]) for k in klines]

        high_7d, low_7d = max(highs), min(lows)
        change_7d = (closes[-1] / closes[0] - 1) * 100 if closes and closes[0] else 0.0
        last_24 = sum(quote_vols[-24:])
        prior = quote_vols[:-24]
        prior_daily_avg = (sum(prior) / len(prior) * 24) if prior else 0.0
        vol_ratio = last_24 / prior_daily_avg if prior_daily_avg else 1.0

        return CoinDetail(
            base=base,
            price=t.price,
            change_24h_pct=t.change_pct,
            change_7d_pct=change_7d,
            high_7d=high_7d,
            low_7d=low_7d,
            pct_from_7d_high=(t.price / high_7d - 1) * 100 if high_7d else 0.0,
            volume_24h_usdt=t.quote_volume,
            volume_ratio_vs_7d_avg=vol_ratio,
            rsi_14h=rsi(closes, 14),
            funding_rate_pct=snap.funding.get(base),
            closes_1h=closes,
            times_1h=times,
        )


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI on the given closes."""
    if len(closes) <= period:
        return None
    gains, losses = [], []
    for a, b in zip(closes, closes[1:]):
        diff = b - a
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)
