"""Tab strip model + the browser-style player pool. Each recently-used tab
keeps a live, PAUSED mpv player (switching = show/pause, no reload); a hard
cap plus a background TTL freeze old tabs the way browsers discard them.
Sync stays one-way: commands out via per-tab signals, only the
playback-time/playlist-pos observers flow back."""

import time

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    Property,
    Qt,
    QTimer,
    Signal,
    Slot,
)

from tabstore import QueueItem, Tab, TabStore

TITLE, ACTIVE = range(Qt.ItemDataRole.UserRole + 1, Qt.ItemDataRole.UserRole + 3)

WATCH_URL = "https://www.youtube.com/watch?v="

LIVE_CAP = 3
FREEZE_TTL_SECS = 30 * 60


def materialize(tab: Tab, resolve=None) -> list[list]:
    """Commands that load a tab into its player: current item replaces the
    playlist (resume position as a per-item option), upcoming items append
    so mpv can auto-advance. Earlier items live only in sqlite. `resolve`
    maps a video_id to a URL (cached stream or watch page)."""
    if resolve is None:
        resolve = lambda vid: WATCH_URL + vid  # noqa: E731
    if not tab.queue:
        return []
    idx = min(max(0, tab.queue_idx), len(tab.queue) - 1)
    current = ["loadfile", resolve(tab.queue[idx].video_id), "replace"]
    if tab.position_secs > 0:
        current += [-1, {"start": str(tab.position_secs)}]
    return [current] + [
        ["loadfile", resolve(item.video_id), "append"]
        for item in tab.queue[idx + 1:]
    ]


class _LivePlayer:
    __slots__ = ("offset", "used_cache", "last_active", "retried")

    def __init__(self, offset: int, now: float):
        self.offset = offset
        self.used_cache = False
        self.last_active = now
        self.retried = False  # one automatic retry per user-initiated load


class QueueModel(QAbstractListModel):
    """The ACTIVE tab's queue for the watch-view queue panel. Mutations
    while visible are granular (move/remove/insert/current); switching
    tabs or replacing the queue resets."""

    TITLE, CURRENT = range(Qt.ItemDataRole.UserRole + 1,
                           Qt.ItemDataRole.UserRole + 3)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[QueueItem] = []
        self._current = -1

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._items)

    def roleNames(self):
        return {self.TITLE: b"title", self.CURRENT: b"current"}

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        if role == self.TITLE:
            return self._items[index.row()].title
        if role == self.CURRENT:
            return index.row() == self._current
        return None

    def reset(self, items: list[QueueItem], current: int):
        self.beginResetModel()
        self._items = list(items)
        self._current = current
        self.endResetModel()

    def insert_row(self, pos: int, item: QueueItem):
        self.beginInsertRows(QModelIndex(), pos, pos)
        self._items.insert(pos, item)
        if pos <= self._current:
            self._current += 1
        self.endInsertRows()

    def move_row(self, frm: int, to: int):
        # beginMoveRows destination: the row BEFORE which the item lands.
        dest = to + 1 if to > frm else to
        if not self.beginMoveRows(QModelIndex(), frm, frm, QModelIndex(),
                                  dest):
            return
        self._items.insert(to, self._items.pop(frm))
        self.endMoveRows()

    def remove_row(self, pos: int):
        self.beginRemoveRows(QModelIndex(), pos, pos)
        self._items.pop(pos)
        if pos < self._current:
            self._current -= 1
        self.endRemoveRows()

    def set_current(self, idx: int):
        old, self._current = self._current, idx
        for row in (old, idx):
            if 0 <= row < len(self._items):
                mi = self.index(row)
                self.dataChanged.emit(mi, mi, [self.CURRENT])


