"""Checks for the InnerTube search/next/playlist parsers (trust boundary:
malformed data must degrade, never crash) and FeedModel's search pagination.
Thumbnail LRU eviction is checked in app/test_thumbs.py; feed cache
persistence is checked in app/test_feedstore.py."""

import asyncio

import innertube
from innertube import (
    PlaylistOption,
    Video,
    parse_next,
    parse_playlist,
    parse_playlist_options,
    parse_playlists_list,
    parse_search,
    parse_search_continuation,
    parse_search_token,
)
from feedmodel import FeedModel


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


def search_response_with_sections(sections):
    return {
        "contents": {
            "twoColumnSearchResultsRenderer": {
                "primaryContents": {
                    "sectionListRenderer": {"contents": sections}
                }
            }
        }
    }


def continuation_item(token):
    return {"continuationItemRenderer": {"continuationEndpoint": {
        "continuationCommand": {"token": token}}}}


def test_search_token_parser():
    # Continuation token lives in a continuationItemRenderer sibling
    # section, not nested inside the itemSectionRenderer sections.
    data = search_response_with_sections([
        {"itemSectionRenderer": {"contents": [video_renderer("dQw4w9WgXcQ", "One")]}},
        continuation_item("TOKEN123"),
    ])
    assert parse_search_token(data) == "TOKEN123"

    # Last page: no continuation section -> empty.
    data2 = search_response([video_renderer("dQw4w9WgXcQ", "One")])
    assert parse_search_token(data2) == ""

    # Garbage degrades.
    assert parse_search_token({}) == ""
    assert parse_search_token(None) == ""
    print("search token parser: ok")


def test_search_continuation_parser():
    # A continuation page: onResponseReceivedCommands, not the first page's
    # twoColumnSearchResultsRenderer shape.
    data = {
        "onResponseReceivedCommands": [
            {"appendContinuationItemsAction": {"continuationItems": [
                {"itemSectionRenderer": {"contents": [
                    video_renderer("dQw4w9WgXcQ", "One"),
                    video_renderer("aqz-KE-bpKQ", "Two"),
                ]}},
                continuation_item("TOKEN456"),
            ]}}
        ]
    }
    videos, token = parse_search_continuation(data)
    assert [v.video_id for v in videos] == ["dQw4w9WgXcQ", "aqz-KE-bpKQ"]
    assert token == "TOKEN456"

    # Last page: no continuationItemRenderer -> empty token, not fatal.
    data2 = {
        "onResponseReceivedCommands": [
            {"appendContinuationItemsAction": {"continuationItems": [
                {"itemSectionRenderer": {
                    "contents": [video_renderer("aqz-KE-bpKQ", "Two")]}},
            ]}}
        ]
    }
    videos2, token2 = parse_search_continuation(data2)
    assert [v.video_id for v in videos2] == ["aqz-KE-bpKQ"]
    assert token2 == ""

    # Garbage degrades.
    assert parse_search_continuation({}) == ([], "")
    assert parse_search_continuation(None) == ([], "")
    print("search continuation parser: ok")


class FakeFeedStore:
    """No sqlite: just enough for FeedModel's save()/load() calls."""

    def __init__(self):
        self.saved = []

    def load(self):
        return []

    def save(self, videos):
        self.saved = list(videos)


