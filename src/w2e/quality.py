"""Deterministic checks every draft must pass before it can be published.

These catch the things Binance explicitly penalises (spam, repetition,
templated text, promises of profit) without spending an LLM call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import QualitySettings

# Tickers may start with a digit ($0G, $1INCH) but must contain a letter ($100 is money).
# A "$" inside a hashtag (#BitcoinRejectedAt$87K) is part of the tag, not a cashtag.
CASHTAG_RE = re.compile(r"(?<![\w$])\$((?=[A-Z0-9]*[A-Z])[A-Z0-9]{2,15})\b")
URL_RE = re.compile(r"https?://|www\.", re.I)

# Hype and promise language: risk of "misleading" moderation and of losing eligibility.
BANNED_PHRASES = [
    "guaranteed", "guarantee profit", "100x", "1000x", "to the moon", "can't lose", "cannot lose",
    "risk-free", "risk free", "free money", "don't miss", "dont miss", "last chance", "get rich",
    "financial freedom", "send it", "referral", "ref code", "promo code", "sign up with my",
    "follow for follow", "like and share", "comment below to", "red packet",
]

# Phrases that make a text read as generic AI output.
AI_TELLS = [
    "let's dive in", "let's dive into", "dive into", "in the ever-evolving", "ever-changing landscape",
    "in today's fast-paced", "buckle up", "game-changer", "game changer", "navigating the",
    "in conclusion", "it's important to note", "it is important to note", "stay tuned",
    "delve", "tapestry", "unlock the potential", "a testament to", "in the world of crypto",
    "as an ai", "remember, always do your own research",
    # Disclaimers and template labels (Oct 6): they make every post look machine-made.
    "not financial advice", "nfa", "dyor", "my take:", "my view:", "what would change my view:",
    "what changes my mind:", "what i am waiting for:", "whole story", "here's the thing",
    "the real question", "underscores", "serves as",
]


# Tags on nearly every crypto post: no discovery value, and they make posts look templated.
GENERIC_HASHTAGS = {
    "crypto", "cryptocurrency", "trading", "bitcoin", "btc", "binance", "binancesquare", "altcoins",
    "altcoin", "cryptonews", "blockchain", "investing", "web3", "defi",
}


def hashtag_set(text: str) -> set[str]:
    return {h.lower() for h in re.findall(r"(?<![\w&])#([A-Za-z][\w]*)", text)}


@dataclass
class CheckResult:
    ok: bool
    problems: list[str] = field(default_factory=list)


def cashtags(text: str) -> list[str]:
    seen: list[str] = []
    for tag in CASHTAG_RE.findall(text):
        if tag not in seen:
            seen.append(tag)
    return seen


def _shingles(text: str, n: int = 3) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9$%.]+", text.lower())
    return {tuple(words[i:i + n]) for i in range(max(len(words) - n + 1, 1))}


def similarity(a: str, b: str) -> float:
    """Jaccard similarity of word 3-grams. 1.0 = same text."""
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def max_similarity(text: str, previous: list[str]) -> float:
    return max((similarity(text, p) for p in previous), default=0.0)


def check_draft(text: str, title: str | None, settings: QualitySettings, tradable: set[str],
                previous_texts: list[str]) -> CheckResult:
    problems: list[str] = []
    lower = text.lower()

    lo, hi = settings.article_chars if title else settings.short_post_chars
    if not lo <= len(text) <= hi:
        problems.append(f"length {len(text)} chars, expected {lo}-{hi}")

    tags = cashtags(text)
    if not tags:
        problems.append("no $CASHTAG: readers need one to open the trade widget (that is where commission comes from)")
    if len(tags) > settings.max_cashtags:
        problems.append(f"{len(tags)} cashtags, max {settings.max_cashtags} (more reads as spam)")
    unknown = [t for t in tags if t not in tradable]
    if unknown:
        problems.append(f"cashtags not tradable on Binance spot/USDT: {', '.join(unknown)}")

    for phrase in BANNED_PHRASES:
        if phrase in lower:
            problems.append(f"banned phrase: '{phrase}'")
    for phrase in AI_TELLS:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", lower):
            problems.append(f"generic AI phrase: '{phrase}'")

    if URL_RE.search(text):
        problems.append("contains a link (external links hurt reach and can look like spam)")
    if text.count("—") > 1:
        problems.append("more than one em dash (a common AI-writing tell)")
    hashtags = hashtag_set(text)
    if len(hashtags) > settings.max_hashtags:
        problems.append(f"{len(hashtags)} hashtags, max {settings.max_hashtags} (more reads as spam)")
    if hashtags:
        last_line = text.strip().splitlines()[-1]
        if hashtag_set(last_line) != hashtags:
            problems.append("hashtags must all be on the last line, after the text")
        generic = sorted(hashtags & GENERIC_HASHTAGS)
        if generic:
            problems.append(f"generic hashtags add nothing: {', '.join('#' + g for g in generic)}")
        if previous_texts and hashtags == hashtag_set(previous_texts[-1]):
            problems.append("same hashtags as the previous post (repeated tags look templated)")
    emoji_count = len(re.findall(r"[\U0001F300-\U0001FAFF☀-➿]", text))
    if emoji_count > 3:
        problems.append(f"{emoji_count} emojis, max 3")
    if re.search(r"(.)\1{4,}", text):
        problems.append("repeated characters")

    sim = max_similarity(text, previous_texts)
    if sim > settings.max_similarity:
        problems.append(f"too similar to a previous post (similarity {sim:.2f})")

    return CheckResult(ok=not problems, problems=problems)
