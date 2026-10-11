from datetime import datetime, timedelta, timezone

import httpx

from uis_assistant.runner import run_sync
from uis_assistant.schema import Item

W0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
W1 = datetime(2027, 1, 1, tzinfo=timezone.utc)
DUE = datetime(2026, 10, 16, 22, tzinfo=timezone.utc)


def deadline(id="canvas:assignment:1", due=DUE, **kw):
    return Item(id=id, source="canvas", kind="deadline", title=kw.pop("title", "Task"), due=due,
                context=kw.pop("context", "course:1"), course_code="DAT120", **kw)


def test_first_snapshot_is_baseline(store):
    res = store.apply_snapshot([deadline()], W0, W1)
    assert res.baseline and res.new == 1
    assert store.changes_since(0) == []


def test_update_diff_and_idempotency(store):
    store.apply_snapshot([deadline(), deadline("canvas:assignment:2")], W0, W1)
    later = DUE + timedelta(days=2)
    res = store.apply_snapshot([deadline(due=later), deadline("canvas:assignment:2")], W0, W1)
    assert (res.updated, res.unchanged) == (1, 1)
    [ch] = store.changes_since(0)
    assert ch.type == "updated" and ch.diff == {"due": [DUE.isoformat(), later.isoformat()]}

    again = store.apply_snapshot([deadline(due=later), deadline("canvas:assignment:2")], W0, W1)
    assert again.updated == again.new == again.removed == 0
    assert len(store.changes_since(0)) == 1


def test_read_state_is_not_a_change(store):
    ann = Item(id="canvas:announcement:1", source="canvas", kind="announcement", title="A", read=False,
               created_at=DUE)
    store.apply_snapshot([ann, deadline()], W0, W1)
    ann.read = True
    store.apply_snapshot([ann, deadline()], W0, W1)
    assert store.changes_since(0) == []
    assert store.get("canvas:announcement:1")[0].read is True  # but the stored copy is current


def test_removal_only_inside_window_and_complete_contexts(store):
    old = deadline("canvas:assignment:old", due=W0 - timedelta(days=30))
    gone = deadline("canvas:assignment:gone")
    guarded = deadline("canvas:assignment:guarded", context="course:2")
    store.apply_snapshot([deadline(), old, gone, guarded], W0 - timedelta(days=60), W1)
    res = store.apply_snapshot([deadline()], W0, W1, incomplete_contexts={"course:2"})
    assert res.removed == 1
    [ch] = store.changes_since(0)
    assert (ch.item_id, ch.type) == ("canvas:assignment:gone", "removed")
    assert store.get("canvas:assignment:gone")[1] is not None

    res = store.apply_snapshot([deadline(), gone], W0, W1)  # it came back
    assert store.changes_since(ch.seq)[0].type == "new"


def test_failed_sync_leaves_store_untouched(canvas, cfg, store):
    assert run_sync(cfg, store, lambda c: canvas.client()).ok
    before = store.db.execute("SELECT id, content_hash FROM items ORDER BY id").fetchall()

    canvas.routes["/api/v1/planner/items"][0]["plannable"]["title"] = "DAT120: Renamed"
    canvas.routes["/api/v1/courses/101/assignments"] = httpx.Response(500, text="boom")
    out = run_sync(cfg, store, lambda c: canvas.client())
    assert not out.ok and out.error_kind == "canvas"
    after = store.db.execute("SELECT id, content_hash FROM items ORDER BY id").fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    assert store.changes_since(0) == []
    assert store.recent_runs(1)[0]["status"] == "failed"


def test_auth_failure_recorded(canvas, cfg, store):
    canvas.routes["/api/v1/users/self/courses"] = httpx.Response(401, json={"errors": [{"message": "Invalid access token."}]})
    out = run_sync(cfg, store, lambda c: canvas.client())
    assert out.error_kind == "auth"


def test_search(canvas, cfg, store):
    run_sync(cfg, store, lambda c: canvas.client())
    assert [i.id for i in store.search("gruppe")] == ["canvas:announcement:8001"]
    assert [i.id for i in store.search('loop"')] == ["canvas:assignment:7001"]  # body text, odd quoting
