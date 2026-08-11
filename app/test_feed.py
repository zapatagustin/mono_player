"""Checks for the InnerTube search parser (trust boundary: malformed data
must degrade, never crash), the thumbnail disk LRU, and the feed cache."""

import os
import tempfile
from pathlib import Path

from innertube import Video, parse_next, parse_playlist, parse_search
from thumbs import ThumbCache
from feedstore import FeedStore


def video_renderer(vid, title, channel="chan", duration="1:23", thumb="https://t/x.jpg"):
    return {
        "videoRenderer": {
            "videoId": vid,
            "title": {"runs": [{"text": title}]},
            "ownerText": {"runs": [{"text": channel}]},
            "lengthText": {"simpleText": duration},
            "thumbnail": {"thumbnails": [{"url": "https://t/lo.jpg"}, {"url": thumb}]},
        }
    }


def search_response(items):
    return {
        "contents": {
            "twoColumnSearchResultsRenderer": {
                "primaryContents": {
                    "sectionListRenderer": {
                        "contents": [{"itemSectionRenderer": {"contents": items}}]
                    }
                }
            }
        }
    }


def test_parser():
    # Well-formed: two videos, highest-res thumbnail picked.
    data = search_response(
        [video_renderer("dQw4w9WgXcQ", "One"), video_renderer("aqz-KE-bpKQ", "Two")]
    )
    videos = parse_search(data)
    assert videos == [
        Video("dQw4w9WgXcQ", "One", "chan", "1:23", "https://t/x.jpg"),
        Video("aqz-KE-bpKQ", "Two", "chan", "1:23", "https://t/x.jpg"),
    ], videos

    # Non-video items (shelves, ads) are skipped, not fatal.
    data = search_response(
        [{"shelfRenderer": {}}, video_renderer("aqz-KE-bpKQ", "Two")]
    )
    assert [v.video_id for v in parse_search(data)] == ["aqz-KE-bpKQ"]

    # Live video: no lengthText -> empty duration, still included.
    item = video_renderer("aqz-KE-bpKQ", "Live")
    del item["videoRenderer"]["lengthText"]
    assert parse_search(search_response([item]))[0].duration == ""

    # Missing videoId or title -> item skipped.
    item = video_renderer("aqz-KE-bpKQ", "x")
    del item["videoRenderer"]["videoId"]
    assert parse_search(search_response([item])) == []
    item = video_renderer("aqz-KE-bpKQ", "x")
    del item["videoRenderer"]["title"]
    assert parse_search(search_response([item])) == []

    # videoId not matching the 11-char id shape (path traversal into the
    # thumb cache filename) -> item skipped.
    assert parse_search(search_response([video_renderer("../../../etc", "evil")])) == []
    assert parse_search(search_response([video_renderer(42, "evil")])) == []

    # Garbage roots and wrong types degrade to empty, never raise.
    assert parse_search({}) == []
    assert parse_search(None) == []
    assert parse_search({"contents": "nope"}) == []
    assert parse_search(search_response("nope")) == []
    assert parse_search(search_response([{"videoRenderer": "nope"}])) == []

    # Channel id is extracted when the owner carries a browseEndpoint.
    item = video_renderer("dQw4w9WgXcQ", "One")
    item["videoRenderer"]["ownerText"]["runs"][0]["navigationEndpoint"] = {
        "browseEndpoint": {"browseId": "UCabc123"}
    }
    assert parse_search(search_response([item]))[0].channel_id == "UCabc123"
    print("parser: ok")


def test_next_parser():
    def compact(vid, title, channel_id=""):
        r = {
            "compactVideoRenderer": {
                "videoId": vid,
                "title": {"simpleText": title},
                "shortBylineText": {"runs": [{"text": "chan"}]},
                "lengthText": {"simpleText": "3:21"},
                "thumbnail": {"thumbnails": [{"url": "https://t/r.jpg"}]},
            }
        }
        if channel_id:
            r["compactVideoRenderer"]["shortBylineText"]["runs"][0][
                "navigationEndpoint"] = {"browseEndpoint": {"browseId": channel_id}}
        return r

    data = {
        "contents": {"anything": [
            {"videoOwnerRenderer": {
                "title": {"runs": [{"text": "Current Channel"}]},
                "navigationEndpoint": {"browseEndpoint": {"browseId": "UCowner1"}},
            }},
            compact("dQw4w9WgXcQ", "Rel One", "UCrel1"),
            compact("aqz-KE-bpKQ", "Rel Two"),
        ]}
    }
    owner_id, owner_name, related = parse_next(data)
    assert (owner_id, owner_name) == ("UCowner1", "Current Channel")
    assert related == [
        Video("dQw4w9WgXcQ", "Rel One", "chan", "3:21", "https://t/r.jpg", "UCrel1"),
        Video("aqz-KE-bpKQ", "Rel Two", "chan", "3:21", "https://t/r.jpg"),
    ]

    # ANDROID-style videoWithContextRenderer is accepted too; the current
    # video (same id as requested) can be filtered by the caller.
    data2 = {"x": [
        {"videoWithContextRenderer": {
            "videoId": "aqz-KE-bpKQ",
            "headline": {"runs": [{"text": "V"}]},
        }},
    ]}
    owner_id, owner_name, related = parse_next(data2)
    assert (owner_id, owner_name) == ("", "")
    assert [v.video_id for v in related] == ["aqz-KE-bpKQ"]

    # Modern WEB shape: lockupViewModel (title/channel under viewmodels,
    # duration as a thumbnail badge). Non-video lockups are skipped.
    def lockup(vid, title, ctype="LOCKUP_CONTENT_TYPE_VIDEO"):
        return {"lockupViewModel": {
            "contentId": vid,
            "contentType": ctype,
            "metadata": {"lockupMetadataViewModel": {
                "title": {"content": title},
                "metadata": {"contentMetadataViewModel": {"metadataRows": [
                    {"metadataParts": [{"text": {"content": "LockChan"}}]}
                ]}},
            }},
            "contentImage": {"thumbnailViewModel": {
                "image": {"sources": [{"url": "https://t/l.jpg"}]},
                "overlays": [{"thumbnailOverlayBadgeViewModel": {
                    "thumbnailBadges": [{"thumbnailBadgeViewModel": {
                        "text": "31:25"}}]}}],
            }},
        }}

    data3 = {"contents": [
        lockup("dQw4w9WgXcQ", "Lock One"),
        lockup("aqz-KE-bpKQ", "A Playlist", ctype="LOCKUP_CONTENT_TYPE_PLAYLIST"),
    ]}
    _, _, related = parse_next(data3)
    assert related == [
        Video("dQw4w9WgXcQ", "Lock One", "LockChan", "31:25", "https://t/l.jpg"),
    ]

    # Owner id can live on the title runs instead of a top-level endpoint.
    data4 = {"x": {"videoOwnerRenderer": {
        "title": {"runs": [{"text": "RunsOwner", "navigationEndpoint": {
            "browseEndpoint": {"browseId": "UCruns"}}}]},
    }}}
    owner_id, owner_name, _ = parse_next(data4)
    assert (owner_id, owner_name) == ("UCruns", "RunsOwner")

    # Garbage degrades.
    assert parse_next({}) == ("", "", [])
    assert parse_next(None) == ("", "", [])
    print("next parser: ok")


