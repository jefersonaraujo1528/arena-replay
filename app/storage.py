"""Persistência em SQLite (eventos e replays).

Uma única conexão protegida por lock: simples e suficiente para o volume do MVP
(poucos eventos por minuto). O modo WAL evita que leituras da API bloqueiem escritas.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Estados do replay (exibidos na interface)
WAITING = "waiting_recording"  # aguardando os segundos posteriores ao lance
PROCESSING = "processing"
DONE = "done"
ERROR = "error"

SCHEMA = """
CREATE TABLE IF NOT EXISTS replays (
    id            TEXT PRIMARY KEY,
    press_time    REAL NOT NULL,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    status        TEXT NOT NULL,
    pre_seconds   REAL NOT NULL,
    post_seconds  REAL NOT NULL,
    device_id     TEXT NOT NULL,
    file_name     TEXT,
    thumb_name    TEXT,
    duration      REAL,
    size_bytes    INTEGER,
    error         TEXT,
    warnings      TEXT NOT NULL DEFAULT '[]',
    presses       INTEGER NOT NULL DEFAULT 1,
    press_offset  REAL
);
CREATE INDEX IF NOT EXISTS idx_replays_press ON replays(press_time DESC);

CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT PRIMARY KEY,
    device_id    TEXT NOT NULL,
    received_at  REAL NOT NULL,
    press_time   REAL NOT NULL,
    outcome      TEXT NOT NULL,
    replay_id    TEXT REFERENCES replays(id) ON DELETE SET NULL
);
"""


@dataclass
class Replay:
    id: str
    press_time: float
    created_at: float
    updated_at: float
    status: str
    pre_seconds: float
    post_seconds: float
    device_id: str
    file_name: Optional[str] = None
    thumb_name: Optional[str] = None
    duration: Optional[float] = None
    size_bytes: Optional[int] = None
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    presses: int = 1
    press_offset: Optional[float] = None  # segundos do início do vídeo até o toque

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Replay":
        data = dict(row)
        data["warnings"] = json.loads(data.get("warnings") or "[]")
        return cls(**data)


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Adiciona colunas novas em bancos criados por versões anteriores."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(replays)")}
        if "press_offset" not in cols:
            self._conn.execute("ALTER TABLE replays ADD COLUMN press_offset REAL")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------ replays
    def insert_replay(self, replay: Replay) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO replays (id, press_time, created_at, updated_at, status, pre_seconds,"
                " post_seconds, device_id, warnings, presses) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (replay.id, replay.press_time, replay.created_at, replay.updated_at, replay.status,
                 replay.pre_seconds, replay.post_seconds, replay.device_id,
                 json.dumps(replay.warnings), replay.presses),
            )

    def update_replay(self, replay_id: str, **values: Any) -> None:
        allowed = {"status", "file_name", "thumb_name", "duration", "size_bytes", "error",
                   "warnings", "presses", "press_offset"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Campos não permitidos: {unknown}")
        if "warnings" in values:
            values["warnings"] = json.dumps(values["warnings"])
        values["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in values)
        with self._lock:
            self._conn.execute(f"UPDATE replays SET {cols} WHERE id = ?", (*values.values(), replay_id))

    def get_replay(self, replay_id: str) -> Optional[Replay]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM replays WHERE id = ?", (replay_id,)).fetchone()
        return Replay.from_row(row) if row else None

    def list_replays(self, limit: int = 50) -> List[Replay]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM replays ORDER BY press_time DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Replay.from_row(r) for r in rows]

    def replays_with_status(self, *statuses: str) -> List[Replay]:
        marks = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM replays WHERE status IN ({marks})", statuses
            ).fetchall()
        return [Replay.from_row(r) for r in rows]

    def oldest_replays_beyond(self, keep: int) -> List[Replay]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM replays ORDER BY press_time DESC LIMIT -1 OFFSET ?", (keep,)
            ).fetchall()
        return [Replay.from_row(r) for r in rows]

    def delete_replay(self, replay_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM replays WHERE id = ?", (replay_id,))

    def count_by_status(self) -> Dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT status, COUNT(*) AS n FROM replays GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    # ------------------------------------------------------------- eventos
    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM events WHERE event_id = ?", (event_id,)).fetchone()
        return dict(row) if row else None

    def insert_event(self, event_id: str, device_id: str, received_at: float, press_time: float,
                     outcome: str, replay_id: Optional[str]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (event_id, device_id, received_at, press_time, outcome, replay_id)"
                " VALUES (?,?,?,?,?,?)",
                (event_id, device_id, received_at, press_time, outcome, replay_id),
            )

    def last_accepted_replay(self) -> Optional[Replay]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM replays WHERE status != ? ORDER BY press_time DESC LIMIT 1", (ERROR,)
            ).fetchone()
        return Replay.from_row(row) if row else None

    def prune_events(self, older_than: float) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM events WHERE received_at < ?", (older_than,))
