"""Post history (append-only JSONL) and the review queue (one JSON file per draft)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Draft:
    format: str
    text: str
    title: str | None = None
    coins: list[str] = field(default_factory=list)
    image_path: str | None = None
    score: int | None = None
    review_notes: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex[:10])
    created_at: str = field(default_factory=lambda: utcnow().isoformat())

    @property
    def is_article(self) -> bool:
        return bool(self.title)


@dataclass
class HistoryEntry:
    draft_id: str
    format: str
    coins: list[str]
    text: str
    title: str | None
    status: str  # published | dry-run | failed
    published_at: str
    post_id: str | None = None
    link: str | None = None
    error: str | None = None

    @property
    def published_dt(self) -> datetime:
        return datetime.fromisoformat(self.published_at)


class Store:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.dir / "history.jsonl"
        self.queue_dir = self.dir / "queue"

    # history -------------------------------------------------------------
    def history(self) -> list[HistoryEntry]:
        if not self.history_file.exists():
            return []
        out = []
        for line in self.history_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(HistoryEntry(**json.loads(line)))
        return out

    def append(self, entry: HistoryEntry) -> None:
        with self.history_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    def published(self) -> list[HistoryEntry]:
        return [h for h in self.history() if h.status == "published"]

    def published_since(self, since: datetime) -> list[HistoryEntry]:
        return [h for h in self.published() if h.published_dt >= since]

    def last_published(self) -> HistoryEntry | None:
        pub = self.published()
        return pub[-1] if pub else None

    def recent_texts(self, n: int = 60) -> list[str]:
        return [h.text for h in self.history() if h.status in ("published", "dry-run")][-n:]

    def recent_coins(self, hours: int) -> set[str]:
        since = utcnow() - timedelta(hours=hours)
        return {c for h in self.published_since(since) for c in h.coins}

    def recent_main_coins(self, hours: int) -> set[str]:
        """Only the first cashtag of each post: the coin the post was about."""
        since = utcnow() - timedelta(hours=hours)
        return {h.coins[0] for h in self.published_since(since) if h.coins}

    def last_format(self) -> str | None:
        last = self.last_published()
        return last.format if last else None

    # review queue --------------------------------------------------------
    def enqueue(self, draft: Draft) -> Path:
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        p = self.queue_dir / f"{draft.id}.json"
        p.write_text(json.dumps(asdict(draft), ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def queued(self) -> list[Draft]:
        if not self.queue_dir.exists():
            return []
        drafts = [Draft(**json.loads(p.read_text(encoding="utf-8"))) for p in self.queue_dir.glob("*.json")]
        return sorted(drafts, key=lambda d: d.created_at)

    def get_queued(self, draft_id: str) -> Draft:
        p = self.queue_dir / f"{draft_id}.json"
        if not p.exists():
            raise KeyError(f"No queued draft with id {draft_id}")
        return Draft(**json.loads(p.read_text(encoding="utf-8")))

    def dequeue(self, draft_id: str) -> None:
        (self.queue_dir / f"{draft_id}.json").unlink(missing_ok=True)
