"""Checks for tab persistence (sqlite source of truth), the pure
materialize() that turns a tab into mpv commands, and TabManager's
switch/persist logic. No mpv, no Qt event loop."""

import tempfile
from pathlib import Path

from tabstore import QueueItem, Tab, TabStore
from tabmanager import TabManager, materialize


def make_store(tmp):
    return TabStore(Path(tmp) / "mono.db")


def test_store():
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        assert store.load() == ([], None)

        q1 = [QueueItem("aaaaaaaaaaa", "A"), QueueItem("bbbbbbbbbbb", "B")]
        t1 = store.create(q1)
        t2 = store.create([QueueItem("ccccccccccc", "C")])
        store.set_active(t1)

        tabs, active = store.load()
        assert active == t1
        assert [t.id for t in tabs] == [t1, t2]
        assert tabs[0].queue == q1
        assert (tabs[0].queue_idx, tabs[0].position_secs) == (0, 0.0)

        store.save_state(t1, 1, 42.5)
        tabs, _ = store.load()
        assert (tabs[0].queue_idx, tabs[0].position_secs) == (1, 42.5)

        # set_queue replaces the queue and resets progress.
        store.set_queue(t1, [QueueItem("ddddddddddd", "D")])
        tabs, _ = store.load()
        assert tabs[0].queue == [QueueItem("ddddddddddd", "D")]
        assert (tabs[0].queue_idx, tabs[0].position_secs) == (0, 0.0)

        # delete cascades queue items and clears active if it pointed there.
        store.delete(t1)
        tabs, active = store.load()
        assert [t.id for t in tabs] == [t2]
        assert active is None

        # Reopen: state survives (new connection, same file).
        store2 = make_store(tmp)
        tabs, _ = store2.load()
        assert [t.id for t in tabs] == [t2]
    print("tab store: ok")


def url(vid):
    return "https://www.youtube.com/watch?v=" + vid


def test_materialize():
    q = [QueueItem("aaaaaaaaaaa", "A"), QueueItem("bbbbbbbbbbb", "B"),
         QueueItem("ccccccccccc", "C")]

    assert materialize(Tab(1, [], 0, 0.0)) == []

    # Fresh single-item tab: plain replace, no start option.
    assert materialize(Tab(1, q[:1], 0, 0.0)) == [
        ["loadfile", url("aaaaaaaaaaa"), "replace"]
    ]

    # Resume position rides the loadfile as a per-item option.
    assert materialize(Tab(1, q[:1], 0, 12.5)) == [
        ["loadfile", url("aaaaaaaaaaa"), "replace", -1, {"start": "12.5"}]
    ]

    # Mid-queue: current item replaces, the rest append; earlier items live
    # only in sqlite (mpv needs current + upcoming for auto-advance).
    assert materialize(Tab(1, q, 1, 3.0)) == [
        ["loadfile", url("bbbbbbbbbbb"), "replace", -1, {"start": "3.0"}],
        ["loadfile", url("ccccccccccc"), "append"],
    ]

    # Corrupt index clamps instead of crashing.
    assert materialize(Tab(1, q[:1], 5, 0.0)) == [
        ["loadfile", url("aaaaaaaaaaa"), "replace"]
    ]
    print("materialize: ok")


