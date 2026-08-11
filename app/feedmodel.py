"""Qt list model for the browse feed. Delegates read roles only; thumbnails
resolve to local file URLs so no Python runs in bindings during scroll."""

import asyncio

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, QUrl, Slot

import innertube
import net
from feedstore import FeedStore
from innertube import Video
from thumbs import ThumbCache

VIDEO_ID, TITLE, CHANNEL, DURATION, THUMB = range(
    Qt.ItemDataRole.UserRole + 1, Qt.ItemDataRole.UserRole + 6
)


class FeedModel(QAbstractListModel):
    def __init__(self, client, store: FeedStore, cache: ThumbCache, auth=None,
                 parent=None):
        super().__init__(parent)
        self._client = client
        self._store = store
        self._cache = cache
        self._auth = auth
        self._thumbs: dict[str, str] = {}  # video_id -> local file URL
        self._pending: set[str] = set()
        # Cold start: paint the cached feed before any network.
        self._videos: list[Video] = store.load()
        if self._videos:
            print(f"feed: {len(self._videos)} videos from cache")

    # --- QAbstractListModel ---

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._videos)

    def roleNames(self):
        return {
            VIDEO_ID: b"videoId",
            TITLE: b"title",
            CHANNEL: b"channel",
            DURATION: b"duration",
            THUMB: b"thumb",
        }

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._videos):
            return None
        v = self._videos[index.row()]
        if role == VIDEO_ID:
            return v.video_id
        if role == TITLE:
            return v.title
        if role == CHANNEL:
            return v.channel
        if role == DURATION:
            return v.duration
        if role == THUMB:
            return self._thumbs.get(v.video_id, "")
        return None

    # --- invokables ---

    @Slot(str)
    def search(self, query: str):
        query = query.strip()
        if query:
            asyncio.create_task(self._search(query))

    @Slot(str)
    def requestThumb(self, video_id: str):
        if video_id in self._thumbs or video_id in self._pending:
            return
        cached = self._cache.get(video_id)
        if cached is not None:
            self._set_thumb(video_id, cached)
            return
        video = next((v for v in self._videos if v.video_id == video_id), None)
        if video is None or not video.thumb_url:
            return
        self._pending.add(video_id)
        asyncio.create_task(self._fetch_thumb(video_id, video.thumb_url))

    @Slot()
    def loadSubscriptions(self):
        if self._auth is not None:
            asyncio.create_task(self._load_subscriptions())

    @Slot()
    def loadWatchLater(self):
        if self._auth is not None:
            asyncio.create_task(self._load_account_feed(
                innertube.watch_later, "watch later"))

    @Slot(str)
    def addToWatchLater(self, video_id: str):
        if self._auth is not None:
            asyncio.create_task(self._add_watch_later(video_id))

    # --- async internals ---

    async def _search(self, query: str):
        try:
            videos = await innertube.search(self._client, query)
        except Exception as exc:  # error surface: log, keep current feed
            print(f"feed: search failed: {exc!r}")
            return
        print(f"feed: {len(videos)} videos for {query!r}")
        self._set_videos(videos)

    async def _load_subscriptions(self):
        await self._load_account_feed(innertube.subscriptions, "subscriptions")

    async def _load_account_feed(self, fetch, label: str):
        bearer = await self._auth.bearer()
        if bearer is None:
            print(f"feed: {label} needs login")
            return
        try:
            videos = await fetch(self._client, bearer)
        except Exception as exc:
            print(f"feed: {label} failed: {exc!r}")
            return
        print(f"feed: {len(videos)} {label} videos")
        self._set_videos(videos)

    async def _add_watch_later(self, video_id: str):
        bearer = await self._auth.bearer()
        if bearer is None:
            print("feed: watch later needs login")
            return
        try:
            ok = await innertube.add_to_watch_later(self._client, bearer, video_id)
            print(f"feed: watch later add {video_id}: {'ok' if ok else 'failed'}")
        except Exception as exc:
            print(f"feed: watch later add failed: {exc!r}")

    def _set_videos(self, videos):
        self.beginResetModel()
        self._videos = videos
        self.endResetModel()
        self._store.save(videos)

    async def _fetch_thumb(self, video_id: str, url: str):
        try:
            async with net.THUMB_SEMAPHORE:
                resp = await self._client.get(url)
                resp.raise_for_status()
            self._set_thumb(video_id, self._cache.put(video_id, resp.content))
        except Exception as exc:
            print(f"feed: thumb {video_id} failed: {exc!r}")
        finally:
            self._pending.discard(video_id)

    def _set_thumb(self, video_id: str, path):
        self._thumbs[video_id] = QUrl.fromLocalFile(path).toString()
        for row, v in enumerate(self._videos):
            if v.video_id == video_id:
                idx = self.index(row)
                self.dataChanged.emit(idx, idx, [THUMB])
                return
