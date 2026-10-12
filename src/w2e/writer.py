"""Claude writes the draft from the data brief, then a second call reviews it.

The writer only sees numbers from Binance's own API, and the reviewer checks
every figure in the draft against that same data, so hallucinated prices are
caught before publishing.
"""

from __future__ import annotations

import json
import logging
import os

import anthropic
import httpx
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
        "a volume anomaly, sentiment shift, funding extreme) and build the post around it. Prefer a coin with "
        "a standout move over BTC/ETH unless the majors themselves are the story. Mention at most "
        "3 coins. Do not just list gainers and losers; say what it means and what you are watching next."
    ),
    "coin_focus": (
        "A short analysis of one coin. Use its 24h and 7d change, distance from the 7d high, volume versus "
        "its 7d average, RSI and funding when present. Give a clear view, the exact level that would "
        "change your mind, and what you are waiting for, written as normal sentences, never as labelled lines."
    ),
    "follow_up": (
        "A follow-up on your own earlier post about this coin (my_previous_post in DATA). Open with what "
        "changed since then: the price then versus now and the move since the post. Say plainly whether "
        "the view and the levels you gave held up or were wrong; never claim you traded it. DATA has the "
        "price then and now only, not the path in between: never say a level was or was not touched, tested "
        "or broken in the meantime, only where price is relative to it now. Then give the "
        "updated view, the level that now matters, and what you watch next."
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
   The feed preview shows only about two lines: the first sentence must carry the main $CASHTAG and the
   single most striking number (e.g. "$QNT is up 271% in seven days."), in a different wording each time.
   Never use: "let's dive in", "delve", "in the ever-evolving", "buckle up", "game-changer",
   "it's important to note", "in conclusion", "stay tuned", "not financial advice", "NFA", "DYOR".
   No disclaimer line at all. Do not use em dashes.
   Write like a human trader, not a template (humanizer rules): no labelled sections such as "My take:",
   "My view:", "What would change my view:" or "What I am waiting for:"; work the view and the levels into
   normal sentences. No "X is the whole story" or other punchline formulas, no lists of three, no runs of
   short dramatic fragments, no "here's the thing" or "the real question" openers, no "serves as",
   "highlights", "underscores", "crucial", "key" (adjective) or "landscape". Uneven sentence length is good. Do not use markdown (no **, no #, no bullet symbols); use line breaks.
7. change_7d_pct is measured from the price 7 days ago, not from low_7d: never write "up X% from the
   weekly low" with that number. DATA has snapshots only, never claim what price did in between
   (no "every dip was bought", "it tested", "it bounced off").
8. Length: short posts {q.short_post_chars[0]}-{q.short_post_chars[1]} characters; articles
   {q.article_chars[0]}-{q.article_chars[1]} characters.
9. Do not reuse the openings or structure of the author's recent posts listed in the request."""


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
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None,
                 http: httpx.Client | None = None):
        self.s = settings
        if settings.llm.provider == "openai_compatible":
            self.client = None
            self.http = http or httpx.Client(timeout=180)
        else:
            self.client = client or anthropic.Anthropic()

    def _parse(self, system: str, user: str, schema: type[BaseModel], max_tokens: int):
        if self.s.llm.provider == "openai_compatible":
            return self._parse_openai(system, user, schema, max_tokens)
        return self._parse_claude(system, user, schema, max_tokens)

    def _parse_openai(self, system: str, user: str, schema: type[BaseModel], max_tokens: int):
        llm = self.s.llm
        headers = {"Content-Type": "application/json"}
        if llm.api_key_env:
            key = os.environ.get(llm.api_key_env, "").strip()
            if not key:
                raise RuntimeError(f"Set {llm.api_key_env} in the environment (free key, see PC_SETUP.md).")
            headers["Authorization"] = f"Bearer {key}"
        system = (system + "\n\nReply with one JSON object only, no other text, matching this JSON schema:\n"
                  + json.dumps(schema.model_json_schema()))
        body = {
            "model": llm.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        last_error = None
        for _ in range(2):
            r = self.http.post(llm.base_url.rstrip("/") + "/chat/completions", json=body, headers=headers)
            if r.status_code >= 400:
                raise RuntimeError(f"LLM API error {r.status_code}: {r.text[:300]}")
            content = r.json()["choices"][0]["message"].get("content") or ""
            content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                return schema.model_validate_json(content)
            except ValueError as e:
                last_error = e
        raise RuntimeError(f"No valid JSON from the model: {last_error}")

    def _parse_claude(self, system: str, user: str, schema: type[BaseModel], max_tokens: int):
        kwargs = {}
        # Haiku has no server-side refusal fallback; only send it where it is supported.
        if "haiku" not in self.s.llm.model:
            kwargs = {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        response = self.client.beta.messages.parse(
            model=self.s.llm.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            thinking={"type": "adaptive"},
            output_config={"effort": self.s.llm.effort},
            output_format=schema,
            **kwargs,
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
