"""Checks for the related-videos model: LRU-cached per video id (GUIDELINE:
related is a function of video_id, not tab state), fetch injected."""

import asyncio

from innertube import Video
from related import RelatedModel


def test_related_model():
    calls = []

    async def fetch(client, video_id):
        calls.append(video_id)
        return ("UCowner", "Chan", [
            Video("bbbbbbbbbbb", "Rel", "c", "1:00", "https://t/x.jpg", "UCrel"),
        ])

    m = RelatedModel(client=None, fetch_fn=fetch, cache_size=2)
    changes = []
    m.changed.connect(lambda: changes.append(1))

    asyncio.run(m._load("aaaaaaaaaaa"))
    assert calls == ["aaaaaaaaaaa"]
    assert m.channelId == "UCowner"
    assert m.channelName == "Chan"
    assert m.items == [{
        "videoId": "bbbbbbbbbbb", "title": "Rel", "channel": "c",
        "duration": "1:00", "channelId": "UCrel",
    }]
    assert changes  # UI notified

    # Same video again: served from cache, no second fetch.
    asyncio.run(m._load("aaaaaaaaaaa"))
    assert calls == ["aaaaaaaaaaa"]

    # LRU: capacity 2 — a third distinct video evicts the oldest.
    asyncio.run(m._load("ccccccccccc"))
    asyncio.run(m._load("ddddddddddd"))
    asyncio.run(m._load("aaaaaaaaaaa"))
    assert calls == ["aaaaaaaaaaa", "ccccccccccc", "ddddddddddd", "aaaaaaaaaaa"]

    # A failing fetch degrades to empty, never raises.
    async def boom(client, video_id):
        raise RuntimeError("network")

    m2 = RelatedModel(client=None, fetch_fn=boom)
    asyncio.run(m2._load("eeeeeeeeeee"))
    assert m2.items == []
    assert not m2.loading
    print("related model: ok")


if __name__ == "__main__":
    test_related_model()
    print("all checks passed")