def test_feedmodel_search_pagination():
    # Search only (GUIDELINE.org): FeedModel holds the continuation token
    # while browsing search results, appends+dedupes on the next page, and
    # drops the token once any other feed loads or the pages run out.
    real_search, real_continuation = innertube.search, innertube.search_continuation
    try:
        async def fake_search(client, query):
            return [Video("aaaaaaaaaaa", "One", "c", "1:00", "https://t/1.jpg")], "TOK1"

        async def fake_continuation(client, token):
            assert token == "TOK1"
            # InnerTube repeats items across pages -- "aaaaaaaaaaa" again.
            return [
                Video("aaaaaaaaaaa", "One", "c", "1:00", "https://t/1.jpg"),
                Video("bbbbbbbbbbb", "Two", "c", "2:00", "https://t/2.jpg"),
            ], ""

        innertube.search = fake_search
        innertube.search_continuation = fake_continuation

        store = FakeFeedStore()
        model = FeedModel(client=None, store=store, cache=None)
        asyncio.run(model._search("query"))
        assert [v.video_id for v in model._videos] == ["aaaaaaaaaaa"]
        assert model._search_token == "TOK1"

        asyncio.run(model._search_more())
        assert [v.video_id for v in model._videos] == \
            ["aaaaaaaaaaa", "bbbbbbbbbbb"]
        assert store.saved == model._videos
        # Pages exhausted -> token cleared, further calls are no-ops.
        assert model._search_token == ""
        model.loadMoreSearchResults()  # no token: does not touch asyncio

        # A non-search load (e.g. a channel open) invalidates pagination.
        model._set_videos([Video("ccccccccccc", "Three", "c", "", "")])
        assert model._search_token == ""
    finally:
        innertube.search = real_search
        innertube.search_continuation = real_continuation
    print("feedmodel search pagination: ok")


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
                    {"metadataParts": [{"text": {"content": "LockChan"}}]},
                    {"metadataParts": [
                        {"text": {"content": "1.2M views"}},
                        {"text": {"content": "3 days ago"}},
                    ]},
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
        Video("dQw4w9WgXcQ", "Lock One", "LockChan", "31:25", "https://t/l.jpg",
              "", "1.2M views · 3 days ago"),
    ]

    # compactVideoRenderer carries views/date as separate text fields.
    item = compact("dQw4w9WgXcQ", "C", "UCx")
    item["compactVideoRenderer"]["shortViewCountText"] = {"simpleText": "17K views"}
    item["compactVideoRenderer"]["publishedTimeText"] = {"simpleText": "1 year ago"}
    _, _, related = parse_next({"a": [item]})
    assert related[0].meta == "17K views · 1 year ago"

    # ANDROID home serves gridVideoRenderer: same fields as search's
    # videoRenderer plus view/published texts.
    grid_item = {"gridVideoRenderer": {
        "videoId": "dQw4w9WgXcQ",
        "title": {"runs": [{"text": "Home Pick"}]},
        "shortBylineText": {"runs": [{"text": "HomeChan", "navigationEndpoint": {
            "browseEndpoint": {"browseId": "UChome"}}}]},
        "lengthText": {"runs": [{"text": "10:00"}]},
        "shortViewCountText": {"runs": [{"text": "1M views"}]},
        "publishedTimeText": {"runs": [{"text": "2 days ago"}]},
        "thumbnail": {"thumbnails": [{"url": "https://t/g.jpg"}]},
    }}
    _, _, videos = parse_next({"contents": [grid_item]})
    assert videos == [
        Video("dQw4w9WgXcQ", "Home Pick", "HomeChan", "10:00", "https://t/g.jpg",
              "UChome", "1M views · 2 days ago"),
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


def test_playlists_list_parser():
    def grid_pl(pid, title, count):
        return {"gridPlaylistRenderer": {
            "playlistId": pid,
            "title": {"runs": [{"text": title}]},
            "videoCountText": {"runs": [{"text": count}]},
            "thumbnail": {"thumbnails": [{"url": "https://t/pl.jpg"}]},
        }}

    data = {"deep": [{"nesting": grid_pl("PLabc123", "My Mix", "42 videos")},
                     {"gridPlaylistRenderer": "nope"}]}
    pls = parse_playlists_list(data)
    assert pls == [
        Video("", "My Mix", "", "", "https://t/pl.jpg", "", "42 videos",
              "PLabc123"),
    ]
    # Authenticated ANDROID shape: compactPlaylistModel inside
    # elementRenderer, VL-prefixed browseId, count in videoCountA11y.
    android = {"elementRenderer": {"deep": {"compactPlaylistModel": {
        "compactPlaylistData": {
            "thumbnail": {
                "image": {"sources": [{"url": "https://t/wl.jpg"}]},
                "videoCountA11y": "446 videos",
            },
            "metadata": {"title": "Watch later"},
            "onTap": {"innertubeCommand": {
                "browseEndpoint": {"browseId": "VLWL"}}},
        }}}}}
    assert parse_playlists_list(android) == [
        Video("", "Watch later", "", "", "https://t/wl.jpg", "",
              "446 videos", "WL"),
    ]
    # Degrades on missing data / bare "VL".
    assert parse_playlists_list({"compactPlaylistModel": {}}) == []
    assert parse_playlists_list({"compactPlaylistModel": {
        "compactPlaylistData": {
            "metadata": {"title": "x"},
            "onTap": {"innertubeCommand": {
                "browseEndpoint": {"browseId": "VL"}}}}}}) == []

    assert parse_playlists_list({}) == []
    assert parse_playlists_list(None) == []
    print("playlists list parser: ok")


def test_playlist_options_parser():
    data = {"contents": [
        {"playlistAddToOptionRenderer": {
            "playlistId": "WL",
            "title": {"runs": [{"text": "Watch later"}]},
            "containsSelectedVideos": "ALL",
        }},
        {"playlistAddToOptionRenderer": {
            "playlistId": "PLx1",
            "title": {"runs": [{"text": "Mix"}]},
            "containsSelectedVideos": "NONE",
        }},
        {"playlistAddToOptionRenderer": "garbage"},
    ]}
    assert parse_playlist_options(data) == [
        PlaylistOption("WL", "Watch later", True),
        PlaylistOption("PLx1", "Mix", False),
    ]
    assert parse_playlist_options({}) == []
    assert parse_playlist_options(None) == []
    print("playlist options parser: ok")


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


if __name__ == "__main__":
    test_parser()
    test_search_token_parser()
    test_search_continuation_parser()
    test_feedmodel_search_pagination()
    test_next_parser()
    test_playlists_list_parser()
    test_playlist_options_parser()
    test_playlist_parser()
    print("all checks passed")
