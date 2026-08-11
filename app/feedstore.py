"""sqlite persistence for the last feed, so cold start paints cached content
before any network (GUIDELINE.org, Network)."""

import sqlite3
from pathlib import Path

from innertube import Video


class FeedStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS feed_cache (
                   pos INTEGER PRIMARY KEY,
                   video_id TEXT NOT NULL,
                   title TEXT NOT NULL,
                   channel TEXT NOT NULL,
                   duration TEXT NOT NULL,
                   thumb_url TEXT NOT NULL
               )"""
        )

    def load(self) -> list[Video]:
        rows = self.db.execute(
            "SELECT video_id, title, channel, duration, thumb_url"
            " FROM feed_cache ORDER BY pos"
        ).fetchall()
        return [Video(*row) for row in rows]

    def save(self, videos: list[Video]) -> None:
        with self.db:
            self.db.execute("DELETE FROM feed_cache")
            self.db.executemany(
                "INSERT INTO feed_cache (pos, video_id, title, channel, duration, thumb_url)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (i, v.video_id, v.title, v.channel, v.duration, v.thumb_url)
                    for i, v in enumerate(videos)
                ],
            )
