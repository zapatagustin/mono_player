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


def _fallback_thumb(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


class FeedModel(QAbstractListModel):
    contextChanged = Signal()
    loadingMoreChanged = Signal()
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
        self._context_playlist_id = ""
        self._thumbs: dict[str, str] = {}  # video_id -> local file URL
        self._pending: set[str] = set()
        # Pagination for whichever feed is loaded (GUIDELINE.org):
        # (continuation token, call fetching the next page) or None, replaced
        # by any non-append feed load -- one value, so an in-flight
        # continuation can detect it went stale by identity.
        self._more = None  # (token, (token) -> (videos, next_token)) | None
        self._loading_more = False
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

    @Slot()
    def loadMore(self):
        """Called from QML when the grid scrolls near the end: no-op unless
        the current feed has a continuation and none is already in flight
        (onContentYChanged fires repeatedly while scrolling)."""
        if self._more is not None and not self._loading_more:
            # Flagged here, not in the task: several signals can land before
            # the loop runs the coroutine.
            self._set_loading_more(True)
            asyncio.create_task(self._load_more())

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
        if video is None:
            return
        self._pending.add(video_id)
        url = video.thumb_url or _fallback_thumb(video_id)
        asyncio.create_task(self._fetch_thumb(video_id, url))

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
            videos, token = await innertube.channel_videos(
                self._client, browse_id)
        except Exception as exc:
            print(f"feed: channel failed: {exc!r}")
            return
        print(f"feed: {len(videos)} channel videos")
        self._set_videos(videos)
        # Channel browse is anonymous: no bearer on its continuation either.
        self._set_more(token, lambda t: innertube.browse_continuation(
            self._client, None, t))
        self._set_context("channel: " + (name or browse_id), browse_id)

    @Slot(str)
    def likeVideo(self, video_id: str):
        if video_id and self._auth is not None:
            asyncio.create_task(self._rate(innertube.like, "liked", video_id))

    @Slot(str)
    def unlikeVideo(self, video_id: str):
        if video_id and self._auth is not None:
            asyncio.create_task(
                self._rate(innertube.unlike, "unliked", video_id))

    async def _rate(self, verb, done: str, video_id: str):
        bearer = await self._auth.bearer()
        if bearer is None:
            self.message.emit("login required (gl)")
            return
        try:
            await verb(self._client, bearer, video_id)
            self.message.emit(done)
        except Exception as exc:
            print(f"feed: {verb.__name__} failed: {exc!r}")
            self.message.emit(f"{verb.__name__} failed")

    @Slot(str, str)
    def commentVideo(self, video_id: str, text: str):
        text = text.strip()
        if video_id and text and self._auth is not None:
            asyncio.create_task(self._comment(video_id, text))

    async def _comment(self, video_id: str, text: str):
        bearer = await self._auth.bearer()
        if bearer is None:
            self.message.emit("login required (gl)")
            return
        try:
            ok = await innertube.create_comment(
                self._client, bearer, video_id, text)
            self.message.emit("comment posted" if ok
                              else "comments disabled on this video")
        except Exception as exc:
            print(f"feed: comment failed: {exc!r}")
            self.message.emit("comment failed")

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

    def _set_context(self, label: str, channel_id: str = "",
                     playlist_id: str = ""):
        self._context_label = label
        self._context_channel_id = channel_id
        self._context_playlist_id = playlist_id
        self.contextChanged.emit()

    contextLabel = Property(str, lambda s: s._context_label,
                            notify=contextChanged)
    contextChannelId = Property(str, lambda s: s._context_channel_id,
                                notify=contextChanged)
    # Non-empty only when the feed IS one of the account's own playlists
    # (watch later, or a playlist opened from gp) — gates removal.
    contextPlaylistId = Property(str, lambda s: s._context_playlist_id,
                                 notify=contextChanged)
    # Continuation page in flight — drives the grid's footer.
    loadingMore = Property(bool, lambda s: s._loading_more,
                           notify=loadingMoreChanged)

    def _set_loading_more(self, value: bool):
        if self._loading_more != value:
            self._loading_more = value
            self.loadingMoreChanged.emit()

    @Slot()
    def loadWatchLater(self):
        if self._auth is not None:
            asyncio.create_task(self._load_account_feed(
                innertube.watch_later, "watch later", playlist_id="WL",
                more=innertube.playlist_continuation))

    @Slot()
    def loadHome(self):
        if self._auth is not None:
            asyncio.create_task(self._load_home())

    async def _load_home(self):
        await self._load_account_feed(
            lambda c, b: innertube.account_feed(c, b, "FEwhat_to_watch"),
            "home", more=innertube.browse_continuation)
        await self._load_home_shorts()

    async def _load_home_shorts(self):
        """YouTube strips shorts from the ANDROID home feed on most requests;
        splice in recent shorts from the channels home already recommends.
        ecomono: fixed 4 channels x 2 shorts in one block at row 4 -- no
        tuning knobs (upgrade: size/position from config if it grates)."""
        channel_ids = list(dict.fromkeys(
            v.channel_id for v in self._videos if v.channel_id))[:4]
        if not channel_ids:
            return
        results = await asyncio.gather(
            *(innertube.channel_shorts(self._client, cid)
              for cid in channel_ids),
            return_exceptions=True)
        # Stale-context guard: the user may have navigated away meanwhile.
        if self._context_label != "home":
            return
        seen = {v.video_id for v in self._videos}
        shorts = []
        for res in results:
            if isinstance(res, BaseException):
                print(f"feed: home shorts failed: {res!r}")
                continue
            for v in res[:2]:
                if v.video_id not in seen:
                    seen.add(v.video_id)
                    shorts.append(v)
        if not shorts:
            return
        row = min(4, len(self._videos))
        self.beginInsertRows(QModelIndex(), row, row + len(shorts) - 1)
        self._videos[row:row] = shorts
        self.endInsertRows()
        self._store.save(self._videos)
        print(f"feed: {len(shorts)} home shorts spliced")

    @Slot()
    def loadHistory(self):
        if self._auth is not None:
            asyncio.create_task(self._load_account_feed(
                lambda c, b: innertube.account_feed(c, b, "FEhistory"),
                "history", more=innertube.browse_continuation))

    @Slot()
    def loadPlaylists(self):
        if self._auth is not None:
            # The playlist list is a single page: no `more`.
            asyncio.create_task(self._load_account_feed(
                innertube.my_playlists, "playlists"))

    @Slot(str)
    def loadPlaylist(self, playlist_id: str):
        if playlist_id and self._auth is not None:
            asyncio.create_task(self._load_account_feed(
                lambda c, b: innertube.playlist_videos(c, b, playlist_id),
                "playlist", playlist_id=playlist_id,
                more=innertube.playlist_continuation))

    @Slot(str)
    def addToWatchLater(self, video_id: str):
        if self._auth is not None:
            asyncio.create_task(self._add_watch_later(video_id))

    @Slot(str)
    def removeFromPlaylist(self, video_id: str):
        """Remove a video from the playlist the feed is showing (gated by
        contextPlaylistId); the cell drops locally on success."""
        if self._auth is not None and self._context_playlist_id:
            asyncio.create_task(
                self._remove_from_playlist(video_id,
                                           self._context_playlist_id))

    # --- async internals ---

    async def _search(self, query: str):
        try:
            videos, token = await innertube.search(self._client, query)
        except Exception as exc:  # error surface: log, keep current feed
            print(f"feed: search failed: {exc!r}")
            return
        print(f"feed: {len(videos)} videos for {query!r}")
        self._set_videos(videos)
        self._set_more(token, lambda t: innertube.search_continuation(
            self._client, t))
        self._set_context("search: " + query)

    async def _load_more(self):
        more = self._more
        if more is None:  # cleared between the slot firing and this running
            self._set_loading_more(False)
            return
        token, fetch = more
        try:
            videos, next_token = await fetch(token)
        except Exception as exc:
            print(f"feed: continuation failed: {exc!r}")
            return
        finally:
            self._set_loading_more(False)
        # Stale-continuation guard (same idea as _remove_from_playlist):
        # another feed may have loaded during the await -- these rows and
        # this token belong to the old feed, not the one on screen.
        if self._more is not more:
            return
        self._more = (next_token, fetch) if next_token else None
        print(f"feed: {len(videos)} more videos (continuation)")
        self._append_videos(videos)

    async def _load_subscriptions(self):
        await self._load_account_feed(
            innertube.subscriptions, "subscriptions",
            more=innertube.subscriptions_continuation)

    async def _load_account_feed(self, fetch, label: str,
                                 playlist_id: str = "", more=None):
        bearer = await self._auth.bearer()
        if bearer is None:
            print(f"feed: {label} needs login")
            return
        try:
            videos, token = await fetch(self._client, bearer)
        except Exception as exc:
            print(f"feed: {label} failed: {exc!r}")
            return
        if videos:
            print(f"feed: {len(videos)} {label} videos")
        else:
            # 0 is ambiguous: empty account feed or the parser drifted off
            # the payload again (it has, twice) -- point at the discriminator.
            print(f"feed: 0 {label} videos -- empty feed or parser drift;"
                  " dump the raw response to tell")
        self._set_videos(videos)
        if more is not None:
            async def fetch_more(t):
                # Bearer resolved per page: the first-page one expires while
                # the feed stays on screen (auth re-mints on demand).
                fresh = await self._auth.bearer()
                if fresh is None:
                    raise RuntimeError("login required")
                return await more(self._client, fresh, t)
            self._set_more(token, fetch_more)
        self._set_context(label, playlist_id=playlist_id)

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

    async def _remove_from_playlist(self, video_id: str, playlist_id: str):
        bearer = await self._auth.bearer()
        if bearer is None:
            self.message.emit("login required (gl)")
            return
        try:
            ok = await innertube.remove_from_playlist(
                self._client, bearer, video_id, playlist_id)
        except Exception as exc:
            print(f"feed: playlist remove failed: {exc!r}")
            ok = False
        if not ok:
            self.message.emit("remove failed")
            return
        # Stale-context guard: the user may have navigated away while the
        # request was in flight — only drop the cell if it's still here.
        row = next((i for i, v in enumerate(self._videos)
                    if v.video_id == video_id), None)
        if row is not None:
            self.beginRemoveRows(QModelIndex(), row, row)
            self._videos.pop(row)
            self.endRemoveRows()
            self._store.save(self._videos)
        self.message.emit("removed")

    def _set_videos(self, videos):
        self.beginResetModel()
        self._videos = videos
        self.endResetModel()
        self._store.save(videos)
        # Any full reload invalidates pagination; the loader re-sets the
        # continuation right after this, for the feeds that have one.
        self._more = None

    def _set_more(self, token: str, fetch):
        self._more = (token, fetch) if token else None

    def _append_videos(self, videos):
        """Append a continuation page, deduped by video_id -- YouTube
        repeats items across pages (GUIDELINE.org)."""
        existing = {v.video_id for v in self._videos}
        new = [v for v in videos if v.video_id not in existing]
        if not new:
            return
        start = len(self._videos)
        self.beginInsertRows(QModelIndex(), start, start + len(new) - 1)
        self._videos.extend(new)
        self.endInsertRows()
        self._store.append(new, start)

    async def _fetch_thumb(self, video_id: str, url: str):
        # Parsed URLs can 404 or expire; i.ytimg.com hqdefault always exists.
        try:
            for attempt_url in dict.fromkeys((url, _fallback_thumb(video_id))):
                try:
                    async with net.THUMB_SEMAPHORE:
                        resp = await self._client.get(attempt_url)
                        resp.raise_for_status()
                except Exception as exc:
                    print(f"feed: thumb {video_id} failed: {exc!r}")
                    continue
                self._set_thumb(video_id,
                                self._cache.put(video_id, resp.content))
                return
        finally:
            self._pending.discard(video_id)

    def _set_thumb(self, video_id: str, path):
        self._thumbs[video_id] = QUrl.fromLocalFile(path).toString()
        for row, v in enumerate(self._videos):
            if v.video_id == video_id:
                idx = self.index(row)
                self.dataChanged.emit(idx, idx, [THUMB])
                return
