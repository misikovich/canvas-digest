"""Compact, human-friendly formatting shared by notifications and MCP tools."""

from __future__ import annotations

from datetime import datetime, tzinfo

from uis_assistant.schema import Item


def when(dt: datetime | None, tz: tzinfo, all_day: bool = False) -> str:
    if dt is None:
        return "no date"
    local = dt.astimezone(tz)
    return local.strftime("%a %d %b") if all_day else local.strftime("%a %d %b %H:%M")


def span(start: datetime | None, end: datetime | None, tz: tzinfo, all_day: bool = False) -> str:
    if start is None:
        return "no date"
    s = when(start, tz, all_day)
    if end and not all_day and end.astimezone(tz).date() == start.astimezone(tz).date():
        s += end.astimezone(tz).strftime("–%H:%M")
    return s


def submission(item: Item) -> str:
    st = item.status or {}
    if st.get("excused"):
        return "excused"
    if st.get("graded"):
        return f"graded {st['grade']}" if st.get("grade") is not None else "graded"
    if st.get("submitted"):
        return "submitted"
    if st.get("marked_complete"):
        return "marked done"
    if st.get("missing"):
        return "MISSING"
    return "not submitted"


def is_open(item: Item) -> bool:
    """A deadline that still needs work."""
    st = item.status or {}
    return not any(st.get(k) for k in ("submitted", "graded", "excused", "marked_complete"))


def line(item: Item, tz: tzinfo, with_id: bool = True) -> str:
    """One-line summary of an item, e.g. for MCP list output."""
    tag = f"[{item.label}]"
    if item.kind == "event":
        s = f"{tag} {span(item.start, item.end, tz, item.all_day)} {item.title}"
        if item.locations:
            s += f" @ {'; '.join(item.locations)}"
    elif item.kind == "deadline":
        due = f"due {when(item.due, tz)}" if item.due else "no due date"
        s = f"{tag} {due} {item.title} — {submission(item)}"
        if item.points:
            s += f" ({item.points:g} pts)"
    elif item.kind == "announcement":
        read = "" if item.read is None else (" (read)" if item.read else " (UNREAD)")
        s = f"{tag} {when(item.created_at, tz)} {item.title}" + (f" — {item.author}" if item.author else "") + read
    else:
        s = f"{tag} {when(item.anchor, tz)} {item.title}"
    return f"{s}  ‹{item.id}›" if with_id else s
