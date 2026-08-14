"""Checks for tab persistence (sqlite source of truth), the pure
materialize() that turns a tab into mpv commands, and TabManager's
switch/persist logic. No mpv, no Qt event loop."""

import tempfile
from pathlib import Path

from tabstore import QueueItem, Tab, TabStore
from tabmanager import TabManager, materialize
from urlcache import StreamUrlCache


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


def collect(m):
    """Ordered event log of every player-directed signal."""
    events = []
    m.createPlayer.connect(lambda tid: events.append(("create", tid)))
    m.destroyPlayer.connect(lambda tid: events.append(("destroy", tid)))
    m.setActivePlayer.connect(lambda tid: events.append(("show", tid)))
    m.mpvCommandFor.connect(lambda tid, cmd: events.append(("cmd", tid, cmd)))
    return events


def make_manager(store, **kw):
    kw.setdefault("materialize_delay_ms", 0)
    kw.setdefault("now_fn", lambda: 1000.0)
    return TabManager(store, **kw)


def test_pool_switching():
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        m = make_manager(store)
        ev = collect(m)

        # First play: player created for the tab, shown, materialized.
        m.playVideo("aaaaaaaaaaa", "A")
        t1 = store.load()[0][0].id
        assert ev == [
            ("create", t1), ("show", t1),
            ("cmd", t1, ["stop"]),
            ("cmd", t1, ["loadfile", url("aaaaaaaaaaa"), "replace"]),
        ]

        # Background tab: no player until activated.
        ev.clear()
        m.openInNewTab("bbbbbbbbbbb", "B")
        t2 = store.load()[0][1].id
        assert ev == []

        m.activate(1)
        assert ev == [
            ("create", t2), ("show", t2),
            ("cmd", t2, ["stop"]),
            ("cmd", t2, ["loadfile", url("bbbbbbbbbbb"), "replace"]),
        ]

        # Switching back to a LIVE tab: show only — the browser feel.
        ev.clear()
        m.activate(0)
        assert ev == [("show", t1)]

        # Replacing the video of the active tab reloads its own player.
        ev.clear()
        m.playVideo("ccccccccccc", "C")
        assert ev == [
            ("show", t1),
            ("cmd", t1, ["stop"]),
            ("cmd", t1, ["loadfile", url("ccccccccccc"), "replace"]),
        ]
    print("pool switching: ok")


def test_pool_evict():
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        clock = [1000.0]
        m = make_manager(store, now_fn=lambda: clock[0], live_cap=2,
                         freeze_ttl_secs=1800)
        ev = collect(m)

        vids = ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]
        for v in vids:
            m.openInNewTab(v, v)
        tabs, _ = store.load()
        ids = [t.id for t in tabs]

        # Activate 1 then 2: both live (cap 2).
        m.activate(0)
        clock[0] += 10
        m.activate(1)
        assert ("destroy", ids[0]) not in ev

        # Activating a third: LRU (tab 1) frozen.
        clock[0] += 10
        ev.clear()
        m.playbackTime(ids[0], 33.0)  # position noted while it was live
        m.activate(2)
        assert ev[0] == ("destroy", ids[0])

        # Returning to the frozen tab re-materializes with resume position.
        clock[0] += 10
        ev.clear()
        m.activate(0)
        assert ("create", ids[0]) in ev
        assert ("cmd", ids[0],
                ["loadfile", url(vids[0]), "replace", -1, {"start": "33.0"}]) in ev

        # TTL: background player idle past the TTL is frozen by the
        # heartbeat; the active one never is.
        clock[0] += 1801
        ev.clear()
        m.persistActive()
        assert ("destroy", ids[2]) in ev
        assert ("destroy", ids[0]) not in ev
    print("pool evict: ok")


