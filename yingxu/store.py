"""Local preset copies and task history. The GUI still writes preset JSON files."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator


class WorkbenchStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path.home() / ".yingxu" / "yingxu.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """create table if not exists presets (
                    name text primary key,
                    payload text not null,
                    updated text not null
                )"""
            )
            conn.execute(
                """create table if not exists tasks (
                    id integer primary key autoincrement,
                    created text not null,
                    source text not null,
                    version integer,
                    status text not null,
                    output text,
                    message text,
                    parameters text
                )"""
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # sqlite3's own context manager only commits; close explicitly so Windows releases the file.
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def save_preset(self, name: str, payload: dict) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "insert into presets(name, payload, updated) values(?, ?, ?) "
                "on conflict(name) do update set payload=excluded.payload, updated=excluded.updated",
                (name, json.dumps(payload, ensure_ascii=False), now),
            )

    def load_preset(self, name: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("select payload from presets where name = ?", (name,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def delete_preset(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute("delete from presets where name = ?", (name,))

    def add_task(self, source: str, version: int, status: str, output: str | None,
                 message: str, parameters: dict | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "insert into tasks(created, source, version, status, output, message, parameters) "
                "values(?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), source, version, status, output, message,
                 json.dumps(parameters or {}, ensure_ascii=False)),
            )

    def recent_tasks(self, limit: int = 40) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "select created, source, version, status, output, message from tasks "
                "order by id desc limit ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
