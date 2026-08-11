"""Related videos for the watch panel: a function of video_id, LRU-cached
(GUIDELINE, Tabs). Fetch is the anonymous InnerTube `next`. Thumbnails ride
the same disk LRU as the grid."""

import asyncio
from collections import OrderedDict

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

import innertube
import net


class RelatedModel(QObject):
    changed = Signal()

    def __init__(self, client, fetch_fn=None, cache_size=32, thumb_cache=None,
                 parent=None):
        super().__init__(parent)
        self._client = client
        self._fetch = fetch_fn or innertube.related
        self._cache: OrderedDict[str, tuple] = OrderedDict()
        self._cache_size = cache_size
        self._thumbs = thumb_cache
        self._channel_id = ""
        self._channel_name = ""
        self._items: list[dict] = []
        self._loading = False

    @Slot(str)
    def load(self, video_id: str):
        asyncio.create_task(self._load(video_id))

    async def _load(self, video_id: str):
        if video_id in self._cache:
            self._cache.move_to_end(video_id)
            self._apply(*self._cache[video_id])
            return
        self._loading = True
        self.changed.emit()
        try:
            result = await self._fetch(self._client, video_id)
        except Exception as exc:
            print(f"related: fetch failed: {exc!r}")
            result = ("", "", [])
        self._cache[video_id] = result
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        self._apply(*result)

    def _apply(self, channel_id: str, channel_name: str, videos):
        self._channel_id = channel_id
        self._channel_name = channel_name
        self._items = [
            {"videoId": v.video_id, "title": v.title, "channel": v.channel,
             "duration": v.duration, "channelId": v.channel_id,
             "meta": v.meta, "thumb": self._thumb_of(v.video_id)}
            for v in videos
        ]
        self._loading = False
        self.changed.emit()
        for v in videos:
            if self._thumb_of(v.video_id) == "" and v.thumb_url \
                    and self._client is not None:
                asyncio.create_task(self._fetch_thumb(v.video_id, v.thumb_url))

    def _thumb_of(self, video_id: str) -> str:
        if self._thumbs is None:
            return ""
        path = self._thumbs.get(video_id)
        return QUrl.fromLocalFile(str(path)).toString() if path else ""

    async def _fetch_thumb(self, video_id: str, url: str):
        try:
            async with net.THUMB_SEMAPHORE:
                resp = await self._client.get(url)
                resp.raise_for_status()
            self._thumbs.put(video_id, resp.content)
        except Exception as exc:
            print(f"related: thumb {video_id} failed: {exc!r}")
            return
        for item in self._items:
            if item["videoId"] == video_id:
                item["thumb"] = self._thumb_of(video_id)
                self.changed.emit()
                break

    items = Property("QVariantList", lambda s: s._items, notify=changed)
    channelId = Property(str, lambda s: s._channel_id, notify=changed)
    channelName = Property(str, lambda s: s._channel_name, notify=changed)
    loading = Property(bool, lambda s: s._loading, notify=changed)
