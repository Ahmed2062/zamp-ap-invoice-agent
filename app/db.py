"""SQLite persistence: runs, stage events (live view), AP ledger (PO consumption), reviews."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  source TEXT NOT NULL,
  sample_id TEXT,
  filename TEXT NOT NULL,
  file_sha256 TEXT NOT NULL,
  file_path TEXT NOT NULL,
  file_size INTEGER,
  status TEXT NOT NULL,
  current_stage TEXT,
  decision TEXT,
  final_decision TEXT,
  decided_by TEXT,
  headline TEXT,
  queue TEXT,
  priority TEXT,
  vendor_id TEXT,
  vendor_name TEXT,
  vendor_gstin TEXT,
  invoice_number TEXT,
  invoice_number_norm TEXT,
  invoice_date TEXT,
  po_number TEXT,
  po_inferred INTEGER DEFAULT 0,
  subtotal REAL,
  total REAL,
  currency TEXT,
  read_method TEXT,
  ai_used INTEGER DEFAULT 0,
  duration_ms INTEGER,
  seeded INTEGER DEFAULT 0,
  result_json TEXT,
  error TEXT
);
CREATE TABLE IF NOT EXISTS run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  ts TEXT NOT NULL,
  stage TEXT,
  type TEXT NOT NULL,
  status TEXT,
  message TEXT,
  data_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON run_events(run_id, seq);
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  vendor_id TEXT,
  po_number TEXT,
  invoice_number TEXT,
  invoice_number_norm TEXT,
  invoice_date TEXT,
  amount_ex_tax REAL,
  total REAL,
  allocations_json TEXT,
  posted_at TEXT NOT NULL,
  posted_by TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  reviewer TEXT,
  note TEXT,
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_run ON ledger(run_id);
"""

