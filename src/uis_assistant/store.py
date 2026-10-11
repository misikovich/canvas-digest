"""SQLite store: current items, a change log, consumer cursors, and sync history."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from uis_assistant.schema import HASH_FIELDS, Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    context      TEXT,
    anchor       TEXT,
    json         TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    removed_at   TEXT
);
CREATE INDEX IF NOT EXISTS items_anchor ON items(anchor);
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(id UNINDEXED, title, body, course);
CREATE TABLE IF NOT EXISTS changes (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (type IN ('new', 'updated', 'removed')),
    diff        TEXT,
    detected_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cursors (
    consumer TEXT PRIMARY KEY,
    last_seq INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS reminders_sent (
    item_id TEXT NOT NULL,
    rule    TEXT NOT NULL,
    due_at  TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (item_id, rule, due_at)
);
CREATE TABLE IF NOT EXISTS sync_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,          -- running | ok | failed
    error_kind  TEXT,                   -- auth | canvas | config | crash
    error       TEXT,
    counts      TEXT
);
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Change:
    seq: int
    item_id: str
    type: str
    diff: dict | None
    detected_at: datetime
    item: Item


@dataclass
class ApplyResult:
    new: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    baseline: bool = False

    def as_dict(self) -> dict:
        return {"new": self.new, "updated": self.updated, "removed": self.removed,
                "unchanged": self.unchanged, "baseline": self.baseline}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


class Store:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    # ---- snapshot ingestion -------------------------------------------------

    def apply_snapshot(
        self,
        items: Iterable[Item],
        window_start: datetime,
        window_end: datetime,
        incomplete_contexts: set[str] = frozenset(),
        now: datetime | None = None,
    ) -> ApplyResult:
        """Upsert a complete snapshot atomically and log what changed.

        The first snapshot ever is a baseline: items are stored without change
        records, so the first notification isn't the whole semester.
        """
        now_s = _iso(now) if now else _now()
        res = ApplyResult()
        with self.transaction() as db:
            existing = {r["id"]: r for r in db.execute("SELECT * FROM items")}
            res.baseline = not existing
            seen: set[str] = set()

            for item in items:
                seen.add(item.id)
                h = item.content_hash()
                data = json.dumps(item.to_dict(), ensure_ascii=False)
                row = existing.get(item.id)
                if row is None:
                    db.execute(
                        "INSERT INTO items (id, kind, context, anchor, json, content_hash, first_seen, last_seen)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (item.id, item.kind, item.context, _iso(item.anchor), data, h, now_s, now_s),
                    )
                    if not res.baseline:
                        self._log(db, item.id, "new", None, now_s)
                    res.new += 1
                else:
                    reappeared = row["removed_at"] is not None
                    changed = row["content_hash"] != h
                    db.execute(
                        "UPDATE items SET kind=?, context=?, anchor=?, json=?, content_hash=?, last_seen=?, removed_at=NULL"
                        " WHERE id=?",
                        (item.kind, item.context, _iso(item.anchor), data, h, now_s, item.id),
                    )
                    if reappeared:
                        self._log(db, item.id, "new", {"reappeared": True}, now_s)
                        res.new += 1
                    elif changed:
                        old = Item.from_dict(json.loads(row["json"]))
                        self._log(db, item.id, "updated", diff_items(old, item), now_s)
                        res.updated += 1
                    else:
                        res.unchanged += 1
                self._index(db, item)

            # Missing from a complete snapshot -> removed, but only within the window
            # we actually fetched, and only for contexts that were fetched fully.
            for item_id, row in existing.items():
                if item_id in seen or row["removed_at"] is not None:
                    continue
                if row["context"] in incomplete_contexts:
                    continue
                anchor = datetime.fromisoformat(row["anchor"]) if row["anchor"] else None
                if anchor is not None and not (window_start <= anchor < window_end):
                    continue
                db.execute("UPDATE items SET removed_at=? WHERE id=?", (now_s, item_id))
                self._log(db, item_id, "removed", None, now_s)
                res.removed += 1
        return res

    def _log(self, db, item_id: str, type_: str, diff: dict | None, now_s: str) -> None:
        db.execute(
            "INSERT INTO changes (item_id, type, diff, detected_at) VALUES (?, ?, ?, ?)",
            (item_id, type_, json.dumps(diff, ensure_ascii=False) if diff else None, now_s),
        )

    def _index(self, db, item: Item) -> None:
        db.execute("DELETE FROM items_fts WHERE id=?", (item.id,))
        course = " ".join(x for x in (item.course_code, item.course_name) if x)
        db.execute("INSERT INTO items_fts (id, title, body, course) VALUES (?, ?, ?, ?)",
                   (item.id, item.title, item.body_md or "", course))

    # ---- reads --------------------------------------------------------------

    def get(self, item_id: str) -> tuple[Item, str | None] | None:
        row = self.db.execute("SELECT json, removed_at FROM items WHERE id=?", (item_id,)).fetchone()
        return (Item.from_dict(json.loads(row["json"])), row["removed_at"]) if row else None

    def items(self, kinds: Iterable[str] | None = None, include_removed: bool = False) -> list[Item]:
        sql, args = "SELECT json FROM items WHERE 1=1", []
        if not include_removed:
            sql += " AND removed_at IS NULL"
        if kinds:
            kinds = list(kinds)
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        return [Item.from_dict(json.loads(r["json"])) for r in self.db.execute(sql + " ORDER BY anchor", args)]

    def search(self, query: str, limit: int = 10) -> list[Item]:
        # Quote every term so user input can't be parsed as FTS syntax
        terms = [t.replace('"', '""') for t in query.split()]
        if not terms:
            return []
        fts = " ".join(f'"{t}"*' for t in terms)
        rows = self.db.execute(
            "SELECT i.json FROM items_fts f JOIN items i ON i.id = f.id"
            " WHERE items_fts MATCH ? AND i.removed_at IS NULL ORDER BY rank LIMIT ?",
            (fts, limit),
        )
        return [Item.from_dict(json.loads(r["json"])) for r in rows]

    def changes_since(self, seq: int, limit: int | None = None) -> list[Change]:
        sql = ("SELECT c.*, i.json FROM changes c JOIN items i ON i.id = c.item_id"
               " WHERE c.seq > ? ORDER BY c.seq")
        args: list = [seq]
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        return [
            Change(r["seq"], r["item_id"], r["type"], json.loads(r["diff"]) if r["diff"] else None,
                   datetime.fromisoformat(r["detected_at"]), Item.from_dict(json.loads(r["json"])))
            for r in self.db.execute(sql, args)
        ]

    def changes_after(self, since: datetime) -> list[Change]:
        row = self.db.execute("SELECT MIN(seq) - 1 AS s FROM changes WHERE detected_at >= ?", (_iso(since),)).fetchone()
        return [] if row["s"] is None else self.changes_since(row["s"])

    def latest_seq(self) -> int:
        return self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM changes").fetchone()[0]

    # ---- cursors / reminders / state ---------------------------------------

    def cursor(self, consumer: str) -> int:
        row = self.db.execute("SELECT last_seq FROM cursors WHERE consumer=?", (consumer,)).fetchone()
        return row["last_seq"] if row else 0

    def set_cursor(self, consumer: str, seq: int) -> None:
        self.db.execute(
            "INSERT INTO cursors (consumer, last_seq) VALUES (?, ?)"
            " ON CONFLICT(consumer) DO UPDATE SET last_seq=excluded.last_seq",
            (consumer, seq),
        )

    def reminder_sent(self, item_id: str, rule: str, due: datetime) -> bool:
        return self.db.execute(
            "SELECT 1 FROM reminders_sent WHERE item_id=? AND rule=? AND due_at=?", (item_id, rule, _iso(due))
        ).fetchone() is not None

    def mark_reminder(self, item_id: str, rule: str, due: datetime) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO reminders_sent (item_id, rule, due_at, sent_at) VALUES (?, ?, ?, ?)",
            (item_id, rule, _iso(due), _now()),
        )

    def get_state(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_state(self, key: str, value) -> None:
        self.db.execute(
            "INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    # ---- sync runs ----------------------------------------------------------

    def start_run(self) -> int:
        return self.db.execute("INSERT INTO sync_runs (started_at, status) VALUES (?, 'running')", (_now(),)).lastrowid

    def finish_run(self, run_id: int, status: str, *, counts: dict | None = None,
                   error_kind: str | None = None, error: str | None = None) -> None:
        self.db.execute(
            "UPDATE sync_runs SET finished_at=?, status=?, counts=?, error_kind=?, error=? WHERE id=?",
            (_now(), status, json.dumps(counts) if counts else None, error_kind, error, run_id),
        )

    def recent_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def last_success(self) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM sync_runs WHERE status='ok' ORDER BY id DESC LIMIT 1").fetchone()

    def consecutive_failures(self) -> list[sqlite3.Row]:
        out = []
        for r in self.recent_runs(50):
            if r["status"] == "ok":
                break
            if r["status"] == "failed":
                out.append(r)
        return out

    def counts(self) -> dict[str, int]:
        return {r["kind"]: r["n"] for r in self.db.execute(
            "SELECT kind, COUNT(*) AS n FROM items WHERE removed_at IS NULL GROUP BY kind")}


def diff_items(old: Item, new: Item) -> dict:
    o, n = old.to_dict(), new.to_dict()
    diff = {}
    for k in HASH_FIELDS:
        if o[k] == n[k]:
            continue
        # Bodies can be long; record only that they changed
        diff[k] = "changed" if k == "body_md" else [o[k], n[k]]
    return diff
