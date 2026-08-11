"""Tab strip model + the one place tab state meets mpv. Sync is one-way:
commands go out via mpvCommand, only playlist-pos/playback-time flow back."""

import time

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    Property,
    Qt,
    QTimer,
    Signal,
    Slot,
)

from tabstore import QueueItem, Tab, TabStore

TITLE, ACTIVE = range(Qt.ItemDataRole.UserRole + 1, Qt.ItemDataRole.UserRole + 3)

WATCH_URL = "https://www.youtube.com/watch?v="


def materialize(tab: Tab) -> list[list]:
    """Commands that load a tab into mpv: current item replaces the playlist
    (resume position as a per-item option), upcoming items append so mpv can
    auto-advance. Earlier items live only in sqlite."""
    if not tab.queue:
        return []
    idx = min(max(0, tab.queue_idx), len(tab.queue) - 1)
    current = ["loadfile", WATCH_URL + tab.queue[idx].video_id, "replace"]
    if tab.position_secs > 0:
        current += [-1, {"start": str(tab.position_secs)}]
    return [current] + [
        ["loadfile", WATCH_URL + item.video_id, "append"]
        for item in tab.queue[idx + 1:]
    ]


class TabManager(QAbstractListModel):
    mpvCommand = Signal("QVariant")
    videoStarted = Signal()
    activeIndexChanged = Signal()

    def __init__(self, store: TabStore, materialize_delay_ms: int = 150,
                 parent=None):
        super().__init__(parent)
        self._store = store
        self._materialize_delay_ms = materialize_delay_ms
        self._tabs, active_id = store.load()
        self._active = next(
            (i for i, t in enumerate(self._tabs) if t.id == active_id), -1
        )
        # No autoplay on startup: tabs are restored, none is materialized
        # until clicked. _materialized tracks which tab owns mpv's playlist.
        self._materialized: int | None = None
        self._offset = 0  # sqlite queue_idx at materialize time
        self._loading_since: float | None = None  # time-to-first-frame probe

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
        self._materialize_active()

    @Slot(str, str)
    def openInNewTab(self, video_id: str, title: str):
        """Context menu: background tab, active/current playback untouched."""
        queue = [QueueItem(video_id, title)]
        self._insert_tab(self._store.create(queue), queue)

    @Slot(str, str)
    def enqueue(self, video_id: str, title: str):
        """Append to the active tab's queue; mpv appends only if that tab
        owns mpv's playlist right now."""
        self._insert_into_queue(video_id, title, at_end=True)

    @Slot(str, str)
    def playNext(self, video_id: str, title: str):
        """Insert right after the current item."""
        self._insert_into_queue(video_id, title, at_end=False)

    def _insert_into_queue(self, video_id: str, title: str, at_end: bool):
        if self._active < 0:
            self.playVideo(video_id, title)
            return
        tab = self._tabs[self._active]
        queue = list(tab.queue)
        pos = len(queue) if at_end else min(tab.queue_idx + 1, len(queue))
        queue.insert(pos, QueueItem(video_id, title))
        self._store.update_queue(tab.id, queue)
        self._update_tab(
            self._active,
            Tab(tab.id, queue, tab.queue_idx, tab.position_secs),
        )
        if self._materialized == tab.id:
            flag = "append" if at_end else "insert-next"
            self.mpvCommand.emit(["loadfile", WATCH_URL + video_id, flag])

    @Slot(int)
    def activate(self, row: int):
        if not 0 <= row < len(self._tabs):
            return
        already_playing = (
            row == self._active
            and self._materialized == self._tabs[row].id
        )
        if already_playing:
            return
        self.persistActive()
        self._set_active(row)
        self._materialize_active()

    @Slot(int)
    def closeTab(self, row: int):
        if not 0 <= row < len(self._tabs):
            return
        closing_active = row == self._active
        self.beginRemoveRows(QModelIndex(), row, row)
        tab = self._tabs.pop(row)
        self.endRemoveRows()
        self._store.delete(tab.id)
        if self._materialized == tab.id:
            self._materialized = None
        if not closing_active:
            if row < self._active:
                self._set_active(self._active - 1)
            return
        if not self._tabs:
            self._set_active(-1)
            self.mpvCommand.emit(["stop"])
            return
        self._set_active(min(row, len(self._tabs) - 1))
        self._materialize_active()

    # --- mpv observers (the only mpv -> state flow) ---

    @Slot(float)
    def playbackTime(self, secs: float):
        # Per-frame: memory only; sqlite writes happen in persistActive().
        if self._loading_since is not None:
            print(f"tabs: first frame {time.monotonic() - self._loading_since:.1f}s"
                  " after load command")
            self._loading_since = None
        if self._active >= 0:
            tab = self._tabs[self._active]
            self._tabs[self._active] = Tab(tab.id, tab.queue, tab.queue_idx, secs)

    @Slot(int)
    def playlistPos(self, pos: int):
        if pos < 0 or self._active < 0:
            return
        tab = self._tabs[self._active]
        if self._materialized != tab.id or not tab.queue:
            return
        idx = min(self._offset + pos, len(tab.queue) - 1)
        if idx != tab.queue_idx:
            self._update_tab(self._active, Tab(tab.id, tab.queue, idx, 0.0))
            self._store.save_state(tab.id, idx, 0.0)

    @Slot()
    def persistActive(self):
        if self._active >= 0:
            tab = self._tabs[self._active]
            self._store.save_state(tab.id, tab.queue_idx, tab.position_secs)

    # --- internals ---

    def _materialize_active(self):
        tab = self._tabs[self._active]
        self._offset = min(max(0, tab.queue_idx), max(0, len(tab.queue) - 1))
        self._materialized = tab.id
        self._loading_since = time.monotonic()
        cmds = materialize(tab)
        if not cmds:
            return
        # Stop first and let the outgoing decoder tear down before loading.
        # ecomono: mitigates (does not close) a driver race — the iHD decoder
        # dies under zero-copy while mpv's render thread maps an in-flight
        # frame (vaSyncSurface segfault, repro: main.py --stress). Remove the
        # delay when a driver/mpv update survives that stress run.
        self.mpvCommand.emit(["stop"])

        def fire():
            for cmd in cmds:
                self.mpvCommand.emit(cmd)
            self.videoStarted.emit()

        if self._materialize_delay_ms <= 0:
            fire()
        else:
            QTimer.singleShot(self._materialize_delay_ms, fire)

    def _insert_tab(self, tab_id: int, queue: list[QueueItem]):
        row = len(self._tabs)
        self.beginInsertRows(QModelIndex(), row, row)
        self._tabs.append(Tab(tab_id, queue, 0, 0.0))
        self.endInsertRows()

    def _update_tab(self, row: int, tab: Tab):
        self._tabs[row] = tab
        idx = self.index(row)
        self.dataChanged.emit(idx, idx, [TITLE])

    def _set_active(self, row: int):
        if row == self._active:
            return
        self._active = row
        self._store.set_active(self._tabs[row].id if row >= 0 else None)
        self.activeIndexChanged.emit()
        if len(self._tabs):
            self.dataChanged.emit(
                self.index(0), self.index(len(self._tabs) - 1), [ACTIVE]
            )
