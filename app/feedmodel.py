"""Qt list model for the browse feed. Delegates read roles only; thumbnails
resolve to local file URLs so no Python runs in bindings during scroll."""

import asyncio

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QModelIndex,
    Qt,
    QUrl,
    Signal,
    Slot,
)

import innertube
import net
from feedstore import FeedStore
from innertube import Video
from thumbs import ThumbCache

VIDEO_ID, TITLE, CHANNEL, DURATION, THUMB, CHANNEL_ID, META, PLAYLIST_ID = range(
    Qt.ItemDataRole.UserRole + 1, Qt.ItemDataRole.UserRole + 9
)


class FeedModel(QAbstractListModel):
    contextChanged = Signal()
    message = Signal(str)  # user-facing toast, shown in the statusline

    def __init__(self, client, store: FeedStore, cache: ThumbCache, auth=None,
                 parent=None):
        super().__init__(parent)
        self._client = client
        self._store = store
        self._cache = cache
        self._auth = auth
        self._context_label = ""
        self._context_channel_id = ""
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
            CHANNEL_ID: b"channelId",
            META: b"meta",
            PLAYLIST_ID: b"playlistId",
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
            cached = self._thumbs.get(v.video_id, "")
            # Playlist entries have no cacheable video id; load remote.
            if not cached and v.playlist_id:
                return v.thumb_url
            return cached
        if role == CHANNEL_ID:
            return v.channel_id
        if role == META:
            return v.meta
        if role == PLAYLIST_ID:
            return v.playlist_id
        return None

    # --- invokables ---

    @Slot(str)
    def search(self, query: str):
        query = query.strip()
        if query:
            asyncio.create_task(self._search(query))

    @Slot(str)
    def requestThumb(self, video_id: str):
        if not video_id or video_id in self._thumbs \
                or video_id in self._pending:
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

    @Slot(str, str)
    def loadChannel(self, browse_id: str, name: str = ""):
        if browse_id:
            asyncio.create_task(self._load_channel(browse_id, name))

    async def _load_channel(self, browse_id: str, name: str):
        try:
            videos = await innertube.channel_videos(self._client, browse_id)
        except Exception as exc:
            print(f"feed: channel failed: {exc!r}")
            return
        print(f"feed: {len(videos)} channel videos")
        self._set_videos(videos)
        self._set_context("channel: " + (name or browse_id), browse_id)

    @Slot(str)
    def subscribeChannel(self, browse_id: str):
        if browse_id and self._auth is not None:
            asyncio.create_task(self._subscribe(browse_id))

    async def _subscribe(self, browse_id: str):
        bearer = await self._auth.bearer()
        if bearer is None:
            self.message.emit("login required (gl)")
            return
        try:
            await innertube.subscribe(self._client, bearer, browse_id)
            self.message.emit("subscribed")
        except Exception as exc:
            print(f"feed: subscribe failed: {exc!r}")
            self.message.emit("subscribe failed")

    def _set_context(self, label: str, channel_id: str = ""):
        self._context_label = label
        self._context_channel_id = channel_id
        self.contextChanged.emit()

    contextLabel = Property(str, lambda s: s._context_label,
                            notify=contextChanged)
    contextChannelId = Property(str, lambda s: s._context_channel_id,
                                notify=contextChanged)

    @Slot()
    def loadWatchLater(self):
        if self._auth is not None:
            asyncio.create_task(self._load_account_feed(
                innertube.watch_later, "watch later"))

    @Slot()
    def loadHome(self):
        if self._auth is not None:
            asyncio.create_task(self._load_account_feed(
                lambda c, b: innertube.account_feed(c, b, "FEwhat_to_watch"),
                "home"))

    @Slot()
    def loadHistory(self):
        if self._auth is not None:
            asyncio.create_task(self._load_account_feed(
                lambda c, b: innertube.account_feed(c, b, "FEhistory"),
                "history"))

    @Slot()
    def loadPlaylists(self):
        if self._auth is not None:
            asyncio.create_task(self._load_account_feed(
                innertube.my_playlists, "playlists"))

    @Slot(str)
    def loadPlaylist(self, playlist_id: str):
        if playlist_id and self._auth is not None:
            asyncio.create_task(self._load_account_feed(
                lambda c, b: innertube.playlist_videos(c, b, playlist_id),
                "playlist"))

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
        self._set_context("search: " + query)

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
        self._set_context(label)

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
