"""Fetch one complete snapshot of everything we track in Canvas.

Nothing here touches the store: either the whole snapshot is built, or an
exception propagates and the caller records a failed run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from uis_assistant.canvas.announcements import parse_topic
from uis_assistant.canvas.assignments import parse_assignment
from uis_assistant.canvas.client import CanvasClient, ForbiddenError
from uis_assistant.canvas.planner import parse_dt, parse_item, split_course_name
from uis_assistant.config import Config
from uis_assistant.schema import Item, merge

log = logging.getLogger(__name__)

MAX_TERM_DAYS = 220  # longer "terms" (e.g. Default Term) don't describe a semester


@dataclass
class Snapshot:
    items: dict[str, Item]
    window_start: datetime
    window_end: datetime
    contexts: list[dict]                       # [{"context", "code", "name", "kind"}]
    incomplete_contexts: set[str] = field(default_factory=set)  # fetch failed; don't infer removals
    warnings: list[str] = field(default_factory=list)
    requests: int = 0


def fetch_snapshot(client: CanvasClient, cfg: Config, now: datetime | None = None) -> Snapshot:
    now = now or datetime.now(timezone.utc)
    base = cfg.base_url

    courses = [
        c for c in client.get_all("/api/v1/users/self/courses",
                                  {"enrollment_state": "active", "include[]": "term", "per_page": 100})
        if not c.get("access_restricted_by_date") and "id" in c
    ]
    groups = [g for g in client.get_all("/api/v1/users/self/groups", {"per_page": 100}) if "id" in g]

    start, end = semester_window(cfg, courses, now)
    contexts = [_course_context(c) for c in courses] + [
        {"context": f"group:{g['id']}", "code": None, "name": g.get("name"), "kind": "group"} for g in groups
    ]
    names = {c["context"]: _full_name(c) for c in contexts}
    snap = Snapshot(items={}, window_start=start, window_end=end, contexts=contexts)

    # 1. Planner: events, deadlines, announcement titles
    for raw in client.get_all("/api/v1/planner/items", {
        "start_date": _iso(start), "end_date": _iso(end), "per_page": 100,
    }):
        _add(snap, parse_item(raw, base))

    # 2. Announcements with bodies: courses in bulk, groups one by one
    course_ctx = [f"course_{c['id']}" for c in courses]
    params = {"start_date": _iso(start), "end_date": _iso(end), "per_page": 100}
    try:
        topics = client.get_all("/api/v1/announcements", [("context_codes[]", cc) for cc in course_ctx] + list(params.items()))
    except ForbiddenError:
        # One course without announcement access fails the bulk call; fall back to per course
        topics = []
        for cc in course_ctx:
            try:
                topics += client.get_all("/api/v1/announcements", [("context_codes[]", cc)] + list(params.items()))
            except ForbiddenError as e:
                _incomplete(snap, cc.replace("_", ":"), e)
    for raw in topics:
        _add(snap, parse_topic(raw, base, names))

    for g in groups:
        ctx = f"group:{g['id']}"
        try:
            topics = client.get_all(f"/api/v1/groups/{g['id']}/discussion_topics",
                                    {"only_announcements": "true", "per_page": 100})
        except ForbiddenError as e:
            _incomplete(snap, ctx, e)
            continue
        for raw in topics:
            item = parse_topic(raw, base, names, context=ctx)
            if item.created_at is None or start <= item.created_at <= end:
                _add(snap, item)

    # 3. Assignments: descriptions, grades, and deadlines without a due date
    for c in courses:
        ctx = f"course:{c['id']}"
        try:
            raws = client.get_all(f"/api/v1/courses/{c['id']}/assignments",
                                  {"include[]": "submission", "per_page": 100})
        except ForbiddenError as e:
            _incomplete(snap, ctx, e)
            continue
        for raw in raws:
            item = parse_assignment(raw, base, c["id"], c.get("name"))
            if item.id in snap.items or item.due is None or start <= item.due <= end:
                _add(snap, item)

    snap.requests = client.requests_made
    return snap


def semester_window(cfg: Config, courses: list[dict], now: datetime) -> tuple[datetime, datetime]:
    """Config override > the current Canvas term(s) > calendar half-year."""
    tz = cfg.tz
    if cfg.window_start and cfg.window_end:
        s, e = cfg.window_start, cfg.window_end
    else:
        s, e = _term_window(courses, now) or _calendar_window(now.astimezone(tz).date())
    start = datetime.combine(s, time.min, tz).astimezone(timezone.utc)
    end = datetime.combine(e + timedelta(days=1), time.min, tz).astimezone(timezone.utc)
    return start, end


def _term_window(courses: list[dict], now: datetime) -> tuple[date, date] | None:
    spans = []
    for c in courses:
        term = c.get("term") or {}
        s, e = parse_dt(term.get("start_at")), parse_dt(term.get("end_at"))
        if s and e and s <= now <= e and (e - s).days <= MAX_TERM_DAYS:
            spans.append((s, e))
    if not spans:
        return None
    return min(s for s, _ in spans).date(), max(e for _, e in spans).date()


def _calendar_window(today: date) -> tuple[date, date]:
    if today.month >= 7:
        return date(today.year, 8, 1), date(today.year, 12, 31)
    return date(today.year, 1, 1), date(today.year, 6, 30)


def _course_context(c: dict) -> dict:
    code, name = split_course_name(c.get("name"))
    return {"context": f"course:{c['id']}", "code": code, "name": name, "kind": "course",
            "full_name": c.get("name")}


def _full_name(ctx: dict) -> str | None:
    return ctx.get("full_name") or ctx.get("name")


def _add(snap: Snapshot, item: Item) -> None:
    prev = snap.items.get(item.id)
    snap.items[item.id] = merge(prev, item) if prev else item


def _incomplete(snap: Snapshot, ctx: str, err: Exception) -> None:
    snap.incomplete_contexts.add(ctx)
    snap.warnings.append(f"{ctx}: {err}")
    log.warning("Skipping %s: %s", ctx, err)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
