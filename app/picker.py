"""Save-to-playlist picker for the watch panel: the account's playlists for
one video (fresh each open — the contains flags are per video), save routed
by index."""

import asyncio

from PySide6.QtCore import Property, QObject, Signal, Slot

import innertube


class PlaylistPicker(QObject):
    changed = Signal()
    message = Signal(str)

    def __init__(self, client, auth, options_fn=None, add_fn=None, parent=None):
        super().__init__(parent)
        self._client = client
        self._auth = auth
        self._options = options_fn or innertube.playlist_options
        self._add = add_fn or innertube.add_to_playlist
        self._video_id = ""
        self._items: list[dict] = []
        self._loading = False

    @Slot(str)
    def load(self, video_id: str):
        asyncio.create_task(self._load(video_id))

    @Slot(int)
    def save(self, index: int):
        asyncio.create_task(self._save(index))

    async def _load(self, video_id: str):
        self._video_id = video_id
        self._items = []
        self._loading = True
        self.changed.emit()
        bearer = await self._auth.bearer()
        if bearer is None:
            self._loading = False
            self.changed.emit()
            self.message.emit("login required (gl)")
            return
        try:
            options = await self._options(self._client, bearer, video_id)
        except Exception as exc:
            print(f"picker: options failed: {exc!r}")
            options = []
        self._items = [
            {"playlistId": o.playlist_id, "title": o.title,
             "contains": o.contains}
            for o in options
        ]
        self._loading = False
        self.changed.emit()

    async def _save(self, index: int):
        if not 0 <= index < len(self._items):
            return
        item = self._items[index]
        bearer = await self._auth.bearer()
        if bearer is None:
            self.message.emit("login required (gl)")
            return
        try:
            await self._add(self._client, bearer, self._video_id,
                            item["playlistId"])
        except Exception as exc:
            print(f"picker: save failed: {exc!r}")
            self.message.emit("save failed")
            return
        item["contains"] = True
        self.changed.emit()
        self.message.emit("saved to " + item["title"])

    items = Property("QVariantList", lambda s: s._items, notify=changed)
    loading = Property(bool, lambda s: s._loading, notify=changed)
