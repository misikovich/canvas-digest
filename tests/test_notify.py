from datetime import datetime, timedelta, timezone

from uis_assistant import notify
from uis_assistant.schema import Item

W0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
W1 = datetime(2027, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)  # 12:00 in Oslo


def deadline(id="canvas:assignment:1", due=NOW + timedelta(days=5), **kw):
    return Item(id=id, source="canvas", kind="deadline", title=kw.pop("title", "Øving 5"), due=due,
                context="course:1", course_code="DAT120", url="https://x/a/1",
                status=kw.pop("status", {"submitted": False}), **kw)


def event(id="canvas:calendar_event:1", start=NOW + timedelta(days=1), room="Aud 1"):
    return Item(id=id, source="canvas", kind="event", title="Forelesning", start=start,
                end=start + timedelta(hours=2), locations=[room], context="course:1", course_code="DAT120")


def run(store, cfg, now=NOW):
    n = notify.build(store, cfg, now)
    n.commit()
    return n.text


def test_nothing_to_say(store, cfg):
    store.apply_snapshot([deadline()], W0, W1)
    assert run(store, cfg) == notify.NO_REPLY


def test_new_deadline_and_due_change_then_cursor_advances(store, cfg):
    store.apply_snapshot([event()], W0, W1)  # baseline
    store.apply_snapshot([event(), deadline()], W0, W1)
    text = run(store, cfg)
    assert "🆕 New deadline: Øving 5" in text and text.startswith("Canvas updates:\nDAT120")

    store.apply_snapshot([event(), deadline(due=NOW + timedelta(days=6))], W0, W1)
    text = run(store, cfg)
    assert "⏰ Due date changed: Øving 5: Mon 19 Oct 12:00 → Tue 20 Oct 12:00" in text
    assert run(store, cfg) == notify.NO_REPLY


def test_event_changes(store, cfg):
    store.apply_snapshot([event(), event("canvas:calendar_event:2")], W0, W1)
    store.apply_snapshot([event(room="Aud 2")], W0, W1)
    text = run(store, cfg)
    assert "🔁 Changed: Forelesning" in text and "room Aud 1 → Aud 2" in text
    assert "❌ Removed from timetable: Forelesning" in text


def test_graded(store, cfg):
    store.apply_snapshot([deadline()], W0, W1)
    store.apply_snapshot([deadline(status={"submitted": True, "graded": True, "grade": "A"})], W0, W1)
    assert "✅ Graded: Øving 5 — A" in run(store, cfg)


def test_reminders_dedupe_and_rearm(store, cfg):
    store.apply_snapshot([deadline(due=NOW + timedelta(hours=40))], W0, W1)
    text = run(store, cfg)
    assert "Deadlines coming up" in text and "(40h left)" in text
    assert run(store, cfg) == notify.NO_REPLY                       # 48h reminder sent once
    assert "(5h left)" in run(store, cfg, NOW + timedelta(hours=35))  # 6h threshold
    assert run(store, cfg, NOW + timedelta(hours=36)) == notify.NO_REPLY

    # Deadline moved: reminders re-arm for the new due time
    store.apply_snapshot([deadline(due=NOW + timedelta(hours=60))], W0, W1)
    text = run(store, cfg, NOW + timedelta(hours=36))
    assert "⏰ Due date changed" in text and "Deadlines coming up" not in text
    assert "(4h left)" in run(store, cfg, NOW + timedelta(hours=56))


def test_submitted_deadlines_get_no_reminder(store, cfg):
    store.apply_snapshot([deadline(due=NOW + timedelta(hours=3), status={"submitted": True})], W0, W1)
    assert run(store, cfg) == notify.NO_REPLY


def test_quiet_hours_hold_updates_but_not_urgent_reminders(store, cfg):
    cfg.quiet_start, cfg.quiet_end = 23, 7
    night = datetime(2026, 10, 14, 22, 0, tzinfo=timezone.utc)  # 00:00 Oslo
    store.apply_snapshot([event()], W0, W1)
    store.apply_snapshot([event(), deadline(), deadline("canvas:assignment:2", due=night + timedelta(hours=3), title="Urgent")], W0, W1)

    text = run(store, cfg, night)
    assert "Urgent" in text and "New deadline" not in text
    morning = datetime(2026, 10, 15, 5, 0, tzinfo=timezone.utc)  # 07:00 Oslo
    text = run(store, cfg, morning)
    assert "🆕 New deadline: Øving 5" in text


def test_health_alerts_once_and_recover(store, cfg):
    rid = store.start_run()
    store.finish_run(rid, "failed", error_kind="auth", error="token rejected")
    assert "access token was rejected" in run(store, cfg)
    rid = store.start_run()
    store.finish_run(rid, "failed", error_kind="auth", error="token rejected")
    assert run(store, cfg) == notify.NO_REPLY
    rid = store.start_run()
    store.finish_run(rid, "ok")
    assert "working again" in run(store, cfg)


def test_transient_failures_alert_after_threshold(store, cfg):
    for i in range(3):
        rid = store.start_run()
        store.finish_run(rid, "failed", error_kind="canvas", error="Canvas 503")
        text = run(store, cfg)
        assert ("failed 3 times" in text) == (i == 2)


def test_dry_run_does_not_commit(store, cfg):
    store.apply_snapshot([event()], W0, W1)
    store.apply_snapshot([event(), deadline()], W0, W1)
    n = notify.build(store, cfg, NOW)
    assert "New deadline" in n.text
    assert "New deadline" in notify.build(store, cfg, NOW).text
