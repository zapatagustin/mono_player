"""sqlite persistence for the last feed, so cold start paints cached content
before any network (GUIDELINE.org, Network)."""

import sqlite3
from pathlib import Path

from innertube import Video


_COLUMNS = (
    "video_id", "title", "channel", "duration", "thumb_url",
    "channel_id", "meta", "playlist_id",
)

_TABLE_BODY = """(
    pos INTEGER PRIMARY KEY,
    video_id TEXT NOT NULL,
    title TEXT NOT NULL,
    channel TEXT NOT NULL,
    duration TEXT NOT NULL,
    thumb_url TEXT NOT NULL,
    channel_id TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT '',
    playlist_id TEXT NOT NULL DEFAULT ''
)"""


class FeedStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.execute("CREATE TABLE IF NOT EXISTS feed_cache " + _TABLE_BODY)
        # Pure cache: an older schema missing the new columns is dropped and
        # recreated rather than migrated -- next network refresh repopulates it.
        existing = {row[1] for row in self.db.execute("PRAGMA table_info(feed_cache)")}
        if not set(_COLUMNS).issubset(existing):
            self.db.execute("DROP TABLE feed_cache")
            self.db.execute("CREATE TABLE feed_cache " + _TABLE_BODY)

    def load(self) -> list[Video]:
        rows = self.db.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM feed_cache ORDER BY pos"
        ).fetchall()
        return [Video(*row) for row in rows]

    def save(self, videos: list[Video]) -> None:
        with self.db:
            self.db.execute("DELETE FROM feed_cache")
            self.db.executemany(
                f"INSERT INTO feed_cache (pos, {', '.join(_COLUMNS)})"
                f" VALUES ({', '.join('?' * (len(_COLUMNS) + 1))})",
                [
                    (i, v.video_id, v.title, v.channel, v.duration, v.thumb_url,
                     v.channel_id, v.meta, v.playlist_id)
                    for i, v in enumerate(videos)
                ],
            )
