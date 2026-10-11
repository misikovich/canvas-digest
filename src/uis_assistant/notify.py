"""Turn store changes, upcoming deadlines and sync health into one Telegram message.

The output is printed by `uis tick` and delivered by an OpenClaw command job.
Printing NO_REPLY tells OpenClaw there's nothing to send.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone

from uis_assistant import fmt
from uis_assistant.config import Config
from uis_assistant.htmlmd import excerpt
from uis_assistant.schema import Item
from uis_assistant.store import Change, Store

NO_REPLY = "NO_REPLY"
CONSUMER = "telegram"
NEW_EVENT_HORIZON = timedelta(days=7)      # new timetable entries further out go to the digest only
ANNOUNCEMENT_MAX_AGE = timedelta(days=14)  # don't announce old announcements that reappear


@dataclass
class Notification:
    text: str
    _commit: Callable[[], None] = field(repr=False)

    @property
    def empty(self) -> bool:
        return self.text == NO_REPLY

    def commit(self) -> None:
        self._commit()


def build(store: Store, cfg: Config, now: datetime | None = None) -> Notification:
    now = now or datetime.now(timezone.utc)
    tz = cfg.tz
    quiet_until = _quiet_until(cfg, now)

    health_lines, health_state = _health(store, cfg)
    cursor = store.cursor(CONSUMER)
    changes = store.changes_since(cursor)
    by_course: dict[str, list[str]] = defaultdict(list)
    announced: set[str] = set()
    for ch in changes:
        if text := _change_line(ch, now, tz):
            by_course[ch.item.label].append(text)
            announced.add(ch.item_id)

    reminders = _due_reminders(store, cfg, now)
    if quiet_until:
        # Hold everything except reminders for deadlines that fall inside the quiet window
        reminders = [r for r in reminders if r[0].due <= quiet_until]
        health_lines, by_course, announced = [], {}, set()
    reminder_lines: dict[str, list[str]] = defaultdict(list)
    for item, rule, _ in reminders:
        if item.id not in announced:
            hours = (item.due - now).total_seconds() / 3600
            left = f"{hours:.0f}h" if hours >= 1 else f"{max(1, hours * 60):.0f} min"
            reminder_lines[item.label].append(f"⏳ {item.title} — due {fmt.when(item.due, tz)} ({left} left)\n   {item.url}")

    parts = list(health_lines)
    if reminder_lines:
        parts.append("Deadlines coming up:\n" + _grouped(reminder_lines))
    if by_course:
        parts.append("Canvas updates:\n" + _grouped(by_course))
    text = "\n\n".join(parts) if parts else NO_REPLY

    last_seq = changes[-1].seq if changes else cursor

    def commit() -> None:
        with store.transaction():
            for item, _, rules in reminders:
                for rule in rules:
                    store.mark_reminder(item.id, rule, item.due)
            if not quiet_until:
                store.set_cursor(CONSUMER, last_seq)
                store.set_state("health_notified", health_state)

    return Notification(text, commit)


def _grouped(groups: dict[str, list[str]]) -> str:
    return "\n\n".join(f"{label}\n" + "\n".join(f"• {l}" for l in lines) for label, lines in sorted(groups.items()))


def _change_line(ch: Change, now: datetime, tz) -> str | None:
    it, diff = ch.item, ch.diff or {}
    future = lambda dt: dt is not None and dt > now  # noqa: E731

    if ch.type == "new":
        if it.kind == "deadline" and (it.due is None or future(it.due)):
            due = f"due {fmt.when(it.due, tz)}" if it.due else "no due date yet"
            return f"🆕 New deadline: {it.title} — {due}\n   {it.url}"
        if it.kind == "announcement" and it.created_at and now - it.created_at <= ANNOUNCEMENT_MAX_AGE:
            by = f" ({it.author})" if it.author else ""
            body = excerpt(it.body_md, 240)
            return f"📣 {it.title}{by}" + (f"\n   {body}" if body else "") + f"\n   {it.url}"
        if it.kind == "event" and future(it.start) and it.start - now <= NEW_EVENT_HORIZON:
            return f"🗓 New: {it.title} {_event_when(it, tz)}"
        return None

    if ch.type == "removed":
        if it.kind == "event" and future(it.start):
            return f"❌ Removed from timetable: {it.title} {_event_when(it, tz)}"
        if it.kind == "deadline" and future(it.due):
            return f"🗑 Deadline removed: {it.title} (was due {fmt.when(it.due, tz)})"
        return None

    # updated
    if it.kind == "deadline":
        out = []
        if "due" in diff and (it.due is None or future(it.due)):
            old = _parse(diff["due"][0])
            out.append(f"⏰ Due date changed: {it.title}: {fmt.when(old, tz)} → {fmt.when(it.due, tz)}")
        if "status" in diff:
            old, new = diff["status"][0] or {}, diff["status"][1] or {}
            if new.get("graded") and not old.get("graded"):
                grade = f" — {new['grade']}" if new.get("grade") is not None else ""
                out.append(f"✅ Graded: {it.title}{grade}")
            elif new.get("has_feedback") and not old.get("has_feedback"):
                out.append(f"💬 New feedback: {it.title}")
        return "\n   ".join(out) + f"\n   {it.url}" if out else None
    if it.kind == "event" and (future(it.start) or ("start" in diff and future(_parse(diff["start"][0])))):
        what = []
        if "start" in diff or "end" in diff:
            old_start = _parse(diff["start"][0]) if "start" in diff else it.start
            old_end = _parse(diff["end"][0]) if "end" in diff else it.end
            what.append(f"time {fmt.span(old_start, old_end, tz)} → {fmt.span(it.start, it.end, tz)}")
        if "locations" in diff:
            what.append(f"room {'; '.join(diff['locations'][0] or ['?'])} → {'; '.join(it.locations or ['?'])}")
        if what:
            return f"🔁 Changed: {it.title} {fmt.span(it.start, it.end, tz, it.all_day)}: " + ", ".join(what)
    return None


def _event_when(it: Item, tz) -> str:
    s = fmt.span(it.start, it.end, tz, it.all_day)
    return s + (f" @ {'; '.join(it.locations)}" if it.locations else "")


def _due_reminders(store: Store, cfg: Config, now: datetime) -> list[tuple[Item, str, list[str]]]:
    """Open deadlines that crossed a reminder threshold and haven't been reminded at it.

    Returns (item, rule shown, all rules to mark sent). If several thresholds were
    crossed at once (e.g. a deadline added 5h before it's due) only one reminder goes out.
    """
    out = []
    for item in store.items(kinds=["deadline"]):
        if item.due is None or item.due <= now or not fmt.is_open(item):
            continue
        crossed = [h for h in cfg.reminder_hours if now >= item.due - timedelta(hours=h)]
        if not crossed:
            continue
        rules = [f"{h}h" for h in crossed]
        shown = f"{min(crossed)}h"
        if not store.reminder_sent(item.id, shown, item.due):
            out.append((item, shown, rules))
    return sorted(out, key=lambda r: r[0].due)


def _health(store: Store, cfg: Config) -> tuple[list[str], str]:
    failures = store.consecutive_failures()
    if failures and failures[0]["error_kind"] in ("auth", "config"):
        state = "auth"
    elif len(failures) >= cfg.failure_alert_after:
        state = "failing"
    else:
        state = "ok"
    notified = store.get_state("health_notified", "ok")
    if state == notified:
        return [], state
    if state == "auth":
        msg = ("⚠️ Canvas sync stopped: the access token was rejected or is missing.\n"
               f"   {failures[0]['error']}\n"
               "   Create a new token in Canvas (Account → Settings → New Access Token) and run `uis init` on the server.")
    elif state == "failing":
        msg = (f"⚠️ Canvas sync has failed {len(failures)} times in a row. Data may be out of date.\n"
               f"   Last error: {failures[0]['error']}")
    else:
        msg = "✅ Canvas sync is working again."
    return [msg], state


def _quiet_until(cfg: Config, now: datetime) -> datetime | None:
    """End of the current quiet period, or None if we're outside quiet hours."""
    qs, qe = cfg.quiet_start, cfg.quiet_end
    if qs == qe:
        return None
    local = now.astimezone(cfg.tz)
    h = local.hour
    quiet = (qs <= h < qe) if qs < qe else (h >= qs or h < qe)
    if not quiet:
        return None
    end_day = local.date() if h < qe else local.date() + timedelta(days=1)
    return datetime.combine(end_day, time(qe), cfg.tz)


def _parse(v) -> datetime | None:
    return datetime.fromisoformat(v) if v else None
