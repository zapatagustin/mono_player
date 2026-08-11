"""Comments for the watch panel: loaded on demand when the panel opens
(comments are heavy and not always wanted), LRU-cached per video id."""

import asyncio
from collections import OrderedDict

from PySide6.QtCore import Property, QObject, Signal, Slot

import innertube


class CommentsModel(QObject):
    changed = Signal()

    def __init__(self, client, fetch_fn=None, cache_size=16, parent=None):
        super().__init__(parent)
        self._client = client
        self._fetch = fetch_fn or innertube.comments
        self._cache: OrderedDict[str, list] = OrderedDict()
        self._cache_size = cache_size
        self._video_id = ""
        self._items: list[dict] = []
        self._loading = False

    @Slot(str)
    def setCurrent(self, video_id: str):
        """Track the active video; never fetches — load is on demand."""
        if video_id == self._video_id:
            return
        self._video_id = video_id
        if self._items:
            self._items = []
            self.changed.emit()

    @Slot()
    def loadCurrent(self):
        asyncio.create_task(self._load())

    async def _load(self):
        video_id = self._video_id
        if not video_id:
            return
        if video_id in self._cache:
            self._cache.move_to_end(video_id)
            self._apply(video_id, self._cache[video_id])
            return
        self._loading = True
        self.changed.emit()
        try:
            comments = await self._fetch(self._client, video_id)
        except Exception as exc:
            print(f"comments: fetch failed: {exc!r}")
            comments = []
        self._cache[video_id] = comments
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        self._apply(video_id, comments)

    def _apply(self, video_id: str, comments):
        if video_id != self._video_id:
            return  # video changed while fetching
        self._items = [
            {"author": c.author, "text": c.text, "likes": c.likes,
             "published": c.published, "replies": c.replies}
            for c in comments
        ]
        self._loading = False
        self.changed.emit()

    items = Property("QVariantList", lambda s: s._items, notify=changed)
    loading = Property(bool, lambda s: s._loading, notify=changed)
