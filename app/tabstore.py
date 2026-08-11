"""sqlite persistence for tabs. Every queue lives here; mpv's playlist is a
materialization of the active tab only (GUIDELINE.org, Tabs)."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QueueItem:
    video_id: str
    title: str


@dataclass(frozen=True)
class Tab:
    id: int
    queue: list[QueueItem]
    queue_idx: int
    position_secs: float


class TabStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.execute("PRAGMA foreign_keys = ON")
        with self.db:
            self.db.executescript(
                """CREATE TABLE IF NOT EXISTS tabs (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       pos INTEGER NOT NULL,
                       queue_idx INTEGER NOT NULL DEFAULT 0,
                       position_secs REAL NOT NULL DEFAULT 0
                   );
                   CREATE TABLE IF NOT EXISTS queue_items (
                       tab_id INTEGER NOT NULL REFERENCES tabs(id) ON DELETE CASCADE,
                       pos INTEGER NOT NULL,
                       video_id TEXT NOT NULL,
                       title TEXT NOT NULL,
                       PRIMARY KEY (tab_id, pos)
                   );
                   CREATE TABLE IF NOT EXISTS meta (
                       key TEXT PRIMARY KEY,
                       value TEXT
                   );"""
            )

    def load(self) -> tuple[list[Tab], int | None]:
        tabs = []
        for tab_id, queue_idx, position in self.db.execute(
            "SELECT id, queue_idx, position_secs FROM tabs ORDER BY pos"
        ).fetchall():
            queue = [
                QueueItem(vid, title)
                for vid, title in self.db.execute(
                    "SELECT video_id, title FROM queue_items"
                    " WHERE tab_id = ? ORDER BY pos",
                    (tab_id,),
                ).fetchall()
            ]
            tabs.append(Tab(tab_id, queue, queue_idx, position))
        row = self.db.execute(
            "SELECT value FROM meta WHERE key = 'active_tab'"
        ).fetchone()
        active = int(row[0]) if row and row[0] is not None else None
        if active is not None and all(t.id != active for t in tabs):
            active = None
        return tabs, active

    def create(self, queue: list[QueueItem]) -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO tabs (pos) VALUES"
                " ((SELECT COALESCE(MAX(pos), 0) + 1 FROM tabs))"
            )
            tab_id = cur.lastrowid
            self._insert_queue(tab_id, queue)
        return tab_id

    def set_queue(self, tab_id: int, queue: list[QueueItem]) -> None:
        with self.db:
            self.db.execute("DELETE FROM queue_items WHERE tab_id = ?", (tab_id,))
            self._insert_queue(tab_id, queue)
            self.db.execute(
                "UPDATE tabs SET queue_idx = 0, position_secs = 0 WHERE id = ?",
                (tab_id,),
            )

    def update_queue(self, tab_id: int, queue: list[QueueItem]) -> None:
        """Rewrite the queue keeping playback progress (enqueue/play-next)."""
        with self.db:
            self.db.execute("DELETE FROM queue_items WHERE tab_id = ?", (tab_id,))
            self._insert_queue(tab_id, queue)

    def save_state(self, tab_id: int, queue_idx: int, position_secs: float) -> None:
        with self.db:
            self.db.execute(
                "UPDATE tabs SET queue_idx = ?, position_secs = ? WHERE id = ?",
                (queue_idx, position_secs, tab_id),
            )

    def set_active(self, tab_id: int | None) -> None:
        self.meta_set("active_tab", tab_id)

    def meta_get(self, key: str) -> str | None:
        row = self.db.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def meta_set(self, key: str, value) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?)"
                " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def delete(self, tab_id: int) -> None:
        with self.db:
            self.db.execute("DELETE FROM tabs WHERE id = ?", (tab_id,))
            self.db.execute(
                "UPDATE meta SET value = NULL WHERE key = 'active_tab' AND value = ?",
                (tab_id,),
            )

    def _insert_queue(self, tab_id: int, queue: list[QueueItem]) -> None:
        self.db.executemany(
            "INSERT INTO queue_items (tab_id, pos, video_id, title)"
            " VALUES (?, ?, ?, ?)",
            [(tab_id, i, q.video_id, q.title) for i, q in enumerate(queue)],
        )
