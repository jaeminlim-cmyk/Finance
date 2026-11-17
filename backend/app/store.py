"""SQLite 저장소. 운용 DB와 백테스트 DB가 같은 스키마를 쓴다."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, desk TEXT NOT NULL, qty INTEGER NOT NULL,
    avg_price REAL NOT NULL, stop_price REAL, target_price REAL,
    sector TEXT, opened_at TEXT NOT NULL, decision_id INTEGER, entry_cost REAL DEFAULT 0,
    UNIQUE(symbol, desk)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, desk TEXT NOT NULL,
    qty INTEGER NOT NULL, filled_qty INTEGER NOT NULL DEFAULT 0,
    ref_price REAL, fill_price REAL, commission REAL DEFAULT 0, slippage REAL DEFAULT 0,
    status TEXT NOT NULL, reason TEXT, note TEXT, decision_id INTEGER
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, desk TEXT NOT NULL, qty INTEGER NOT NULL,
    entry_price REAL NOT NULL, exit_price REAL NOT NULL,
    entry_ts TEXT NOT NULL, exit_ts TEXT NOT NULL,
    pnl REAL NOT NULL, pnl_pct REAL NOT NULL, costs REAL NOT NULL DEFAULT 0,
    exit_reason TEXT, sector TEXT, decision_id INTEGER, reviewed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS equity (
    ts TEXT PRIMARY KEY, day TEXT NOT NULL, equity REAL NOT NULL, cash REAL NOT NULL,
    positions_value REAL NOT NULL, benchmark REAL
);

CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, as_of TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
    stage TEXT, symbols TEXT, summary TEXT, cost_usd REAL DEFAULT 0, error TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL, ts TEXT NOT NULL, as_of TEXT NOT NULL,
    symbol TEXT NOT NULL, agent TEXT NOT NULL, signal TEXT, confidence REAL,
    summary TEXT, payload TEXT NOT NULL, ref_price REAL,
    fwd_return REAL, hit INTEGER
);
CREATE INDEX IF NOT EXISTS idx_reports_meeting ON reports(meeting_id, symbol);
CREATE INDEX IF NOT EXISTS idx_reports_agent ON reports(agent, symbol);

CREATE TABLE IF NOT EXISTS debate (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL, ts TEXT NOT NULL, symbol TEXT NOT NULL,
    round INTEGER NOT NULL, side TEXT NOT NULL, payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL, ts TEXT NOT NULL, symbol TEXT NOT NULL,
    action TEXT NOT NULL, conviction REAL, rationale TEXT, dissent TEXT,
    consensus REAL, proposal TEXT, risk_review TEXT, decision TEXT NOT NULL,
    status TEXT NOT NULL, status_detail TEXT, qty INTEGER, stop_price REAL, target_price REAL
);

CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, kind TEXT NOT NULL, symbol TEXT, detail TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, month TEXT NOT NULL, agent TEXT NOT NULL, model TEXT NOT NULL,
    input_tokens INTEGER, output_tokens INTEGER, cache_read INTEGER, cache_write INTEGER,
    cost_usd REAL NOT NULL, cached INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, ts TEXT NOT NULL, payload TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, trade_id INTEGER, symbol TEXT, sector TEXT, pnl_pct REAL,
    lesson TEXT NOT NULL, payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, actor TEXT NOT NULL, event TEXT NOT NULL, detail TEXT
);
"""

JSON_COLUMNS = {"payload", "proposal", "risk_review", "decision", "symbols"}


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._listeners: list[Callable[[str, dict], None]] = []
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    # ── 기본 연산 ──────────────────────────────────────────
    def execute(self, sql: str, params: tuple | dict = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.lastrowid or 0

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._decode(r) for r in rows]

    def one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: tuple | dict = (), default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return default if row is None or row[0] is None else row[0]

    def insert(self, table: str, **cols: Any) -> int:
        keys = list(cols)
        vals = [json.dumps(v, ensure_ascii=False) if k in JSON_COLUMNS and not isinstance(v, str) and v is not None else v
                for k, v in cols.items()]
        sql = f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})"
        return self.execute(sql, tuple(vals))

    def update(self, table: str, row_id: int, **cols: Any) -> None:
        keys = list(cols)
        vals = [json.dumps(v, ensure_ascii=False) if k in JSON_COLUMNS and not isinstance(v, str) and v is not None else v
                for k, v in cols.items()]
        self.execute(f"UPDATE {table} SET {','.join(k + '=?' for k in keys)} WHERE id=?", (*vals, row_id))

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        d = dict(row)
        for k in JSON_COLUMNS & d.keys():
            if isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except json.JSONDecodeError:
                    pass
        return d

    # ── 상태 키-값 ─────────────────────────────────────────
    def get_state(self, key: str, default: Any = None) -> Any:
        v = self.scalar("SELECT value FROM state WHERE key=?", (key,))
        return default if v is None else json.loads(v)

    def set_state(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # ── 감사 로그 / 이벤트 ─────────────────────────────────
    def audit(self, ts: str, actor: str, event: str, detail: Any = None) -> None:
        self.insert("audit", ts=ts, actor=actor, event=event,
                    detail=json.dumps(detail, ensure_ascii=False, default=str) if detail is not None else None)

    def subscribe(self, fn: Callable[[str, dict], None]) -> None:
        self._listeners.append(fn)

    def emit(self, topic: str, data: dict | None = None) -> None:
        for fn in list(self._listeners):
            try:
                fn(topic, data or {})
            except Exception:  # 구독자 오류가 매매 흐름을 막으면 안 된다
                pass

    def close(self) -> None:
        with self._lock:
            self._conn.close()