def test_pool_close():
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        m = make_manager(store)
        ev = collect(m)

        m.playVideo("aaaaaaaaaaa", "A")
        m.openInNewTab("bbbbbbbbbbb", "B")
        m.activate(1)
        tabs, _ = store.load()
        t1, t2 = tabs[0].id, tabs[1].id

        # Closing the active tab with a live neighbour: destroy + show only.
        ev.clear()
        m.closeTab(1)
        assert ev == [("destroy", t2), ("show", t1)]

        # Closing the last tab: destroy, no further commands.
        ev.clear()
        m.closeTab(0)
        assert ev == [("destroy", t1)]
        assert (m.rowCount(), m.activeIndex) == (0, -1)
    print("pool close: ok")


def test_per_tab_state():
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        cache = StreamUrlCache()
        m = make_manager(store, url_cache=cache)
        collect(m)

        m.playVideo("aaaaaaaaaaa", "A")
        m.openInNewTab("bbbbbbbbbbb", "B")
        m.activate(1)
        tabs, _ = store.load()
        t1, t2 = tabs[0].id, tabs[1].id

        # playbackTime is routed by tab id, not "whatever is active".
        m.playbackTime(t1, 11.0)
        m.playbackTime(t2, 22.0)
        m.persistActive()
        tabs, _ = store.load()
        assert tabs[1].position_secs == 22.0

        # resolvedUrl is credited to the reporting tab's current video.
        resolved = "https://rr1.googlevideo.com/videoplayback?expire=1704067200"
        m.resolvedUrl(t1, resolved)
        assert cache.get("aaaaaaaaaaa", now=1000.0) == resolved

        # playlistPos routed by id too (single-item queue: stays 0).
        m.playlistPos(t1, 0)
    print("per-tab state: ok")


def test_enqueue_playnext():
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        m = make_manager(store)
        ev = collect(m)

        m.playVideo("aaaaaaaaaaa", "A")
        t1 = store.load()[0][0].id

        # Enqueue on the live active tab: sqlite grows, its player appends.
        ev.clear()
        m.enqueue("bbbbbbbbbbb", "B")
        tabs, _ = store.load()
        assert [q.video_id for q in tabs[0].queue] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
        assert (tabs[0].queue_idx, tabs[0].position_secs) == (0, 0.0)
        assert ev == [("cmd", t1, ["loadfile", url("bbbbbbbbbbb"), "append"])]

        # Play next inserts after current, player gets insert-next.
        ev.clear()
        m.playNext("ccccccccccc", "C")
        tabs, _ = store.load()
        assert [q.video_id for q in tabs[0].queue] == [
            "aaaaaaaaaaa", "ccccccccccc", "bbbbbbbbbbb"
        ]
        assert ev == [("cmd", t1, ["loadfile", url("ccccccccccc"), "insert-next"])]

    # On a restored (never-activated) tab: sqlite only, no commands.
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        store.create([QueueItem("aaaaaaaaaaa", "A")])
        store.set_active(store.load()[0][0].id)
        m = make_manager(store)
        ev = collect(m)
        m.enqueue("ddddddddddd", "D")
        tabs, _ = store.load()
        assert [q.video_id for q in tabs[0].queue] == ["aaaaaaaaaaa", "ddddddddddd"]
        assert ev == []
    print("enqueue/play-next: ok")


def test_restore_and_activate_active_row():
    # After a restart the active tab exists but has NO live player.
    # Activating it (startup resume, or clicking its strip cell) must
    # materialize it with the persisted resume position.
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        tid = store.create([QueueItem("aaaaaaaaaaa", "A")])
        store.save_state(tid, 0, 42.5)
        store.set_active(tid)

        m = make_manager(store)
        ev = collect(m)
        assert m.activeIndex == 0
        m.activate(0)  # same row as the restored active index
        assert ("create", tid) in ev
        assert ("cmd", tid,
                ["loadfile", url("aaaaaaaaaaa"), "replace", -1,
                 {"start": "42.5"}]) in ev

        # Activating the active row while its player is live stays cheap:
        # a re-show, no reload commands.
        ev.clear()
        m.activate(0)
        assert ev == [("show", tid)]
    print("restore/activate active row: ok")


