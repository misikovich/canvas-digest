"""Parse Canvas DiscussionTopic objects (announcements) into Items.

Sources: /api/v1/announcements (courses, bulk) and
/api/v1/groups/:id/discussion_topics?only_announcements=true (groups).
"""

from __future__ import annotations

from uis_assistant.canvas.client import CanvasFormatError
from uis_assistant.canvas.planner import abs_url, parse_dt, set_context
from uis_assistant.htmlmd import html_to_md
from uis_assistant.schema import Item


def parse_topic(raw: dict, base_url: str, contexts: dict[str, str | None], context: str | None = None) -> Item:
    """`contexts` maps "course:123" -> context name; `context` overrides the topic's own context_code."""
    try:
        tid = raw["id"]
    except KeyError:
        raise CanvasFormatError(f"Announcement missing id: {raw!r:.200}") from None

    if context is None and raw.get("context_code"):
        ctype, _, cid = raw["context_code"].partition("_")
        context = f"{ctype}:{cid}"
    ctype, _, cid = (context or "").partition(":")

    author = raw.get("author") or {}
    item = Item(
        id=f"canvas:announcement:{tid}",
        source="canvas",
        kind="announcement",
        title=raw.get("title") or "",
        raw_type="announcement",
        url=abs_url(raw.get("html_url"), base_url),
        author=author.get("display_name") or raw.get("user_name"),
        body_md=html_to_md(raw.get("message"), base_url),
        attachments=[
            {"name": a.get("display_name") or a.get("filename"), "url": a.get("url")}
            for a in raw.get("attachments") or []
        ],
        read=raw.get("read_state") == "read" if raw.get("read_state") else None,
        created_at=parse_dt(raw.get("posted_at") or raw.get("created_at")),
    )
    set_context(item, ctype or None, cid or None, contexts.get(context or ""))
    return item
