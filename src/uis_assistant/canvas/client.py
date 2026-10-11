"""Minimal Canvas REST client: bearer auth, full pagination, polite retries."""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

log = logging.getLogger(__name__)

NEXT_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')
MAX_PAGES = 200  # guard against a pagination loop
LOW_RATE_LIMIT = 100.0  # Canvas' bucket starts at 700


class CanvasError(Exception):
    """Base class for anything that should fail a sync loudly."""


class AuthError(CanvasError):
    """The token was rejected (expired, revoked, or wrong)."""


class ForbiddenError(CanvasError):
    """Authenticated, but not allowed to read this resource."""


class CanvasFormatError(CanvasError):
    """The response doesn't have the shape we expect (API change, HTML error page, ...)."""


def decode_json(text: str) -> Any:
    # Cookie-authenticated JSON can come prefixed with a JSON-hijacking guard
    try:
        return json.loads(text.removeprefix("while(1);"))
    except json.JSONDecodeError as e:
        raise CanvasFormatError(f"Response is not JSON: {text[:200]!r}") from e


Params = dict[str, Any] | list[tuple[str, Any]] | None


class CanvasClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._sleep = sleep
        self._max_retries = max_retries
        self.requests_made = 0
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "CanvasClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get_json(self, path: str, params: Params = None) -> Any:
        return decode_json(self._request(path, params).text)

    def get_all(self, path: str, params: Params = None) -> list[dict]:
        """GET a paginated list endpoint, following every rel="next" link."""
        out: list[dict] = []
        url: str | None = path
        for _ in range(MAX_PAGES):
            resp = self._request(url, params)
            page = decode_json(resp.text)
            if isinstance(page, dict) and "errors" in page:
                raise CanvasFormatError(f"{path}: Canvas returned errors: {page['errors']}")
            if not isinstance(page, list):
                raise CanvasFormatError(f"{path}: expected a list, got {type(page).__name__}")
            out.extend(page)
            m = NEXT_LINK_RE.search(resp.headers.get("link", ""))
            if not m:
                return out
            url, params = m.group(1), None  # the next link already carries the query
        raise CanvasFormatError(f"{path}: more than {MAX_PAGES} pages, giving up")

    def _request(self, url: str, params: Params) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            last = attempt == self._max_retries
            try:
                resp = self._http.get(url, params=params)
            except httpx.TransportError as e:
                if last:
                    raise CanvasError(f"Network error for {url}: {e}") from e
                self._backoff(attempt, f"network error: {e}")
                continue
            self.requests_made += 1

            if resp.status_code == 401:
                if _is_unauthenticated(resp):
                    raise AuthError("Canvas rejected the access token (expired or revoked?)")
                raise ForbiddenError(f"Not authorized to read {_path(url)}")
            if (resp.status_code == 403 and "rate limit" in resp.text.lower()) or resp.status_code == 429:
                if last:
                    raise CanvasError(f"Rate limited on {url} after {attempt + 1} attempts")
                self._backoff(attempt, "rate limited")
                continue
            if resp.status_code == 403:
                raise ForbiddenError(f"Forbidden: {_path(url)}")
            if resp.status_code >= 500:
                if last:
                    raise CanvasError(f"Canvas {resp.status_code} for {url}")
                self._backoff(attempt, f"HTTP {resp.status_code}")
                continue
            if resp.status_code >= 300:
                # Redirects usually mean a login page, i.e. not an API response
                raise CanvasFormatError(f"Unexpected HTTP {resp.status_code} for {_path(url)}")

            self._respect_rate_limit(resp)
            return resp
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int, why: str) -> None:
        delay = min(60.0, 2.0 ** (attempt + 1))
        log.warning("Canvas %s, retrying in %.0fs", why, delay)
        self._sleep(delay)

    def _respect_rate_limit(self, resp: httpx.Response) -> None:
        try:
            remaining = float(resp.headers.get("x-rate-limit-remaining", "inf"))
        except ValueError:
            return
        if remaining < LOW_RATE_LIMIT:
            log.info("Canvas rate limit low (%.0f), slowing down", remaining)
            self._sleep(2.0)


def _is_unauthenticated(resp: httpx.Response) -> bool:
    # Canvas uses 401 for both bad tokens ("unauthenticated") and permission errors ("unauthorized")
    try:
        body = resp.json()
    except ValueError:
        return True
    text = json.dumps(body).lower()
    return "unauthenticated" in text or "invalid access token" in text


def _path(url: str) -> str:
    return str(httpx.URL(url).path) if "://" in url else url.split("?")[0]
