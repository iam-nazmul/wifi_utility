"""SQLite storage (SPEC §4.4)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .detector import Baseline, MotionEvent
from .sources.base import LinkKey, Sample

SAMPLE_RETENTION_SECS = 24 * 3600
DAY = 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts REAL NOT NULL, rssi INTEGER, noise INTEGER, tx_rate REAL, channel INTEGER, bssid TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT NOT NULL, start_ts REAL NOT NULL,
    end_ts REAL, peak_score REAL, confidence TEXT
);
CREATE TABLE IF NOT EXISTS occupancy (
    zone_id TEXT NOT NULL, bucket_start INTEGER NOT NULL, occupied INTEGER NOT NULL,
    PRIMARY KEY (zone_id, bucket_start)
);
CREATE TABLE IF NOT EXISTS baselines (
    bssid TEXT NOT NULL, channel INTEGER NOT NULL, created_ts REAL NOT NULL, stats_json TEXT
);
CREATE TABLE IF NOT EXISTS policy (
    version INTEGER PRIMARY KEY, text_hash TEXT NOT NULL, published_ts REAL NOT NULL,
    active INTEGER NOT NULL, effective_ts REAL NOT NULL, text TEXT NOT NULL,
    notice_confirmed_ts REAL
);
CREATE TABLE IF NOT EXISTS acknowledgements (
    policy_version INTEGER NOT NULL, ack_ts REAL NOT NULL, ack_by TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
    BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


class StoreError(RuntimeError):
    pass


def default_db_path() -> Path:
    from .config import data_dir

    return data_dir() / "data.db"


class Store:
    def __init__(self, path: str | Path | None = None, workplace: bool = False):
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.workplace = workplace
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, params)
            self._db.commit()
            return cur

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    # samples (home debug capture only)
    def add_sample(self, s: Sample) -> None:
        if self.workplace:
            raise StoreError("raw samples are never stored in workplace mode")
        self._exec(
            "INSERT INTO samples VALUES (?,?,?,?,?,?)",
            (s.ts, s.rssi_dbm, s.noise_dbm, s.tx_rate_mbps, s.channel, s.bssid),
        )

    # events (home)
    def start_event(self, mode: str, e: MotionEvent) -> int:
        if self.workplace:
            raise StoreError("motion events are not stored in workplace mode")
        cur = self._exec(
            "INSERT INTO events (mode, start_ts, peak_score, confidence) VALUES (?,?,?,?)",
            (mode, e.start_ts, e.peak_score, e.confidence),
        )
        return int(cur.lastrowid)

    def end_event(self, event_id: int, e: MotionEvent) -> None:
        self._exec(
            "UPDATE events SET end_ts=?, peak_score=?, confidence=? WHERE id=?",
            (e.end_ts, e.peak_score, e.confidence, event_id),
        )

    def events(self, since: float = 0, limit: int = 200) -> list[dict]:
        return self._all(
            "SELECT * FROM events WHERE start_ts >= ? ORDER BY start_ts DESC LIMIT ?",
            (since, limit),
        )

    # occupancy (workplace)
    def mark_occupancy(self, zone_id: str, bucket_start: int, occupied: bool) -> None:
        # Once a bucket is occupied it stays occupied.
        self._exec(
            "INSERT INTO occupancy VALUES (?,?,?) ON CONFLICT(zone_id, bucket_start) "
            "DO UPDATE SET occupied = MAX(occupied, excluded.occupied)",
            (zone_id, int(bucket_start), int(bool(occupied))),
        )

    def occupancy(self, since: float = 0, zone_id: str | None = None) -> list[dict]:
        sql = "SELECT zone_id, bucket_start, occupied FROM occupancy WHERE bucket_start >= ?"
        params: tuple = (since,)
        if zone_id:
            sql += " AND zone_id = ?"
            params += (zone_id,)
        return self._all(sql + " ORDER BY zone_id, bucket_start", params)

    # baselines
    def save_baseline(self, b: Baseline) -> None:
        self._exec(
            "INSERT INTO baselines VALUES (?,?,?,?)",
            (b.bssid, b.channel, b.created_ts, b.stats_json()),
        )

    def get_baseline(self, link: LinkKey) -> Baseline | None:
        rows = self._all(
            "SELECT * FROM baselines WHERE bssid=? AND channel=? ORDER BY created_ts DESC LIMIT 1",
            (link.bssid, link.channel),
        )
        if not rows:
            return None
        r = rows[0]
        return Baseline.from_row(r["bssid"], r["channel"], r["created_ts"], r["stats_json"])

    def latest_baseline(self) -> Baseline | None:
        rows = self._all("SELECT * FROM baselines ORDER BY created_ts DESC LIMIT 1")
        if not rows:
            return None
        r = rows[0]
        return Baseline.from_row(r["bssid"], r["channel"], r["created_ts"], r["stats_json"])

    # policy (workplace)
    def add_policy(
        self, text_hash: str, published_ts: float, effective_ts: float, text: str
    ) -> int:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(MAX(version), 0) FROM policy").fetchone()
            version = int(row[0]) + 1
            self._db.execute("UPDATE policy SET active = 0")
            self._db.execute(
                "INSERT INTO policy (version, text_hash, published_ts, active, effective_ts, text)"
                " VALUES (?,?,?,1,?,?)",
                (version, text_hash, published_ts, effective_ts, text),
            )
            self._db.commit()
        return version

    def active_policy(self) -> dict | None:
        rows = self._all("SELECT * FROM policy WHERE active = 1 ORDER BY version DESC LIMIT 1")
        return rows[0] if rows else None

    def policies(self) -> list[dict]:
        return self._all(
            "SELECT version, text_hash, published_ts, active, effective_ts, notice_confirmed_ts "
            "FROM policy ORDER BY version"
        )

    def confirm_notice(self, version: int, ts: float) -> None:
        self._exec("UPDATE policy SET notice_confirmed_ts = ? WHERE version = ?", (ts, version))

    def add_ack(self, version: int, ts: float, ack_by: str | None) -> None:
        self._exec("INSERT INTO acknowledgements VALUES (?,?,?)", (version, ts, ack_by))

    def ack_count(self, version: int) -> int:
        rows = self._all(
            "SELECT COUNT(*) AS n FROM acknowledgements WHERE policy_version = ?", (version,)
        )
        return int(rows[0]["n"])

    # audit (append-only, enforced by triggers)
    def audit(self, ts: float, actor: str, action: str, detail: str = "") -> None:
        self._exec("INSERT INTO audit VALUES (?,?,?,?)", (ts, actor, action, detail))

    def audit_log(self, limit: int = 200) -> list[dict]:
        return self._all("SELECT * FROM audit ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,))

    def last_audit(self, action: str) -> dict | None:
        rows = self._all(
            "SELECT * FROM audit WHERE action = ? ORDER BY ts DESC, rowid DESC LIMIT 1", (action,)
        )
        return rows[0] if rows else None

    # key/value state shared between CLI and the running engine (e.g. armed)
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        rows = self._all("SELECT value FROM kv WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def kv_set(self, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # retention (SPEC §4.4, §6.4)
    def prune(self, now: float, occupancy_retention_days: int) -> None:
        self._exec("DELETE FROM samples WHERE ts < ?", (now - SAMPLE_RETENTION_SECS,))
        self._exec(
            "DELETE FROM occupancy WHERE bucket_start < ?", (now - occupancy_retention_days * DAY,)
        )
