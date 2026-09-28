"""Fetch recent crypto headlines from RSS feeds (stdlib XML parsing, no extra deps)."""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass
class Headline:
    title: str
    summary: str
    link: str
    source: str
    published: datetime

    def brief(self) -> dict:
        return {"title": self.title, "summary": self.summary[:500], "source": self.source,
                "published_utc": self.published.strftime("%Y-%m-%d %H:%M")}


def _clean(text: str | None) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", text or "")).split())


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_feed(xml_text: str, source: str) -> list[Headline]:
    root = ET.fromstring(xml_text)
    out: list[Headline] = []
    for item in root.iter("item"):
        published = _parse_date(item.findtext("pubDate"))
        title = _clean(item.findtext("title"))
        if title and published:
            out.append(Headline(title, _clean(item.findtext("description")), (item.findtext("link") or "").strip(),
                                source, published))
    for entry in root.iter(f"{_ATOM}entry"):
        published = _parse_date(entry.findtext(f"{_ATOM}updated") or entry.findtext(f"{_ATOM}published"))
        title = _clean(entry.findtext(f"{_ATOM}title"))
        link_el = entry.find(f"{_ATOM}link")
        if title and published:
            out.append(Headline(title, _clean(entry.findtext(f"{_ATOM}summary")),
                                link_el.get("href", "") if link_el is not None else "", source, published))
    return out


def fetch_headlines(feeds: list[str], max_age_hours: int, http: httpx.Client | None = None) -> list[Headline]:
    http = http or httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 w2e/0.1"})
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    seen: set[str] = set()
    out: list[Headline] = []
    for url in feeds:
        source = re.sub(r"^www\.", "", httpx.URL(url).host)
        try:
            r = http.get(url)
            r.raise_for_status()
            items = parse_feed(r.text, source)
        except (httpx.HTTPError, ET.ParseError) as e:
            log.warning("feed %s failed: %s", url, e)
            continue
        for h in items:
            key = h.title.lower()
            if h.published >= cutoff and key not in seen:
                seen.add(key)
                out.append(h)
    return sorted(out, key=lambda h: h.published, reverse=True)
