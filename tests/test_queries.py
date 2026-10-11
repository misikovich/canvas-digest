from datetime import datetime, timezone

import anyio

from uis_assistant import queries
from uis_assistant.mcp_server import build_server
from uis_assistant.runner import run_sync

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)


def synced(canvas, cfg, store):
    assert run_sync(cfg, store, lambda c: canvas.client()).ok
    return store


def test_upcoming_and_deadlines(canvas, cfg, store):
    synced(canvas, cfg, store)
    up = queries.upcoming(store, cfg, days=7, now=NOW)
    lines = up.splitlines()
    assert lines[0].startswith("[DAT120] Tue 13 Oct 10:15–12:00 Øvingstime 5")
    assert "@ D-201 Undervisning, Hus A; D-301 Undervisning, Hus A" in lines[0]
    assert any("due Fri 16 Oct 23:59 Øving 5 — not submitted (10 pts)" in l for l in lines)

    dl = queries.deadlines(store, cfg, now=NOW)
    assert "Quiz 1" in dl and "No due date:" in dl and "Prosjekt" in dl


def test_get_item_wraps_untrusted_body(canvas, cfg, store):
    synced(canvas, cfg, store)
    out = queries.get_item(store, cfg, "canvas:announcement:8001")
    assert '<untrusted source="canvas announcement">' in out and out.rstrip().endswith("</untrusted>")
    assert "attachment: groups.pdf" in out
    assert "No item" in queries.get_item(store, cfg, "nope")


def test_digest_and_list_new(canvas, cfg, store):
    synced(canvas, cfg, store)
    d = queries.digest(store, cfg, "week", now=NOW)
    assert "Schedule:" in d and "Open deadlines (next 14 days):" in d and "Unread announcements:" in d
    assert queries.list_new(store, cfg) == "No changes since you last checked."

    canvas.routes["/api/v1/planner/items"][2]["plannable"]["due_at"] = "2026-10-18T21:59:59Z"
    canvas.routes["/api/v1/courses/101/assignments"][0]["due_at"] = "2026-10-18T21:59:59Z"
    synced(canvas, cfg, store)
    out = queries.list_new(store, cfg, ack=False)
    assert out.startswith("[updated]") and "due Fri 16 Oct 23:59 → Sun 18 Oct 23:59" in out
    assert queries.list_new(store, cfg) == out
    assert queries.list_new(store, cfg) == "No changes since you last checked."


def test_mcp_tools_registered_and_callable(canvas, cfg, store):
    synced(canvas, cfg, store)
    server = build_server(cfg)

    async def go():
        tools = {t.name for t in await server.list_tools()}
        assert tools == {"upcoming", "deadlines", "list_new", "search", "get_item", "digest",
                         "courses", "status", "sync_now"}
        res = await server.call_tool("courses", {})
        return res

    res = anyio.run(go)
    text = str(res)
    assert "DAT120" in text and "group:900" in text