class TabManager(QAbstractListModel):
    createPlayer = Signal(int)
    destroyPlayer = Signal(int)
    setActivePlayer = Signal(int)
    mpvCommandFor = Signal(int, "QVariant")
    videoStarted = Signal()
    activeIndexChanged = Signal()
    currentVideoChanged = Signal(str)  # active tab's current video id
    autoplayChanged = Signal()

    def __init__(self, store: TabStore, materialize_delay_ms: int = 50,
                 url_cache=None, now_fn=time.time, live_cap: int = LIVE_CAP,
                 freeze_ttl_secs: float = FREEZE_TTL_SECS,
                 related_provider=None, mark_watched=None, parent=None):
        super().__init__(parent)
        self._store = store
        self._materialize_delay_ms = materialize_delay_ms
        self._url_cache = url_cache
        # `mark_watched(video_id)` replays the watch-history ping for loads
        # that skip ytdl_hook (URL-cache hits); None = feature off.
        self._mark_watched = mark_watched
        self._now = now_fn
        self._live_cap = max(1, live_cap)
        self._freeze_ttl = freeze_ttl_secs
        self._tabs, active_id = store.load()
        self._active = next(
            (i for i, t in enumerate(self._tabs) if t.id == active_id), -1
        )
        # No autoplay on startup: restored tabs get a player on first click.
        self._live: dict[int, _LivePlayer] = {}
        self._loading_since: float | None = None  # time-to-first-frame probe
        self._queue_model = QueueModel(self)
        self._sync_queue_model()
        # Autoplay on queue exhaustion (GUIDELINE.org): per-app, session-only,
        # opt-in flag; `related_provider(video_id)` returns cached
        # (video_id, title) pairs (RelatedModel.related_for), never fetches.
        self._autoplay = False
        self._related_provider = related_provider
        self._played: dict[int, set[str]] = {}  # tab_id -> played video ids

    # --- QAbstractListModel ---

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._tabs)

    def roleNames(self):
        return {TITLE: b"title", ACTIVE: b"active"}

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._tabs):
            return None
        tab = self._tabs[index.row()]
        if role == TITLE:
            idx = min(max(0, tab.queue_idx), len(tab.queue) - 1)
            return tab.queue[idx].title if tab.queue else ""
        if role == ACTIVE:
            return index.row() == self._active
        return None

    def _get_active_index(self) -> int:
        return self._active

    activeIndex = Property(int, _get_active_index, notify=activeIndexChanged)

    def _get_queue_model(self):
        return self._queue_model

    queueModel = Property(QObject, _get_queue_model, constant=True)

    def _get_autoplay(self):
        return self._autoplay

    autoplay = Property(bool, _get_autoplay, notify=autoplayChanged)

    @Slot()
    def toggleAutoplay(self):
        self._autoplay = not self._autoplay
        self.autoplayChanged.emit()

    # --- invokables ---

    @Slot(str, str)
    def playVideo(self, video_id: str, title: str):
        """Tap on a video: fresh queue in the active tab (created if none)."""
        queue = [QueueItem(video_id, title)]
        if self._active < 0:
            self._insert_tab(self._store.create(queue), queue)
            self._set_active(len(self._tabs) - 1)
        else:
            tab = self._tabs[self._active]
            self._store.set_queue(tab.id, queue)
            self._update_tab(self._active, Tab(tab.id, queue, 0, 0.0))
            self._sync_queue_model()
        self._materialize_active()

    @Slot(str, str)
    def openInNewTab(self, video_id: str, title: str):
        """Context menu: background tab — no player until activated."""
        queue = [QueueItem(video_id, title)]
        self._insert_tab(self._store.create(queue), queue)

    @Slot(str, str)
    def enqueue(self, video_id: str, title: str):
        self._insert_into_queue(video_id, title, at_end=True)

    @Slot(str, str)
    def playNext(self, video_id: str, title: str):
        self._insert_into_queue(video_id, title, at_end=False)

    @Slot(int)
    def activate(self, row: int):
        if not 0 <= row < len(self._tabs):
            return
        if row != self._active:
            self.persistActive()
            self._touch_active()
            self._set_active(row)
        tab = self._tabs[row]
        if tab.id in self._live:
            # The browser feel: the player is alive and paused — just show it.
            self._live[tab.id].last_active = self._now()
            self.setActivePlayer.emit(tab.id)
            self.videoStarted.emit()
            self._emit_current_video()
        else:
            # Restored active tab (startup, or a click on its strip cell)
            # has no player yet — materialize with the resume position.
            self._materialize_active()

    def _emit_current_video(self):
        if self._active < 0:
            return
        tab = self._tabs[self._active]
        if tab.queue:
            idx = min(max(0, tab.queue_idx), len(tab.queue) - 1)
            self.currentVideoChanged.emit(tab.queue[idx].video_id)

    @Slot(int)
    def closeTab(self, row: int):
        if not 0 <= row < len(self._tabs):
            return
        closing_active = row == self._active
        self.beginRemoveRows(QModelIndex(), row, row)
        tab = self._tabs.pop(row)
        self.endRemoveRows()
        self._store.delete(tab.id)
        if tab.id in self._live:
            self._freeze(tab.id)
        if not closing_active:
            if row < self._active:
                self._set_active(self._active - 1)
            return
        if not self._tabs:
            self._set_active(-1)
            return
        self._set_active(min(row, len(self._tabs) - 1), force=True)
        new_tab = self._tabs[self._active]
        if new_tab.id in self._live:
            self._live[new_tab.id].last_active = self._now()
            self.setActivePlayer.emit(new_tab.id)
            self.videoStarted.emit()
        else:
            self._materialize_active()

    # --- mpv observers (routed by tab id; the only mpv -> state flow) ---

    @Slot(int, float)
    def playbackTime(self, tab_id: int, secs: float):
        # Per-frame: memory only; sqlite writes happen in persistActive().
        if self._loading_since is not None and self._active >= 0 \
                and self._tabs[self._active].id == tab_id:
            print(f"tabs: first frame {time.monotonic() - self._loading_since:.1f}s"
                  " after load command")
            self._loading_since = None
        row = self._row_of(tab_id)
        if row is not None:
            tab = self._tabs[row]
            self._tabs[row] = Tab(tab.id, tab.queue, tab.queue_idx, secs)

    @Slot(int, int)
    def playlistPos(self, tab_id: int, pos: int):
        row = self._row_of(tab_id)
        if row is None:
            return
        if pos < 0:
            # True queue exhaustion: mpv went idle, nothing plays next.
            if row == self._active:
                self._maybe_autoplay(row)
            return
        live = self._live.get(tab_id)
        if live is None:
            return
        tab = self._tabs[row]
        if not tab.queue:
            return
        idx = min(live.offset + pos, len(tab.queue) - 1)
        self._mark_played(tab.id, tab.queue[idx].video_id)
        if idx != tab.queue_idx:
            self._update_tab(row, Tab(tab.id, tab.queue, idx, 0.0))
            self._store.save_state(tab.id, idx, 0.0)
            if row == self._active:
                self._queue_model.set_current(idx)
                self._emit_current_video()

    @Slot(int, str)
    def resolvedUrl(self, tab_id: int, resolved: str):
        """What ytdl_hook resolved for a tab's current item (its player's
        stream-open-filename), reported back by the bridge."""
        if self._url_cache is None:
            return
        row = self._row_of(tab_id)
        if row is None or not self._tabs[row].queue:
            return
        tab = self._tabs[row]
        idx = min(max(0, tab.queue_idx), len(tab.queue) - 1)
        self._url_cache.put(tab.queue[idx].video_id, resolved, self._now())

    @Slot(int)
    def loadFailed(self, tab_id: int):
        """A load errored. Retry the materialization once: cached URLs are
        invalidated first (stale despite the expiry margin), and fresh
        extractions get one more shot too — googlevideo 403s transiently."""
        live = self._live.get(tab_id)
        row = self._row_of(tab_id)
        if live is None or row is None or live.retried:
            return
        if live.used_cache and self._url_cache is not None:
            for item in self._tabs[row].queue:
                self._url_cache.invalidate(item.video_id)
        live.used_cache = False
        live.retried = True
        if row == self._active:
            self._materialize_active(use_cache=False, is_retry=True)

    # --- queue panel ops (active tab only) ---

    @Slot(int, int, result=bool)
    def moveQueueItem(self, idx: int, delta: int) -> bool:
        """Swap a FUTURE queue item with its neighbour. The current and
        past items stay put: they are not (or not correctly) addressable
        in the mpv playlist, and moving history is pointless. Returns
        whether the move happened (the panel selection follows it)."""
        tab, live = self._active_tab()
        if tab is None or delta not in (-1, 1):
            return False
        to = idx + delta
        if not (tab.queue_idx < idx < len(tab.queue)
                and tab.queue_idx < to < len(tab.queue)):
            return False
        queue = list(tab.queue)
        queue.insert(to, queue.pop(idx))
        self._store.update_queue(tab.id, queue)
        self._update_tab(self._active,
                         Tab(tab.id, queue, tab.queue_idx, tab.position_secs))
        self._queue_model.move_row(idx, to)
        if live is not None:
            frm = idx - live.offset
            # mpv playlist-move targets the entry whose place is taken:
            # moving down needs from+2, moving up from-1.
            self.mpvCommandFor.emit(
                tab.id, ["playlist-move", frm, frm + 2 if delta > 0 else frm - 1])
        return True

    @Slot(int)
    def removeQueueItem(self, idx: int):
        """Drop any queue item except the one playing."""
        tab, live = self._active_tab()
        if tab is None or not 0 <= idx < len(tab.queue) \
                or idx == tab.queue_idx:
            return
        queue = list(tab.queue)
        queue.pop(idx)
        new_idx = tab.queue_idx - (1 if idx < tab.queue_idx else 0)
        self._store.update_queue(tab.id, queue)
        if new_idx != tab.queue_idx:
            self._store.save_state(tab.id, new_idx, tab.position_secs)
        self._update_tab(self._active,
                         Tab(tab.id, queue, new_idx, tab.position_secs))
        self._queue_model.remove_row(idx)
        if live is not None:
            if idx >= live.offset:
                self.mpvCommandFor.emit(
                    tab.id, ["playlist-remove", idx - live.offset])
            else:
                # Item predates the materialization: mpv never had it,
                # only the queue->mpv offset shifts.
                live.offset -= 1

    @Slot(int)
    def jumpToQueueItem(self, idx: int):
        tab, live = self._active_tab()
        if tab is None or not 0 <= idx < len(tab.queue) \
                or idx == tab.queue_idx:
            return
        if live is not None and idx >= live.offset:
            # In the mpv playlist: play it there; the playlist-pos
            # observer brings queue_idx along.
            self.mpvCommandFor.emit(
                tab.id, ["playlist-play-index", idx - live.offset])
        else:
            # Before the offset (or tab frozen): re-materialize from it.
            self._update_tab(self._active, Tab(tab.id, tab.queue, idx, 0.0))
            self._store.save_state(tab.id, idx, 0.0)
            self._materialize_active()

    def _active_tab(self):
        if self._active < 0:
            return None, None
        tab = self._tabs[self._active]
        return tab, self._live.get(tab.id)

    def _sync_queue_model(self):
        tab, _ = self._active_tab()
        if tab is None:
            self._queue_model.reset([], -1)
        else:
            self._queue_model.reset(tab.queue, tab.queue_idx)

    @Slot()
    def persistActive(self):
        if self._active >= 0:
            tab = self._tabs[self._active]
            self._store.save_state(tab.id, tab.queue_idx, tab.position_secs)
        self._freeze_stale()

    # --- internals ---

    def _materialize_active(self, use_cache: bool = True,
                            is_retry: bool = False):
        tab = self._tabs[self._active]
        if tab.id not in self._live:
            self._ensure_capacity()
            self._live[tab.id] = _LivePlayer(0, self._now())
            self.createPlayer.emit(tab.id)
        live = self._live[tab.id]
        live.offset = min(max(0, tab.queue_idx), max(0, len(tab.queue) - 1))
        live.used_cache = False
        live.last_active = self._now()
        if not is_retry:
            live.retried = False  # user-initiated load re-arms the retry
        self._loading_since = time.monotonic()
        resolve = (lambda vid: self._resolve(live, vid)) if use_cache else None
        cmds = materialize(tab, resolve)
        self.setActivePlayer.emit(tab.id)
        self.videoStarted.emit()
        self._emit_current_video()
        if not cmds:
            return
        # Stop first and drain before loading into a reused player.
        # ecomono: mitigates (does not close) an iHD race — the decoder dies
        # under zero-copy while mpv's render thread maps an in-flight frame
        # (repro: main.py --stress). 50ms validated by that stress run.
        self.mpvCommandFor.emit(tab.id, ["stop"])

        def fire():
            for cmd in cmds:
                self.mpvCommandFor.emit(tab.id, cmd)

        if self._materialize_delay_ms <= 0:
            fire()
        else:
            QTimer.singleShot(self._materialize_delay_ms, fire)

    def _ensure_capacity(self):
        while len(self._live) >= self._live_cap:
            active_id = (self._tabs[self._active].id
                         if self._active >= 0 else None)
            candidates = [tid for tid in self._live if tid != active_id]
            if not candidates:
                return
            self._freeze(min(candidates,
                             key=lambda tid: self._live[tid].last_active))

    def _freeze(self, tab_id: int):
        """Browser-style tab discard: the strip entry stays, the player goes.
        State was persisted when the tab left the foreground."""
        del self._live[tab_id]
        self.destroyPlayer.emit(tab_id)

    def _freeze_stale(self):
        active_id = self._tabs[self._active].id if self._active >= 0 else None
        now = self._now()
        for tid in list(self._live):
            if tid != active_id and \
                    now - self._live[tid].last_active > self._freeze_ttl:
                self._freeze(tid)

    def _touch_active(self):
        if self._active >= 0:
            live = self._live.get(self._tabs[self._active].id)
            if live is not None:
                live.last_active = self._now()

    def _resolve(self, live: _LivePlayer, video_id: str) -> str:
        if self._url_cache is not None:
            cached = self._url_cache.get(video_id, self._now())
            if cached is not None:
                # A cache hit skips ytdl_hook, so yt-dlp's mark-watched
                # never runs for this load — replay the ping ourselves.
                live.used_cache = True
                if self._mark_watched is not None:
                    self._mark_watched(video_id)
                return cached
        return WATCH_URL + video_id

    def _insert_into_queue(self, video_id: str, title: str, at_end: bool,
                          resume_idle: bool = False):
        if self._active < 0:
            self.playVideo(video_id, title)
            return
        tab = self._tabs[self._active]
        queue = list(tab.queue)
        pos = len(queue) if at_end else min(tab.queue_idx + 1, len(queue))
        item = QueueItem(video_id, title)
        queue.insert(pos, item)
        self._store.update_queue(tab.id, queue)
        self._update_tab(
            self._active,
            Tab(tab.id, queue, tab.queue_idx, tab.position_secs),
        )
        self._queue_model.insert_row(pos, item)
        live = self._live.get(tab.id)
        if live is not None:
            # resume_idle: autoplay's hook -- mpv is idle (queue just
            # exhausted), so the append must also start it playing.
            flag = ("append-play" if resume_idle
                    else "append" if at_end else "insert-next")
            self.mpvCommandFor.emit(
                tab.id, ["loadfile", self._resolve(live, video_id), flag])

    def _mark_played(self, tab_id: int, video_id: str):
        self._played.setdefault(tab_id, set()).add(video_id)

    def _maybe_autoplay(self, row: int):
        """Queue exhaustion hook: when armed, enqueue the first related
        video (of the one that just finished) not already played in this
        tab this session, via the existing enqueue path."""
        if not self._autoplay or self._related_provider is None:
            return
        tab = self._tabs[row]
        if not tab.queue:
            return
        last = tab.queue[tab.queue_idx].video_id
        played = self._played.get(tab.id, set())
        for video_id, title in self._related_provider(last):
            if video_id != last and video_id not in played:
                self._insert_into_queue(video_id, title, at_end=True,
                                        resume_idle=True)
                return

    def _row_of(self, tab_id: int):
        return next((i for i, t in enumerate(self._tabs) if t.id == tab_id),
                    None)

    def _insert_tab(self, tab_id: int, queue: list[QueueItem]):
        row = len(self._tabs)
        self.beginInsertRows(QModelIndex(), row, row)
        self._tabs.append(Tab(tab_id, queue, 0, 0.0))
        self.endInsertRows()

    def _update_tab(self, row: int, tab: Tab):
        self._tabs[row] = tab
        idx = self.index(row)
        self.dataChanged.emit(idx, idx, [TITLE])

    def _set_active(self, row: int, force: bool = False):
        # `force` covers closing the active tab when the successor lands
        # on the same row index: the row is equal but the tab is not.
        if row == self._active and not force:
            return
        self._active = row
        self._store.set_active(self._tabs[row].id if row >= 0 else None)
        self.activeIndexChanged.emit()
        self._sync_queue_model()
        if len(self._tabs):
            self.dataChanged.emit(
                self.index(0), self.index(len(self._tabs) - 1), [ACTIVE]
            )
