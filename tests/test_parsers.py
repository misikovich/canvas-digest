from conftest import BASE, load

from uis_assistant.canvas.announcements import parse_topic
from uis_assistant.canvas.assignments import parse_assignment
from uis_assistant.canvas.planner import parse_item
from uis_assistant.htmlmd import excerpt, html_to_md


def planner(i):
    return parse_item(load("planner.json")[i], BASE)


def test_tp_event_sections_and_title_prefix():
    it = planner(0)
    assert it.id == "canvas:calendar_event:5001"
    assert it.kind == "event"
    assert it.title == "Øvingstime 5: Øvingstime"
    assert it.course_code == "DAT120" and it.course_name == "Grunnleggende programmering"
    assert it.locations == ["D-201 Undervisning, Hus A", "D-301 Undervisning, Hus A"]
    assert it.people == ["Lecturer One", "Lecturer Two"]
    assert it.body_md is None  # TP boilerplate isn't kept as a body


def test_tp_event_map_url():
    assert planner(1).map_url.startswith("https://use.mazemap.com/")


def test_planner_assignment():
    it = planner(2)
    assert it.kind == "deadline" and it.raw_type == "assignment"
    assert it.due.isoformat() == "2026-10-16T21:59:59+00:00"
    assert it.points == 10.0
    assert it.status == {"submitted": False, "excused": False, "graded": False, "late": False,
                         "missing": False, "has_feedback": False}
    assert it.url == BASE + "/courses/101/assignments/7001"


def test_group_context():
    it = planner(4)
    assert it.context == "group:900"
    assert it.course_code is None and it.course_name == "CaseGr 4"


def test_announcement_topic():
    it = parse_topic(load("announcements.json")[0], BASE, {"course:101": "DAT120-1 26H Grunnleggende programmering"})
    assert it.id == "canvas:announcement:8001"
    assert it.context == "course:101" and it.course_code == "DAT120"
    assert it.author == "Lecturer One"
    assert it.read is False
    assert "**ready**" in it.body_md
    assert f"({BASE}/courses/101/files/55)" in it.body_md  # absolute, ?wrap=1 stripped
    assert "secret" not in it.body_md
    assert it.attachments == [{"name": "groups.pdf", "url": "https://canvas.example.edu/files/55/download"}]


def test_assignment_ids_match_planner():
    raws = load("assignments_101.json")
    a, undated, quiz = (parse_assignment(r, BASE, 101, "DAT120-1 26H Grunnleggende programmering") for r in raws[:3])
    assert a.id == "canvas:assignment:7001" and a.body_md == "Write a *loop*."
    assert a.status["submitted"] is False and a.status["graded"] is False
    assert undated.due is None
    assert quiz.id == "canvas:quiz:4001" and quiz.raw_type == "quiz"


def test_html_to_md_and_excerpt():
    assert html_to_md("", BASE) is None
    md = html_to_md('<h3>Hi</h3><p>See <a href="/x">here</a></p><script>x()</script>', BASE)
    assert md == f"### Hi\n\nSee [here]({BASE}/x)"
    assert excerpt(md) == "Hi See here"
    assert excerpt("word " * 100, 20).endswith("…")
