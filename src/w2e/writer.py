"""Claude writes the draft from the data brief, then a second call reviews it.

The writer only sees numbers from Binance's own API, and the reviewer checks
every figure in the draft against that same data, so hallucinated prices are
caught before publishing.
"""

from __future__ import annotations

import json
import logging

import anthropic
from pydantic import BaseModel, Field

from .config import Settings
from .planner import Brief

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class PostDraft(BaseModel):
    title: str = Field(description="Article title. Empty string for short posts.")
    text: str = Field(description="The full post body, exactly as it will be published.")


class Review(BaseModel):
    score: int = Field(description="Overall quality 1-10.")
    factual_errors: list[str] = Field(description="Every number or claim not supported by DATA. Empty if none.")
    issues: list[str] = Field(description="Concrete, actionable fixes. Empty if none.")


FORMAT_GUIDE = {
    "market_pulse": (
        "A short market read of the last 24h. Pick the ONE thing that matters most today (a big mover, "
        "a volume anomaly, sentiment shift, funding extreme) and build the post around it. Mention at most "
        "3 coins. Do not just list gainers and losers; say what it means and what you are watching next."
    ),
    "coin_focus": (
        "A short analysis of one coin. Use its 24h and 7d change, distance from the 7d high, volume versus "
        "its 7d average, RSI and funding when present. Give a clear view, the exact level that would "
        "change your mind, and what you are waiting for before acting."
    ),
    "news_take": (
        "Your opinion on the headline in DATA. Summarise the news in one sentence in your own words (name "
        "the source outlet), then give your take and connect it to the coin's actual market data. "
        "Never copy the headline or summary wording."
    ),
    "explainer": (
        "Teach the topic in DATA to a beginner in plain words, using the live numbers as the worked example. "
        "End with one practical takeaway."
    ),
    "weekly_article": (
        "A long-form weekly market article. Use a specific, non-clickbait title. Structure: a 2-3 sentence "
        "summary of the week, then sections for BTC, ETH, the notable movers, sentiment and funding, and "
        "'What I am watching next week' with exact levels. Short paragraphs. Plain text section headers on "
        "their own line (no markdown symbols)."
    ),
}


def system_prompt(settings: Settings) -> str:
    q = settings.quality
    return f"""You write posts for Binance Square, published under the author's own account.
Write in this language: {settings.language}.

AUTHOR PERSONA (write as this person, in the first person):
{settings.persona() or "An experienced, calm crypto trader who writes plainly."}

HARD RULES
1. Use only facts and numbers that appear in DATA. Never invent prices, events, dates, partnerships or
   statistics. Round sensibly (e.g. 64,210 not 64210.3871). If DATA lacks something, do not mention it.
2. Write coin tickers as cashtags inline in the sentence, like $BTC or $SOL, using only coins present in
   DATA. Use 1 to {q.max_cashtags} different cashtags. Readers tap them to trade, so place the main one early.
3. Give a real opinion with a reason. Say what would prove you wrong. Never promise profit, never tell
   readers to buy or sell, no price targets presented as certain.
4. No links, no referral codes, no "follow me", no requests for likes or comments.
5. End with 1 or 2 hashtags on their own last line, specific to this post: the project or the event
   (e.g. #Aave, #BitcoinETF, #FearAndGreed). No generic tags (#crypto, #trading, #bitcoin, #BinanceSquare)
   and not the same hashtags as the author's previous post. At most 2 emojis in the whole post. Zero is fine.
6. Sound like a person, not a content farm: vary sentence length, no filler intro, start with the point.
   Never use: "let's dive in", "delve", "in the ever-evolving", "buckle up", "game-changer",
   "it's important to note", "in conclusion", "stay tuned", "not financial advice".
   Do not use em dashes. Do not use markdown (no **, no #, no bullet symbols); use line breaks.
7. Length: short posts {q.short_post_chars[0]}-{q.short_post_chars[1]} characters; articles
   {q.article_chars[0]}-{q.article_chars[1]} characters.
8. Do not reuse the openings or structure of the author's recent posts listed in the request."""


REVIEW_SYSTEM = """You are a strict editor for a Binance Square creator. You check drafts before publishing.

Check:
1. Every number, price, percentage and factual claim against DATA. Tolerate normal rounding only.
   Anything not supported by DATA goes in factual_errors.
2. Does it offer a real, specific insight, or is it generic filler anyone could write?
3. Does it read like a human trader wrote it, or like templated AI text?
4. Compliance: no profit promises, no buy/sell instructions, no links, no engagement bait.

Score 1-10 where 7 means "good enough to publish under a real person's name", 9+ means genuinely
useful and distinctive. Any factual error caps the score at 4. Be specific in issues."""


class Writer:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None):
        self.s = settings
        self.client = client or anthropic.Anthropic()

    def _parse(self, system: str, user: str, schema: type[BaseModel], max_tokens: int):
        response = self.client.beta.messages.parse(
            model=self.s.llm.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
            output_config={"effort": self.s.llm.effort},
            output_format=schema,
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"Model declined the request: {response.stop_details}")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise RuntimeError(f"No structured output (stop_reason={response.stop_reason})")
        return response.parsed_output

    def write(self, brief: Brief, recent_texts: list[str], feedback: list[str] | None = None) -> PostDraft:
        kind = "long-form ARTICLE (needs a title)" if brief.article else "SHORT POST (title must be empty)"
        recent = "\n".join(f"- {t[:160]!r}" for t in recent_texts[-6:]) or "(none yet)"
        parts = [
            f"Write one {kind}. Format: {brief.format}.",
            f"FORMAT GUIDE: {FORMAT_GUIDE[brief.format]}",
            f"DATA (the only source of facts):\n{json.dumps(brief.data, ensure_ascii=False, indent=1)}",
            f"AUTHOR'S RECENT POSTS (do not repeat their openings, angles or structure):\n{recent}",
        ]
        if brief.chart is not None:
            parts.append("A 7-day price chart of the main coin is attached to the post; you may refer to it.")
        elif getattr(brief, "movers", None):
            parts.append("A 24h movers bar chart (gainers, majors, losers) is attached; you may refer to it.")
        if feedback:
            parts.append("YOUR PREVIOUS DRAFT WAS REJECTED. Fix all of these:\n" + "\n".join(f"- {f}" for f in feedback))
        draft = self._parse(system_prompt(self.s), "\n\n".join(parts), PostDraft, 16000)
        if not brief.article:
            draft.title = ""
        return draft

    def review(self, brief: Brief, draft: PostDraft) -> Review:
        user = (f"DATA:\n{json.dumps(brief.data, ensure_ascii=False, indent=1)}\n\n"
                f"DRAFT TITLE: {draft.title or '(short post, no title)'}\n\nDRAFT:\n{draft.text}")
        review = self._parse(REVIEW_SYSTEM, user, Review, 8000)
        if review.factual_errors:
            review.score = min(review.score, 4)
        return review
