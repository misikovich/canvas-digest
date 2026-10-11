"""Parse /api/v1/courses/:id/assignments?include[]=submission into deadline Items.

IDs match the planner's: quizzes and graded discussions are keyed by their quiz /
topic id, because that's what the planner uses as plannable_id.
"""

from __future__ import annotations

from uis_assistant.canvas.client import CanvasFormatError
from uis_assistant.canvas.planner import abs_url, parse_dt, set_context
from uis_assistant.htmlmd import html_to_md
from uis_assistant.schema import Item


def assignment_id(raw: dict) -> str:
    if raw.get("quiz_id"):
        return f"canvas:quiz:{raw['quiz_id']}"
    topic = raw.get("discussion_topic") or {}
    if topic.get("id"):
        return f"canvas:discussion_topic:{topic['id']}"
    return f"canvas:assignment:{raw['id']}"


def parse_assignment(raw: dict, base_url: str, course_id: int, course_name: str | None) -> Item:
    if "id" not in raw:
        raise CanvasFormatError(f"Assignment missing id: {raw!r:.200}")
    item_id = assignment_id(raw)
    item = Item(
        id=item_id,
        source="canvas",
        kind="deadline",
        title=raw.get("name") or "",
        raw_type=item_id.split(":")[1],
        url=abs_url(raw.get("html_url"), base_url),
        due=parse_dt(raw.get("due_at")),
        points=raw.get("points_possible"),
        body_md=html_to_md(raw.get("description"), base_url),
        status=_submission_status(raw.get("submission")),
        created_at=parse_dt(raw.get("created_at")),
        updated_at=parse_dt(raw.get("updated_at")),
    )
    set_context(item, "course", course_id, course_name)
    return item


def _submission_status(sub: dict | None) -> dict | None:
    if not sub:
        return None
    status = {
        "submitted": bool(sub.get("submitted_at")),
        "graded": sub.get("grade") is not None,
        "excused": bool(sub.get("excused")),
        "missing": bool(sub.get("missing")),
        "late": bool(sub.get("late")),
    }
    if sub.get("grade") is not None:
        status["grade"] = sub["grade"]
    if sub.get("score") is not None:
        status["score"] = sub["score"]
    return status
