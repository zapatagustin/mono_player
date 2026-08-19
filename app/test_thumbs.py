"""Checks for the thumbnail disk LRU: eviction is mtime-based, oldest first
(app/thumbs.py, ThumbCache._evict).

Note: this duplicates coverage already in app/test_feed.py::test_thumb_cache
-- kept as a separate file per the fix spec that requested it."""

import os
import tempfile
from pathlib import Path

from thumbs import ThumbCache


def test_thumb_cache_evicts_oldest():
    with tempfile.TemporaryDirectory() as tmp:
        cache = ThumbCache(Path(tmp), max_files=3)

        p1 = cache.put("aaaaaaaaaaa", b"1")
        p2 = cache.put("bbbbbbbbbbb", b"2")
        p3 = cache.put("ccccccccccc", b"3")

        # Make recency deterministic: a oldest, then b, then c.
        for i, p in enumerate([p1, p2, p3]):
            os.utime(p, (i, i))

        # Fourth insert pushes past the cap: the oldest (a) is evicted,
        # the rest survive.
        cache.put("ddddddddddd", b"4")
        assert cache.get("aaaaaaaaaaa") is None
        assert cache.get("bbbbbbbbbbb") is not None
        assert cache.get("ccccccccccc") is not None
        assert cache.get("ddddddddddd") is not None
    print("thumb cache eviction: ok")


if __name__ == "__main__":
    test_thumb_cache_evicts_oldest()
    print("all checks passed")
