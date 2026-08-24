"""Checks for FeedStore persistence: cold start paints cached content before
any network (GUIDELINE.org, Network), so a round trip must lose nothing --
channel_id (open-channel `gc`) and playlist_id (gating) included."""

import tempfile
from pathlib import Path

from feedstore import FeedStore
from innertube import Video


def test_feed_store_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        store = FeedStore(Path(tmp) / "mono.db")
        assert store.load() == []
        videos = [
            Video("dQw4w9WgXcQ", "One", "c1", "1:23", "https://t/1.jpg",
                  "UC1", "1.2M views · 3 days ago", ""),
            Video("", "Two", "c2", "", "https://t/2.jpg",
                  "UC2", "42 videos", "PL2"),
        ]
        store.save(videos)
        assert store.load() == videos
        # save() replaces, order preserved.
        store.save(list(reversed(videos)))
        assert store.load() == list(reversed(videos))
        # append() adds continuation rows after `start`, no full rewrite.
        more = [Video("aqz-KE-bpKQ", "Three", "c3", "0:30", "https://t/3.jpg")]
        store.append(more, start=2)
        assert store.load() == list(reversed(videos)) + more
    print("feed store round trip: ok")


if __name__ == "__main__":
    test_feed_store_round_trip()
    print("all checks passed")