RUN_COLUMNS = {
    "completed_at", "status", "current_stage", "decision", "final_decision", "decided_by", "headline", "queue",
    "priority", "vendor_id", "vendor_name", "vendor_gstin", "invoice_number", "invoice_number_norm", "invoice_date",
    "po_number", "po_inferred", "subtotal", "total", "currency", "read_method", "ai_used", "duration_ms",
    "result_json", "error", "created_at",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._seq: dict[int, int] = {}

    # ---------------------------------------------------------------- generic
    def _exec(self, sql: str, params=()):
        with self._lock:
            return self._conn.execute(sql, params)

    def _all(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params=()) -> dict | None:
        with self._lock:
            r = self._conn.execute(sql, params).fetchone()
            return dict(r) if r else None

    @property
    def lock(self):
        return self._lock

    def reset(self) -> None:
        with self._lock:
            self._conn.executescript("DELETE FROM runs; DELETE FROM run_events; DELETE FROM ledger; DELETE FROM reviews;"
                                     "DELETE FROM sqlite_sequence;")
            self._seq.clear()

    # ---------------------------------------------------------------- runs
    def create_run(self, *, source: str, filename: str, sha256: str, file_path: str, file_size: int,
                   sample_id: str | None = None, seeded: bool = False, created_at: str | None = None) -> int:
        cur = self._exec(
            "INSERT INTO runs (created_at, source, sample_id, filename, file_sha256, file_path, file_size, status, seeded)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (created_at or now_iso(), source, sample_id, filename, sha256, file_path, file_size, "running",
             1 if seeded else 0))
        return int(cur.lastrowid)

    def update_run(self, run_id: int, **fields) -> None:
        bad = set(fields) - RUN_COLUMNS
        if bad:
            raise ValueError(f"unknown run columns: {bad}")
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE runs SET {cols} WHERE id = ?", (*fields.values(), run_id))

    def count_active(self) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM runs WHERE status = 'running'")
        return int(row["n"]) if row else 0

    def get_run(self, run_id: int) -> dict | None:
        row = self._one("SELECT * FROM runs WHERE id = ?", (run_id,))
        if row and row.get("result_json"):
            row["result"] = json.loads(row.pop("result_json"))
        elif row:
            row.pop("result_json", None)
            row["result"] = None
        return row

    def list_runs(self, decision: str | None = None, q: str | None = None, limit: int = 200) -> list[dict]:
        sql = ("SELECT id, created_at, completed_at, source, sample_id, filename, status, current_stage, decision, "
               "final_decision, decided_by, headline, queue, priority, vendor_id, vendor_name, invoice_number, "
               "invoice_date, po_number, po_inferred, total, currency, read_method, ai_used, duration_ms, seeded "
               "FROM runs")
        where, params = [], []
        if decision:
            where.append("COALESCE(final_decision, decision) = ?")
            params.append(decision)
        if q:
            where.append("(vendor_name LIKE ? OR invoice_number LIKE ? OR po_number LIKE ? OR filename LIKE ?)")
            params += [f"%{q}%"] * 4
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        return self._all(sql, params)

    def runs_with_hash(self, sha256: str, exclude_id: int) -> list[dict]:
        return self._all("SELECT id, filename, invoice_number, decision, final_decision, created_at FROM runs "
                         "WHERE file_sha256 = ? AND id != ? AND status = 'completed'", (sha256, exclude_id))

    def runs_for_vendor(self, vendor_key: str, exclude_id: int) -> list[dict]:
        return self._all(
            "SELECT id, filename, invoice_number, invoice_number_norm, invoice_date, total, decision, final_decision, "
            "created_at FROM runs WHERE (vendor_id = ? OR vendor_gstin = ?) AND id != ? AND status = 'completed'",
            (vendor_key, vendor_key, exclude_id))

    # ---------------------------------------------------------------- events
    def add_event(self, run_id: int, type_: str, stage: str | None = None, status: str | None = None,
                  message: str | None = None, data: dict | None = None, ts: str | None = None) -> int:
        with self._lock:
            seq = self._seq.get(run_id)
            if seq is None:
                r = self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM run_events WHERE run_id = ?", (run_id,))
                seq = r.fetchone()[0]
            seq += 1
            self._seq[run_id] = seq
            self._conn.execute(
                "INSERT INTO run_events (run_id, seq, ts, stage, type, status, message, data_json) VALUES (?,?,?,?,?,?,?,?)",
                (run_id, seq, ts or now_iso(), stage, type_, status, message,
                 json.dumps(data, default=str) if data is not None else None))
            return seq

    def get_events(self, run_id: int, after: int = 0) -> list[dict]:
        rows = self._all("SELECT seq, ts, stage, type, status, message, data_json FROM run_events "
                         "WHERE run_id = ? AND seq > ? ORDER BY seq", (run_id, after))
        for r in rows:
            r["data"] = json.loads(r.pop("data_json")) if r.get("data_json") else None
        return rows

    # ---------------------------------------------------------------- ledger
    def post_ledger(self, *, run_id: int, vendor_id: str | None, po_number: str | None, invoice_number: str | None,
                    invoice_number_norm: str | None, invoice_date: str | None, amount_ex_tax: float, total: float,
                    allocations: list[dict], posted_by: str, posted_at: str | None = None) -> None:
        self._exec(
            "INSERT INTO ledger (run_id, vendor_id, po_number, invoice_number, invoice_number_norm, invoice_date, "
            "amount_ex_tax, total, allocations_json, posted_at, posted_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, vendor_id, po_number, invoice_number, invoice_number_norm, invoice_date, amount_ex_tax, total,
             json.dumps(allocations), posted_at or now_iso(), posted_by))

    def ledger_for_po(self, po_number: str) -> list[dict]:
        rows = self._all("SELECT * FROM ledger WHERE po_number = ? ORDER BY posted_at", (po_number,))
        for r in rows:
            r["allocations"] = json.loads(r.pop("allocations_json") or "[]")
        return rows

    def ledger_find(self, vendor_id: str | None, invoice_number_norm: str | None, exclude_run: int) -> dict | None:
        if not vendor_id or not invoice_number_norm:
            return None
        return self._one("SELECT run_id, invoice_number, invoice_date, total, posted_at FROM ledger "
                         "WHERE vendor_id = ? AND invoice_number_norm = ? AND run_id != ? LIMIT 1",
                         (vendor_id, invoice_number_norm, exclude_run))

    def ledger_all(self) -> list[dict]:
        rows = self._all("SELECT * FROM ledger ORDER BY posted_at")
        for r in rows:
            r["allocations"] = json.loads(r.pop("allocations_json") or "[]")
        return rows

    # ---------------------------------------------------------------- reviews
    def add_review(self, run_id: int, action: str, reviewer: str, note: str, created_at: str | None = None) -> None:
        self._exec("INSERT INTO reviews (run_id, action, reviewer, note, created_at) VALUES (?,?,?,?,?)",
                   (run_id, action, reviewer, note, created_at or now_iso()))

    def reviews_for_run(self, run_id: int) -> list[dict]:
        return self._all("SELECT * FROM reviews WHERE run_id = ? ORDER BY created_at", (run_id,))

    def review_precedents(self, vendor_id: str, exclude_id: int) -> list[dict]:
        return self._all(
            "SELECT r.run_id, r.action, r.reviewer, r.note, r.created_at, runs.invoice_number, runs.result_json "
            "FROM reviews r JOIN runs ON runs.id = r.run_id WHERE runs.vendor_id = ? AND r.run_id != ? "
            "ORDER BY r.created_at DESC", (vendor_id, exclude_id))

    # ---------------------------------------------------------------- stats
    def stats(self) -> dict:
        rows = self._all("SELECT COALESCE(final_decision, decision) AS d, decision AS agent, decided_by, status, total, "
                         "duration_ms, read_method, created_at, headline, result_json FROM runs")
        return {"rows": rows}