def test_resolved_url_cache():
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        cache = StreamUrlCache()
        m = make_manager(store, url_cache=cache, live_cap=1)
        ev = collect(m)

        # Play, resolve, then freeze the tab by activating another.
        m.playVideo("aaaaaaaaaaa", "A")
        t1 = store.load()[0][0].id
        resolved = "https://rr1.googlevideo.com/videoplayback?expire=1704067200"
        m.resolvedUrl(t1, resolved)
        m.openInNewTab("bbbbbbbbbbb", "B")
        m.activate(1)  # cap 1: t1 frozen

        # Back to the frozen tab: re-materializes via the cached stream.
        ev.clear()
        m.activate(0)
        assert ("cmd", t1, ["loadfile", resolved, "replace"]) in ev

        # Stale cached stream: invalidate, retry once via the page URL.
        ev.clear()
        m.loadFailed(t1)
        assert ("cmd", t1, ["loadfile", url("aaaaaaaaaaa"), "replace"]) in ev
        ev.clear()
        m.loadFailed(t1)
        assert ev == []
    print("resolved url cache: ok")


def test_fresh_load_retry():
    # A FRESH (non-cached) load that errors — transient 403s on freshly
    # extracted URLs happen — retries the materialization once, then stops.
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        m = make_manager(store)
        ev = collect(m)

        m.playVideo("aaaaaaaaaaa", "A")
        t1 = store.load()[0][0].id
        ev.clear()
        m.loadFailed(t1)
        assert ("cmd", t1, ["loadfile", url("aaaaaaaaaaa"), "replace"]) in ev
        ev.clear()
        m.loadFailed(t1)  # second failure: no retry loop
        assert ev == []

        # A fresh user-initiated load re-arms the retry.
        m.playVideo("bbbbbbbbbbb", "B")
        ev.clear()
        m.loadFailed(t1)
        assert ("cmd", t1, ["loadfile", url("bbbbbbbbbbb"), "replace"]) in ev
    print("fresh load retry: ok")


def queue_titles(m):
    qm = m.queueModel
    return [qm.data(qm.index(i), qm.TITLE) for i in range(qm.rowCount())]


def current_row(m):
    qm = m.queueModel
    return next((i for i in range(qm.rowCount())
                 if qm.data(qm.index(i), qm.CURRENT)), -1)


