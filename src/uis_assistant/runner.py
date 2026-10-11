"""Run one sync: lock, fetch a snapshot, apply it atomically, record the outcome."""

from __future__ import annotations

import fcntl
import logging
import traceback
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from uis_assistant.canvas.client import AuthError, CanvasClient, CanvasError
from uis_assistant.canvas.sync import fetch_snapshot
from uis_assistant.config import Config, ConfigError
from uis_assistant.store import Store

log = logging.getLogger(__name__)


@dataclass
class SyncOutcome:
    ok: bool
    skipped: bool = False
    counts: dict | None = None
    error_kind: str | None = None
    error: str | None = None
    warnings: list[str] | None = None

    def summary(self) -> str:
        if self.skipped:
            return "Sync skipped: another sync is running"
        if not self.ok:
            return f"Sync failed ({self.error_kind}): {self.error}"
        c = self.counts or {}
        s = (f"Sync ok: {c.get('new', 0)} new, {c.get('updated', 0)} updated, {c.get('removed', 0)} removed,"
             f" {c.get('unchanged', 0)} unchanged ({c.get('requests', 0)} requests)")
        if c.get("baseline"):
            s += " [baseline: first sync, no notifications]"
        if self.warnings:
            s += "\nWarnings:\n" + "\n".join(f"  - {w}" for w in self.warnings)
        return s


@contextmanager
def _sync_lock(cfg: Config):
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.data_dir / "sync.lock", "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def run_sync(cfg: Config, store: Store, client_factory: Callable[[Config], CanvasClient] | None = None) -> SyncOutcome:
    client_factory = client_factory or (lambda c: CanvasClient(c.base_url, c.token()))
    with _sync_lock(cfg) as acquired:
        if not acquired:
            return SyncOutcome(ok=False, skipped=True)
        run_id = store.start_run()
        try:
            with client_factory(cfg) as client:
                snap = fetch_snapshot(client, cfg)
            res = store.apply_snapshot(snap.items.values(), snap.window_start, snap.window_end,
                                       snap.incomplete_contexts)
            store.set_state("contexts", snap.contexts)
            store.set_state("window", [snap.window_start.isoformat(), snap.window_end.isoformat()])
            counts = {**res.as_dict(), "items": len(snap.items), "requests": snap.requests}
            store.finish_run(run_id, "ok", counts=counts, error="\n".join(snap.warnings) or None)
            return SyncOutcome(ok=True, counts=counts, warnings=snap.warnings)
        except AuthError as e:
            kind, msg = "auth", str(e)
        except ConfigError as e:
            kind, msg = "config", str(e)
        except CanvasError as e:
            kind, msg = "canvas", str(e)
        except Exception as e:  # anything else is a bug or an API change; still record it
            log.debug(traceback.format_exc())
            kind, msg = "crash", f"{type(e).__name__}: {e}"
        store.finish_run(run_id, "failed", error_kind=kind, error=msg)
        return SyncOutcome(ok=False, error_kind=kind, error=msg)


def recently_synced(store: Store, within: timedelta) -> bool:
    runs = store.recent_runs(1)
    if not runs:
        return False
    started = datetime.fromisoformat(runs[0]["started_at"])
    return datetime.now(timezone.utc) - started < within