def test_manager():
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        m = TabManager(store, materialize_delay_ms=0)
        cmds = []
        m.mpvCommand.connect(cmds.append)

        # First play creates a tab, activates it, materializes.
        m.playVideo("aaaaaaaaaaa", "A")
        assert m.activeIndex == 0
        assert m.rowCount() == 1
        assert cmds == [["stop"], ["loadfile", url("aaaaaaaaaaa"), "replace"]]

        # Background tab: persisted, not materialized, active unchanged.
        cmds.clear()
        m.playbackTime(33.0)
        m.openInNewTab("bbbbbbbbbbb", "B")
        assert (m.rowCount(), m.activeIndex, cmds) == (2, 0, [])

        # Switch: outgoing position persisted, incoming materialized.
        m.activate(1)
        assert m.activeIndex == 1
        assert cmds == [["stop"], ["loadfile", url("bbbbbbbbbbb"), "replace"]]
        tabs, _ = store.load()
        assert tabs[0].position_secs == 33.0

        # Activating the already-materialized tab is a no-op.
        cmds.clear()
        m.activate(1)
        assert cmds == []

        # Closing the active tab activates a neighbour and resumes it.
        m.closeTab(1)
        assert m.activeIndex == 0
        assert cmds == [
            ["stop"],
            ["loadfile", url("aaaaaaaaaaa"), "replace", -1, {"start": "33.0"}],
        ]

        # Closing the last tab stops playback.
        cmds.clear()
        m.closeTab(0)
        assert (m.rowCount(), m.activeIndex, cmds) == (0, -1, [["stop"]])

    # Auto-advance: playlist-pos maps back through the materialize offset.
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        q = [QueueItem("aaaaaaaaaaa", "A"), QueueItem("bbbbbbbbbbb", "B"),
             QueueItem("ccccccccccc", "C")]
        tid = store.create(q)
        store.save_state(tid, 1, 0.0)
        m = TabManager(store, materialize_delay_ms=0)
        m.mpvCommand.connect(lambda _: None)

        m.activate(0)  # materialized at queue_idx 1 -> offset 1
        m.playlistPos(1)  # mpv advanced to its playlist item 1 -> queue idx 2
        m.persistActive()
        tabs, _ = store.load()
        assert tabs[0].queue_idx == 2
        # Stray -1 (idle) is ignored.
        m.playlistPos(-1)
        m.persistActive()
        tabs, _ = store.load()
        assert tabs[0].queue_idx == 2
    print("tab manager: ok")


def test_enqueue_playnext():
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        m = TabManager(store, materialize_delay_ms=0)
        cmds = []
        m.mpvCommand.connect(cmds.append)

        # Enqueue with no tabs behaves like playVideo (creates + plays).
        m.enqueue("aaaaaaaaaaa", "A")
        assert cmds == [["stop"], ["loadfile", url("aaaaaaaaaaa"), "replace"]]

        # Enqueue on the materialized active tab: sqlite grows, mpv appends.
        cmds.clear()
        m.enqueue("bbbbbbbbbbb", "B")
        tabs, _ = store.load()
        assert [q.video_id for q in tabs[0].queue] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
        assert (tabs[0].queue_idx, tabs[0].position_secs) == (0, 0.0)  # no reset
        assert cmds == [["loadfile", url("bbbbbbbbbbb"), "append"]]

        # Play next inserts right after the current item, mpv insert-next.
        cmds.clear()
        m.playNext("ccccccccccc", "C")
        tabs, _ = store.load()
        assert [q.video_id for q in tabs[0].queue] == [
            "aaaaaaaaaaa", "ccccccccccc", "bbbbbbbbbbb"
        ]
        assert cmds == [["loadfile", url("ccccccccccc"), "insert-next"]]

        # On a restored (not materialized) tab: sqlite only, no mpv commands.
        store2 = make_store(tmp)
        m2 = TabManager(store2, materialize_delay_ms=0)
        cmds2 = []
        m2.mpvCommand.connect(cmds2.append)
        m2.enqueue("ddddddddddd", "D")
        m2.playNext("eeeeeeeeeee", "E")
        tabs, _ = store2.load()
        assert [q.video_id for q in tabs[0].queue] == [
            "aaaaaaaaaaa", "eeeeeeeeeee", "ccccccccccc", "bbbbbbbbbbb",
            "ddddddddddd",
        ]
        assert cmds2 == []
    print("enqueue/play-next: ok")


if __name__ == "__main__":
    test_store()
    test_materialize()
    test_manager()
    test_enqueue_playnext()
    print("all checks passed")
