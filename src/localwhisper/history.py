"""A local, private record of what you dictated.

Useful when an insertion lands in the wrong window: open the history and
re-insert. Nothing leaves the machine; the file is a plain SQLite database
under ``~/.local/share/local-whisper``.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .logging_setup import get

log = get("history")

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    REAL    NOT NULL,
    text          TEXT    NOT NULL,
    audio_seconds REAL    NOT NULL DEFAULT 0,
    transcribe_s  REAL    NOT NULL DEFAULT 0,
    model         TEXT    NOT NULL DEFAULT '',
    language      TEXT    NOT NULL DEFAULT '',
    inserted      INTEGER NOT NULL DEFAULT 1,
    method        TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS entries_created_at ON entries (created_at DESC);
"""


@dataclass
class Entry:
    id: int
    created_at: float
    text: str
    audio_seconds: float = 0.0
    transcribe_s: float = 0.0
    model: str = ""
    language: str = ""
    inserted: bool = True
    method: str = ""

    @property
    def words(self) -> int:
        return len(self.text.split())

    def when(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(self.created_at))


class History:
    """Thread-safe wrapper around a small SQLite table."""

    def __init__(self, path: Path | None = None, limit: int = 500) -> None:
        self.path = path or paths.history_db()
        self.limit = limit
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(SCHEMA)
            self._connection.commit()

    def add(
        self,
        text: str,
        *,
        audio_seconds: float = 0.0,
        transcribe_s: float = 0.0,
        model: str = "",
        language: str = "",
        inserted: bool = True,
        method: str = "",
    ) -> int:
        if not text.strip():
            return -1
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO entries (created_at, text, audio_seconds, transcribe_s, model,"
                " language, inserted, method) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), text, audio_seconds, transcribe_s, model, language,
                 int(inserted), method),
            )
            self._connection.commit()
            entry_id = int(cursor.lastrowid or -1)
            self._trim()
        return entry_id

    def _trim(self) -> None:
        """Keep only the newest ``limit`` rows; history is a convenience, not an archive."""
        self._connection.execute(
            "DELETE FROM entries WHERE id NOT IN ("
            "  SELECT id FROM entries ORDER BY created_at DESC LIMIT ?)",
            (max(1, self.limit),),
        )
        self._connection.commit()

    def recent(self, limit: int = 50, search: str = "") -> list[Entry]:
        query = "SELECT * FROM entries"
        params: list = []
        if search:
            query += " WHERE text LIKE ?"
            params.append(f"%{search}%")
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [_row_to_entry(row) for row in rows]

    def latest(self) -> Entry | None:
        entries = self.recent(1)
        return entries[0] if entries else None

    def delete(self, entry_id: int) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            self._connection.commit()

    def clear(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM entries")
            self._connection.commit()

    def stats(self) -> dict[str, float]:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(audio_seconds), 0) AS seconds FROM entries"
            ).fetchone()
            words = self._connection.execute("SELECT text FROM entries").fetchall()
        total_words = sum(len(str(r["text"]).split()) for r in words)
        return {
            "entries": float(row["n"]),
            "audio_seconds": float(row["seconds"]),
            "words": float(total_words),
        }

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _row_to_entry(row: sqlite3.Row) -> Entry:
    return Entry(
        id=int(row["id"]),
        created_at=float(row["created_at"]),
        text=str(row["text"]),
        audio_seconds=float(row["audio_seconds"]),
        transcribe_s=float(row["transcribe_s"]),
        model=str(row["model"]),
        language=str(row["language"]),
        inserted=bool(row["inserted"]),
        method=str(row["method"]),
    )