def test_playlist_parser():
    def pvr(vid, title):
        return {
            "playlistVideoRenderer": {
                "videoId": vid,
                "title": {"runs": [{"text": title}]},
                "shortBylineText": {"runs": [{"text": "chan"}]},
                "lengthText": {"simpleText": "2:34"},
                "thumbnail": {"thumbnails": [{"url": "https://t/p.jpg"}]},
            }
        }

    # Renderers are found at any nesting depth, in document order — the
    # container shape shifts between clients and updates.
    data = {
        "contents": {"weird": {"nesting": [
            {"stuff": pvr("aaaaaaaaaaa", "First")},
            {"deeper": [{"x": pvr("bbbbbbbbbbb", "Second")}]},
        ]}}
    }
    assert parse_playlist(data) == [
        Video("aaaaaaaaaaa", "First", "chan", "2:34", "https://t/p.jpg"),
        Video("bbbbbbbbbbb", "Second", "chan", "2:34", "https://t/p.jpg"),
    ]

    # ANDROID may serve videoWithContextRenderer instead; both are accepted.
    mixed = {"a": [pvr("aaaaaaaaaaa", "P"), {
        "videoWithContextRenderer": {
            "videoId": "bbbbbbbbbbb",
            "headline": {"runs": [{"text": "V"}]},
        }
    }]}
    assert [v.title for v in parse_playlist(mixed)] == ["P", "V"]

    # Malformed entries are skipped; garbage degrades.
    bad = {"a": [{"playlistVideoRenderer": {"videoId": "../evil",
                                            "title": {"runs": [{"text": "x"}]}}},
                 {"playlistVideoRenderer": "nope"},
                 pvr("ccccccccccc", "OK")]}
    assert [v.video_id for v in parse_playlist(bad)] == ["ccccccccccc"]
    assert parse_playlist({}) == []
    assert parse_playlist(None) == []
    print("playlist parser: ok")


def test_thumb_cache():
    with tempfile.TemporaryDirectory() as tmp:
        cache = ThumbCache(Path(tmp), max_files=3)

        assert cache.get("aaaaaaaaaaa") is None

        p1 = cache.put("aaaaaaaaaaa", b"1")
        p2 = cache.put("bbbbbbbbbbb", b"2")
        p3 = cache.put("ccccccccccc", b"3")
        assert p1.read_bytes() == b"1"
        assert cache.get("aaaaaaaaaaa") == p1

        # Make recency deterministic: a oldest, then b, then c.
        for i, p in enumerate([p1, p2, p3]):
            os.utime(p, (i, i))

        # Fourth insert evicts the least-recently-used (a).
        cache.put("ddddddddddd", b"4")
        assert cache.get("aaaaaaaaaaa") is None
        assert cache.get("bbbbbbbbbbb") is not None

        # get() refreshes recency: touch b, insert -> c evicted, b survives.
        os.utime(cache.get("bbbbbbbbbbb"), (100, 100))
        cache.put("eeeeeeeeeee", b"5")
        assert cache.get("ccccccccccc") is None
        assert cache.get("bbbbbbbbbbb") is not None
    print("thumb cache: ok")


def test_feed_store():
    with tempfile.TemporaryDirectory() as tmp:
        store = FeedStore(Path(tmp) / "mono.db")
        assert store.load() == []
        videos = [
            Video("dQw4w9WgXcQ", "One", "c1", "1:23", "https://t/1.jpg"),
            Video("aqz-KE-bpKQ", "Two", "c2", "", "https://t/2.jpg"),
        ]
        store.save(videos)
        assert store.load() == videos
        # save() replaces, order preserved.
        store.save(list(reversed(videos)))
        assert store.load() == list(reversed(videos))
    print("feed store: ok")


if __name__ == "__main__":
    test_parser()
    test_next_parser()
    test_playlist_parser()
    test_thumb_cache()
    test_feed_store()
    print("all checks passed")
