"""Normalized item schema shared by every source."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from typing import Literal

Kind = Literal["event", "deadline", "announcement", "other"]

_DATETIME_FIELDS = ("start", "end", "due", "created_at", "updated_at")

# Fields whose change is meaningful to the user. Read state and updated_at are
# deliberately excluded so that opening an announcement doesn't count as a change.
HASH_FIELDS = (
    "title", "start", "end", "due", "all_day", "locations", "people",
    "body_md", "status", "points", "attachments",
)
# Submission state that matters; Canvas also returns noise like posted_at
STATUS_KEYS = ("submitted", "graded", "excused", "missing", "late", "has_feedback", "grade", "score")


@dataclass
class Item:
    id: str                      # stable across runs: "<source>:<type>:<source id>"
    source: str                  # "canvas"
    kind: Kind
    title: str
    raw_type: str = ""           # source-native type, e.g. "calendar_event", "quiz"
    url: str | None = None

    # Where it belongs (course code like "DAT120", or group name)
    context: str | None = None   # "course:18227" / "group:59062"
    course_code: str | None = None
    course_name: str | None = None

    # Time
    start: datetime | None = None
    end: datetime | None = None
    due: datetime | None = None
    all_day: bool = False

    # Event details
    locations: list[str] = field(default_factory=list)
    map_url: str | None = None
    people: list[str] = field(default_factory=list)

    author: str | None = None
    body_md: str | None = None
    attachments: list[dict] = field(default_factory=list)  # [{"name", "url"}]
    points: float | None = None
    read: bool | None = None
    status: dict | None = None   # submission state for deadlines, see STATUS_KEYS

    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def anchor(self) -> datetime | None:
        """The date that places the item in the semester window."""
        return self.start or self.due or self.created_at

    @property
    def label(self) -> str:
        return self.course_code or self.course_name or "Canvas"

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in _DATETIME_FIELDS:
            if d[k] is not None:
                d[k] = d[k].isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        known = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in known}
        for k in _DATETIME_FIELDS:
            if d.get(k):
                d[k] = datetime.fromisoformat(d[k])
        return cls(**d)

    def content_hash(self) -> str:
        d = self.to_dict()
        payload = json.dumps({k: d[k] for k in HASH_FIELDS}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]


def merge(base: Item, extra: Item) -> Item:
    """Overlay non-empty fields of `extra` onto `base` (same id). Status dicts are merged key-wise."""
    out = Item.from_dict(base.to_dict())
    for f in fields(Item):
        v = getattr(extra, f.name)
        if f.name == "status" and v and out.status:
            out.status = {**out.status, **{k: x for k, x in v.items() if x is not None}}
        elif v not in (None, "", [], {}) and not (f.name == "all_day" and v is False):
            setattr(out, f.name, v)
    return out


def clean_status(raw: dict | None) -> dict | None:
    if not raw:
        return None
    return {k: raw[k] for k in STATUS_KEYS if k in raw and raw[k] is not None}
