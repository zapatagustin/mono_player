"""Disk LRU cache for thumbnails, keyed by video id (validated upstream at
the InnerTube parse boundary). Recency = file mtime."""

import os
from pathlib import Path


class ThumbCache:
    def __init__(self, root: Path, max_files: int = 500):
        self.root = root
        self.max_files = max_files
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, video_id: str) -> Path:
        return self.root / f"{video_id}.jpg"

    def get(self, video_id: str) -> Path | None:
        p = self._path(video_id)
        if not p.exists():
            return None
        os.utime(p)  # refresh recency
        return p

    def put(self, video_id: str, data: bytes) -> Path:
        p = self._path(video_id)
        p.write_bytes(data)
        self._evict()
        return p

    def _evict(self) -> None:
        files = sorted(self.root.glob("*.jpg"), key=lambda f: f.stat().st_mtime)
        for f in files[: max(0, len(files) - self.max_files)]:
            f.unlink(missing_ok=True)
