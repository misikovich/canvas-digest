import httpx
from conftest import NOW

from uis_assistant.canvas.sync import fetch_snapshot


def test_snapshot_merges_sources(canvas, cfg):
    snap = fetch_snapshot(canvas.client(), cfg, NOW)
    items = snap.items
    assert set(items) == {
        "canvas:calendar_event:5001", "canvas:calendar_event:5002",
        "canvas:assignment:7001", "canvas:assignment:7002", "canvas:quiz:4001",
        "canvas:announcement:8001", "canvas:announcement:8101",
    }  # 7004 is outside the window

    a = items["canvas:assignment:7001"]
    assert a.body_md == "Write a *loop*."            # from assignments endpoint
    assert a.status["has_feedback"] is False         # from planner, kept by merge
    ann = items["canvas:announcement:8001"]
    assert ann.author == "Lecturer One" and ann.body_md
    group_ann = items["canvas:announcement:8101"]
    assert group_ann.context == "group:900" and group_ann.course_name == "CaseGr 4"
    assert {c["context"] for c in snap.contexts} == {"course:101", "course:102", "group:900"}
    assert not snap.incomplete_contexts


def test_window_from_terms(canvas, cfg):
    snap = fetch_snapshot(canvas.client(), cfg, NOW)
    assert snap.window_start.isoformat().startswith("2026-07-31T22:00")  # Aug 1, Oslo midnight
    planner_call = next(r for r in canvas.calls if r.url.path == "/api/v1/planner/items")
    assert planner_call.url.params["start_date"] == "2026-07-31T22:00:00Z"


def test_forbidden_course_is_incomplete_not_fatal(canvas, cfg):
    canvas.routes["/api/v1/courses/102/assignments"] = httpx.Response(
        401, json={"status": "unauthorized", "errors": [{"message": "user not authorized to perform that action"}]})
    snap = fetch_snapshot(canvas.client(), cfg, NOW)
    assert snap.incomplete_contexts == {"course:102"}
    assert snap.warnings
