import httpx
import pytest

from uis_assistant.canvas.client import AuthError, CanvasClient, CanvasError, CanvasFormatError, ForbiddenError

BASE = "https://canvas.example.edu"


def client(handler):
    return CanvasClient(BASE, "tok", transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_follows_next_links_and_strips_guard():
    def handler(req):
        assert req.headers["authorization"] == "Bearer tok"
        if req.url.params.get("page") == "2":
            return httpx.Response(200, text='while(1);[{"id": 2}]')
        return httpx.Response(200, json=[{"id": 1}],
                              headers={"link": f'<{BASE}/api/v1/x?page=2&per_page=1>; rel="next", <{BASE}/api/v1/x?page=1>; rel="first"'})
    assert client(handler).get_all("/api/v1/x", {"per_page": 1}) == [{"id": 1}, {"id": 2}]


def test_bad_token_is_auth_error():
    h = lambda req: httpx.Response(401, json={"errors": [{"message": "Invalid access token."}]})  # noqa: E731
    with pytest.raises(AuthError):
        client(h).get_all("/api/v1/x")


def test_permission_denied_is_forbidden_not_auth():
    h = lambda req: httpx.Response(401, json={"status": "unauthorized", "errors": [{"message": "user not authorized to perform that action"}]})  # noqa: E731
    with pytest.raises(ForbiddenError):
        client(h).get_all("/api/v1/x")


def test_rate_limit_is_retried():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(403, text="403 Forbidden (Rate Limit Exceeded)")
        return httpx.Response(200, json=[])
    assert client(handler).get_all("/api/v1/x") == []
    assert len(calls) == 3


def test_server_errors_give_up_eventually():
    with pytest.raises(CanvasError):
        client(lambda req: httpx.Response(503, text="down")).get_all("/api/v1/x")


def test_unexpected_shapes_fail_loudly():
    with pytest.raises(CanvasFormatError):
        client(lambda req: httpx.Response(200, json={"oops": 1})).get_all("/api/v1/x")
    with pytest.raises(CanvasFormatError):
        client(lambda req: httpx.Response(200, text="<html>login</html>")).get_all("/api/v1/x")
    with pytest.raises(CanvasFormatError):
        client(lambda req: httpx.Response(302, headers={"location": "/login"})).get_all("/api/v1/x")
