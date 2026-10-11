"""Shared fixtures: anonymized Canvas payloads and a fake Canvas over httpx.MockTransport."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from uis_assistant.canvas.client import CanvasClient
from uis_assistant.config import Config
from uis_assistant.store import Store

BASE = "https://canvas.example.edu"
FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(data_dir=tmp_path, base_url=BASE, quiet_start=0, quiet_end=0)


@pytest.fixture
def store(cfg) -> Store:
    s = Store(cfg.db_path)
    yield s
    s.close()


class FakeCanvas:
    """Routes GET paths to canned JSON. Mutate `routes` between syncs to simulate changes."""

    def __init__(self) -> None:
        self.routes: dict[str, object] = {
            "/api/v1/users/self/courses": load("courses.json"),
            "/api/v1/users/self/groups": load("groups.json"),
            "/api/v1/planner/items": load("planner.json"),
            "/api/v1/announcements": load("announcements.json"),
            "/api/v1/groups/900/discussion_topics": load("group_announcements.json"),
            "/api/v1/courses/101/assignments": load("assignments_101.json"),
            "/api/v1/courses/102/assignments": [],
        }
        self.calls: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        body = self.routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"errors": [{"message": "not found"}]})
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=copy.deepcopy(body))

    def client(self, cfg: Config | None = None) -> CanvasClient:
        return CanvasClient(BASE, "test-token", transport=httpx.MockTransport(self.handler), sleep=lambda s: None)


@pytest.fixture
def canvas() -> FakeCanvas:
    return FakeCanvas()
