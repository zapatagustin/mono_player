"""Checks for the resolved-stream-URL cache: entries live until the
earliest googlevideo expire= embedded in the URL/EDL, minus a safety
margin; unparseable URLs get a conservative TTL."""

from urlcache import MARGIN_SECS, FALLBACK_TTL_SECS, StreamUrlCache

NOW = 1_700_000_000


def test_url_cache():
    c = StreamUrlCache()
    assert c.get("aaaaaaaaaaa", now=NOW) is None

    # Plain URL with expire= query param.
    url = f"https://rr4.googlevideo.com/videoplayback?expire={NOW + 3600}&x=1"
    c.put("aaaaaaaaaaa", url, now=NOW)
    assert c.get("aaaaaaaaaaa", now=NOW) == url
    # Still valid just before expire-margin, gone after.
    assert c.get("aaaaaaaaaaa", now=NOW + 3600 - MARGIN_SECS - 1) == url
    assert c.get("aaaaaaaaaaa", now=NOW + 3600 - MARGIN_SECS + 1) is None

    # EDL with two URLs: the EARLIEST expire wins.
    edl = (f"edl://%100%https://a.googlevideo.com/v?expire={NOW + 7200};"
           f"%100%https://b.googlevideo.com/a?expire={NOW + 1800}")
    c.put("bbbbbbbbbbb", edl, now=NOW)
    assert c.get("bbbbbbbbbbb", now=NOW + 1800 - MARGIN_SECS - 1) == edl
    assert c.get("bbbbbbbbbbb", now=NOW + 1800 - MARGIN_SECS + 1) is None

    # No parseable expire: conservative fallback TTL.
    c.put("ccccccccccc", "https://cdn.example/stream.m3u8", now=NOW)
    assert c.get("ccccccccccc", now=NOW + FALLBACK_TTL_SECS - 1) is not None
    assert c.get("ccccccccccc", now=NOW + FALLBACK_TTL_SECS + 1) is None

    # Caching the resolved form of an ORIGINAL youtube page URL must be
    # refused — re-loading it through the cache would skip ytdl entirely.
    c.put("ddddddddddd", "https://www.youtube.com/watch?v=ddddddddddd", now=NOW)
    assert c.get("ddddddddddd", now=NOW) is None

    # invalidate() drops the entry.
    c.invalidate("aaaaaaaaaaa")
    assert c.get("aaaaaaaaaaa", now=NOW) is None
    print("url cache: ok")


if __name__ == "__main__":
    test_url_cache()
    print("all checks passed")