def test_queue_ops():
    # Live tab, offset 0: queue idx == mpv playlist index.
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        m = make_manager(store)
        ev = collect(m)
        resets = []
        m.queueModel.modelReset.connect(lambda: resets.append(1))

        m.playVideo("aaaaaaaaaaa", "A")
        t1 = store.load()[0][0].id
        for vid, title in [("bbbbbbbbbbb", "B"), ("ccccccccccc", "C"),
                           ("ddddddddddd", "D")]:
            m.enqueue(vid, title)
        assert queue_titles(m) == ["A", "B", "C", "D"]
        assert current_row(m) == 0

        # Move down / up: sqlite + model + mpv playlist-move (target entry
        # semantics: down = from+2, up = from-1).
        ev.clear()
        resets.clear()
        m.moveQueueItem(1, 1)
        assert queue_titles(m) == ["A", "C", "B", "D"]
        assert ev == [("cmd", t1, ["playlist-move", 1, 3])]
        ev.clear()
        m.moveQueueItem(2, -1)
        assert queue_titles(m) == ["A", "B", "C", "D"]
        assert ev == [("cmd", t1, ["playlist-move", 2, 1])]

        # Rejected: the current item, targets at/before it, out of bounds.
        ev.clear()
        for idx, delta in [(0, 1), (1, -1), (3, 1), (-1, 1), (9, 1)]:
            m.moveQueueItem(idx, delta)
        assert queue_titles(m) == ["A", "B", "C", "D"]
        assert ev == []

        # Remove a future item; the current one is refused.
        m.removeQueueItem(2)
        assert queue_titles(m) == ["A", "B", "D"]
        assert ev == [("cmd", t1, ["playlist-remove", 2])]
        ev.clear()
        m.removeQueueItem(0)
        assert queue_titles(m) == ["A", "B", "D"]
        assert ev == []

        # All in-place mutations were granular: zero model resets.
        assert resets == []

        # Jump: command only; queue_idx follows via the mpv observer.
        m.jumpToQueueItem(1)
        assert ev == [("cmd", t1, ["playlist-play-index", 1])]
        m.playlistPos(t1, 1)
        assert store.load()[0][0].queue_idx == 1
        assert current_row(m) == 1

    # Restored tab materialized at queue_idx 1: offset maps queue<->mpv.
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        tid = store.create([QueueItem("aaaaaaaaaaa", "A"),
                            QueueItem("bbbbbbbbbbb", "B"),
                            QueueItem("ccccccccccc", "C")])
        store.save_state(tid, 1, 0.0)
        store.set_active(tid)
        m = make_manager(store)
        ev = collect(m)
        m.activate(0)  # offset 1: mpv playlist = [B, C]
        m.enqueue("ddddddddddd", "D")  # mpv playlist = [B, C, D]
        ev.clear()

        m.moveQueueItem(2, 1)  # C after D: mpv from 1, down -> target 3
        assert queue_titles(m) == ["A", "B", "D", "C"]
        assert ev == [("cmd", tid, ["playlist-move", 1, 3])]
        ev.clear()

        # Removing a PAST item (before the offset): sqlite only, the mpv
        # playlist never contained it; queue_idx and offset shift down.
        m.removeQueueItem(0)
        assert queue_titles(m) == ["B", "D", "C"]
        assert store.load()[0][0].queue_idx == 0
        assert current_row(m) == 0
        assert ev == []

        # After the shift the queue<->mpv mapping must still line up.
        m.removeQueueItem(1)  # D: mpv index 1 now that offset is 0
        assert queue_titles(m) == ["B", "C"]
        assert ev == [("cmd", tid, ["playlist-remove", 1])]
        m.playlistPos(tid, 1)
        assert store.load()[0][0].queue_idx == 1
        assert current_row(m) == 1

    # Frozen active tab (restored, never activated): sqlite only, no player.
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        tid = store.create([QueueItem("aaaaaaaaaaa", "A"),
                            QueueItem("bbbbbbbbbbb", "B"),
                            QueueItem("ccccccccccc", "C")])
        store.set_active(tid)
        m = make_manager(store)
        ev = collect(m)
        m.moveQueueItem(1, 1)
        m.removeQueueItem(1)
        tabs, _ = store.load()
        assert [q.title for q in tabs[0].queue] == ["A", "B"]
        assert ev == []
    print("queue ops: ok")


def test_close_active_tab_persists_successor():
    # Closing the active tab when the successor lands on the SAME row
    # index must still persist the new active id (regression: _set_active
    # early-returned on equal row, leaving sqlite pointing at the deleted
    # tab — session restore then lost the active tab entirely).
    with tempfile.TemporaryDirectory() as tmp:
        store = make_store(tmp)
        m = make_manager(store)
        m.playVideo("aaaaaaaaaaa", "A")
        m.openInNewTab("bbbbbbbbbbb", "B")
        m.activate(0)
        m.closeTab(0)  # successor (B) is now row 0 == old active row
        tabs, active = store.load()
        assert [t.id for t in tabs] == [tabs[0].id]
        assert active == tabs[0].id
        assert queue_titles(m) == ["B"]
    print("close active tab persists successor: ok")


if __name__ == "__main__":
    test_store()
    test_materialize()
    test_pool_switching()
    test_pool_evict()
    test_pool_close()
    test_restore_and_activate_active_row()
    test_per_tab_state()
    test_enqueue_playnext()
    test_resolved_url_cache()
    test_fresh_load_retry()
    test_queue_ops()
    test_close_active_tab_persists_successor()
    print("all checks passed")
