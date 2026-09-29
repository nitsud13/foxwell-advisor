"""SQLite store for advised changes and performance snapshots (the outcome loop).

Everything here comes from what the buyer's own browser can see on the Ads Manager page:
no API tokens, no member data beyond the metrics visible in their own account.

Tables
  events     one row per advised change: what changed, what Jev said, metrics at that moment
  snapshots  one row per visible entity per page load: the metrics columns as shown
  outcomes   one row per event once judged (7+ days later): before/after metrics and Jev's verdict
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "advisor.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  account TEXT, level TEXT, entity_id TEXT, entity_name TEXT,
  field TEXT, old TEXT, new TEXT,
  topic TEXT, risk TEXT, interrupt REAL, verdict TEXT,
  metrics TEXT,
  status TEXT DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  account TEXT, level TEXT, entity_id TEXT, entity_name TEXT,
  date_range TEXT,
  metrics TEXT
);
CREATE INDEX IF NOT EXISTS snap_entity ON snapshots(account, level, entity_name, ts);
CREATE TABLE IF NOT EXISTS outcomes (
  event_id INTEGER PRIMARY KEY,
  judged_ts REAL NOT NULL,
  before TEXT, after TEXT,
  held TEXT, confidence REAL, probabilities TEXT, note TEXT
);
"""


class Store:
    def __init__(self, path: Path | None = None):
        self.path = path or DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # older databases: add the status column if missing
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(events)")}
        if "status" not in cols:
            self.conn.execute("ALTER TABLE events ADD COLUMN status TEXT DEFAULT 'pending'"); self.conn.commit()

    # --- writes ---------------------------------------------------------
    def add_event(self, ev: dict, result: dict) -> int:
        ent = ev.get("entity") or {}
        cur = self.conn.execute(
            "INSERT INTO events(ts,account,level,entity_id,entity_name,field,old,new,topic,risk,interrupt,verdict,metrics)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), ent.get("account"), ent.get("level"), ent.get("id"), ent.get("name"),
             ev.get("field"), _s(ev.get("old")), _s(ev.get("new")),
             result.get("topic"), result.get("risk"), result.get("interrupt"),
             (result.get("verdict") or {}).get("verdict"), json.dumps(ent.get("metrics") or {})),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def add_snapshots(self, account: str, level: str, date_range: str, rows: list[dict]) -> int:
        now = time.time()
        self.conn.executemany(
            "INSERT INTO snapshots(ts,account,level,entity_id,entity_name,date_range,metrics) VALUES(?,?,?,?,?,?,?)",
            [(now, account, level, r.get("id"), r.get("name"), date_range, json.dumps(r.get("metrics") or {})) for r in rows],
        )
        self.conn.commit()
        return len(rows)

    def set_status(self, event_ids: list[int], status: str) -> int:
        """published | discarded | pending. Only pending events change, so a late click cannot flip a decided one."""
        if not event_ids:
            return 0
        q = ",".join("?" * len(event_ids))
        cur = self.conn.execute(f"UPDATE events SET status=? WHERE id IN ({q}) AND status='pending'", (status, *event_ids))
        self.conn.commit()
        return cur.rowcount

    def add_outcome(self, event_id: int, before: dict | None, after: dict | None, judged: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO outcomes(event_id,judged_ts,before,after,held,confidence,probabilities,note) VALUES(?,?,?,?,?,?,?,?)",
            (event_id, time.time(), json.dumps(before), json.dumps(after), judged.get("held"),
             judged.get("confidence"), json.dumps(judged.get("probabilities")), judged.get("note")),
        )
        self.conn.commit()

    # --- reads ----------------------------------------------------------
    def events_awaiting_outcome(self, min_age_days: float = 7.0, statuses: tuple = ("published",)) -> list[dict]:
        cutoff = time.time() - min_age_days * 86400
        q = ",".join("?" * len(statuses))
        rows = self.conn.execute(
            f"SELECT e.* FROM events e LEFT JOIN outcomes o ON o.event_id = e.id WHERE o.event_id IS NULL AND e.ts <= ? AND e.status IN ({q}) ORDER BY e.ts",
            (cutoff, *statuses),
        ).fetchall()
        return [dict(r) for r in rows]

    def status_counts(self) -> dict:
        return {r["status"]: r["n"] for r in self.conn.execute("SELECT status, COUNT(*) AS n FROM events GROUP BY status")}

    def snapshot_near(self, account: str, level: str, name: str, ts: float, after: bool, min_gap_days: float = 0.0) -> dict | None:
        if after:
            row = self.conn.execute(
                "SELECT * FROM snapshots WHERE account=? AND level=? AND entity_name=? AND ts >= ? ORDER BY ts ASC LIMIT 1",
                (account, level, name, ts + min_gap_days * 86400),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM snapshots WHERE account=? AND level=? AND entity_name=? AND ts <= ? ORDER BY ts DESC LIMIT 1",
                (account, level, name, ts),
            ).fetchone()
        return dict(row) if row else None

    def recent_events(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT e.*, o.held, o.confidence AS outcome_confidence, o.before, o.after FROM events e LEFT JOIN outcomes o ON o.event_id=e.id ORDER BY e.ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def outcome_summary(self) -> dict:
        rows = self.conn.execute("SELECT held, COUNT(*) AS n FROM outcomes GROUP BY held").fetchall()
        return {r["held"]: r["n"] for r in rows}

    def counts(self) -> dict:
        c = {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("events", "snapshots", "outcomes")}
        c["events_by_status"] = self.status_counts()
        return c


def _s(x):
    return None if x is None else str(x)
