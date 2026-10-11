"""Parse Canvas /api/v1/planner/items into normalized Items."""

from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser

from uis_assistant.canvas.client import CanvasFormatError, decode_json
from uis_assistant.htmlmd import html_to_md
from uis_assistant.schema import Item, clean_status

# "DAT120-1 26H Grunnleggende programmering" -> code, section, term, name
COURSE_RE = re.compile(r"^(?P<code>[A-Z]{2,}\d{2,})-(?P<section>\d+)\s+(?P<term>\d{2}[HV])\s+(?P<name>.+)$")

KIND_BY_TYPE = {
    "calendar_event": "event",
    "assignment": "deadline",
    "quiz": "deadline",
    "discussion_topic": "deadline",
    "announcement": "announcement",
}


def load_page(text: str) -> list[dict]:
    data = decode_json(text)
    if isinstance(data, dict) and "errors" in data:
        raise CanvasFormatError(f"Canvas returned errors: {data['errors']}")
    if not isinstance(data, list):
        raise CanvasFormatError(f"Expected a list of planner items, got {type(data).__name__}")
    return data


def parse_item(raw: dict, base_url: str) -> Item:
    try:
        ptype = raw["plannable_type"]
        p = raw["plannable"]
        pid = raw["plannable_id"]
    except KeyError as e:
        raise CanvasFormatError(f"Planner item missing {e}: {raw!r:.200}") from None

    item = Item(
        id=f"canvas:{ptype}:{pid}",
        source="canvas",
        kind=KIND_BY_TYPE.get(ptype, "other"),
        title=p.get("title") or "",
        raw_type=ptype,
        url=abs_url(raw.get("html_url"), base_url),
        created_at=parse_dt(p.get("created_at")),
        updated_at=parse_dt(p.get("updated_at")),
    )
    set_context(item, raw.get("context_type"), raw.get(f"{(raw.get('context_type') or '').lower()}_id"),
                raw.get("context_name"))

    if ptype == "calendar_event":
        _parse_event(item, p, base_url)
    elif item.kind == "deadline":
        item.due = parse_dt(p.get("due_at") or p.get("todo_date") or raw.get("plannable_date"))
        item.points = p.get("points_possible")
        status = clean_status(raw["submissions"]) if isinstance(raw.get("submissions"), dict) else None
        if (raw.get("planner_override") or {}).get("marked_complete"):
            status = {**(status or {}), "marked_complete": True}
        item.status = status
    elif ptype == "announcement":
        item.read = p.get("read_state") == "read"
    else:
        item.start = parse_dt(raw.get("plannable_date"))

    return item


def set_context(item: Item, context_type: str | None, context_id, context_name: str | None) -> None:
    ctype = (context_type or "").lower()
    if ctype and context_id is not None:
        item.context = f"{ctype}:{context_id}"
    code, name = split_course_name(context_name)
    item.course_code, item.course_name = code, name


def split_course_name(name: str | None) -> tuple[str | None, str | None]:
    m = COURSE_RE.match(name or "")
    return (m["code"], m["name"]) if m else (None, name)


def _parse_event(item: Item, p: dict, base_url: str) -> None:
    item.start = parse_dt(p.get("start_at"))
    item.end = parse_dt(p.get("end_at"))
    item.all_day = bool(p.get("all_day"))

    # TP (timetable system) prefixes titles with the course code: "DAT120: Forelesning"
    if item.course_code and item.title.startswith(f"{item.course_code}: "):
        item.title = item.title[len(item.course_code) + 2:]

    desc = p.get("description") or ""
    tp = _TPDescription.parse(desc)
    if tp.sections:
        item.locations = tp.sections.get("Sted") or tp.sections.get("Steder") or []
        item.people = tp.sections.get("Fagperson") or tp.sections.get("Fagpersoner") or []
        item.map_url = tp.map_url
    elif desc:
        item.body_md = html_to_md(desc, base_url)

    if not item.locations and p.get("location_name"):
        item.locations = [p["location_name"]]


class _TPDescription(HTMLParser):
    """Extract the <h3>Heading</h3><div>value</div>... sections that TP bookings use.

    Stops at <hr> (the boilerplate footer) and skips hidden divs.
    """

    def __init__(self) -> None:
        super().__init__()
        self.sections: dict[str, list[str]] = {}
        self.map_url: str | None = None
        self._heading: str | None = None
        self._in_h3 = False
        self._buf: list[str] | None = None
        self._hidden_depth = 0
        self._done = False

    @classmethod
    def parse(cls, html: str) -> "_TPDescription":
        p = cls()
        p.feed(html)
        p.close()
        return p

    def handle_starttag(self, tag, attrs):
        if self._done:
            return
        a = dict(attrs)
        if tag == "hr":
            self._done = True
        elif tag == "h3":
            self._in_h3, self._heading = True, ""
        elif tag == "div":
            if self._hidden_depth or "display: none" in (a.get("style") or ""):
                self._hidden_depth += 1
            elif self._heading:
                self._buf = []
        elif tag == "a" and not self.map_url and "mazemap" in (a.get("href") or ""):
            self.map_url = a["href"]

    def handle_endtag(self, tag):
        if tag == "h3" and self._in_h3:
            self._in_h3 = False
            self._heading = self._heading.strip()
        elif tag == "div":
            if self._hidden_depth:
                self._hidden_depth -= 1
            elif self._buf is not None:
                text = " ".join("".join(self._buf).split())
                if text:
                    self.sections.setdefault(self._heading, []).append(text)
                self._buf = None

    def handle_data(self, data):
        if self._done or self._hidden_depth:
            return
        if self._in_h3:
            self._heading += data
        elif self._buf is not None:
            self._buf.append(data)


def parse_dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def abs_url(u: str | None, base_url: str) -> str | None:
    if not u:
        return None
    return u if u.startswith("http") else base_url + u
