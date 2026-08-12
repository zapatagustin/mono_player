"""Checks for the comments parsers (modern commentEntityPayload + thread
ordering + pagination token) and the on-demand comments model with
load-more and reply expansion."""

import asyncio

import base64
from urllib.parse import quote

from innertube import (
    Comment,
    parse_android_comments_token,
    parse_comments,
    parse_create_comment_params,
)
from comments import CommentsModel


def payload(cid, author, text, likes="", replies="", published="1 day ago"):
    return {"payload": {"commentEntityPayload": {
        "key": cid,
        "properties": {"commentId": cid, "content": {"content": text},
                       "publishedTime": published},
        "author": {"channelId": "UCx", "displayName": author,
                   "avatarThumbnailUrl": f"https://a/{author}.jpg"},
        "toolbar": {"likeCountNotliked": likes, "replyCount": replies},
    }}}


def toolbar(cid, action):
    key = quote(base64.b64encode(
        b"\x12\x1d" + cid.encode() + b"/12 F(").decode())
    return {"payload": {"engagementToolbarSurfaceEntityPayload": {
        "key": key,
        "likeCommand": {"innertubeCommand": {
            "performCommentActionEndpoint": {"action": action}}},
    }}}


def thread(cid, reply_token=""):
    t = {"commentThreadRenderer": {
        "commentViewModel": {"commentViewModel": {"commentId": cid}},
    }}
    if reply_token:
        t["commentThreadRenderer"]["replies"] = {"commentRepliesRenderer": {
            "contents": [{"continuationItemRenderer": {"continuationEndpoint": {
                "continuationCommand": {"token": reply_token}}}}]}}
    return t


def test_parse_comments():
    # Top-level page: threads give order + reply tokens, payloads give data,
    # the trailing continuationItemRenderer gives the next-page token.
    data = {
        "onResponseReceivedEndpoints": [{"reloadContinuationItemsCommand": {
            "continuationItems": [
                thread("UgxAliceAAAAAAAAAAAAAAA", reply_token="REPLIES_C1"),
                thread("UgxEveBBBBBBBBBBBBBBBBB"),
                {"continuationItemRenderer": {"continuationEndpoint": {
                    "continuationCommand": {"token": "PAGE2"}}}},
            ],
        }}],
        "frameworkUpdates": {"entityBatchUpdate": {"mutations": [
            payload("UgxAliceAAAAAAAAAAAAAAA", "@alice", "First", likes="5K", replies="29"),
            payload("UgxEveBBBBBBBBBBBBBBBBB", "@eve", "Second"),
            payload("orphan", "@x", "not referenced by any thread"),
        ]}},
    }
    data["frameworkUpdates"]["entityBatchUpdate"]["mutations"].append(
        toolbar("UgxAliceAAAAAAAAAAAAAAA", "LIKE_ACTION_C1"))
    comments, next_token = parse_comments(data)
    assert next_token == "PAGE2"
    assert comments == [
        Comment("@alice", "First", "5K", "1 day ago", "29",
                "UgxAliceAAAAAAAAAAAAAAA", "REPLIES_C1",
                "https://a/@alice.jpg", "LIKE_ACTION_C1"),
        Comment("@eve", "Second", "", "1 day ago", "",
                "UgxEveBBBBBBBBBBBBBBBBB", "",
                "https://a/@eve.jpg", ""),
    ]

    # Replies page: no threads — payloads in document order, no next token.
    data2 = {"frameworkUpdates": {"entityBatchUpdate": {"mutations": [
        payload("r1", "@bob", "A reply"),
    ]}}}
    comments, next_token = parse_comments(data2)
    assert [c.author for c in comments] == ["@bob"]
    assert next_token == ""

    assert parse_comments({}) == ([], "")
    assert parse_comments(None) == ([], "")
    print("comments parser: ok")


def test_create_comment_params():
    data = {"frameworkUpdates": {"entityBatchUpdate": {"mutations": [
        {"payload": {"somethingElse": {}}},
        {"payload": {"commentComposerControlsEntityPayload": {
            "createCommentParams": "CREATE_PARAMS_TOKEN"}}},
    ]}}}
    assert parse_create_comment_params(data) == "CREATE_PARAMS_TOKEN"
    assert parse_create_comment_params({}) == ""
    assert parse_create_comment_params(None) == ""
    print("create comment params: ok")


def test_android_comments_token():
    data = {"x": [{"engagementPanelSectionListRenderer": {
        "panelIdentifier": "comment-item-section",
        "content": {"sectionListRenderer": {"continuations": [
            {"reloadContinuationData": {"continuation": "ANDROID_TOK"}}]}},
    }}, {"engagementPanelSectionListRenderer": {
        "panelIdentifier": "engagement-panel-clip-create",
        "content": {"sectionListRenderer": {"continuations": [
            {"reloadContinuationData": {"continuation": "OTHER"}}]}},
    }}]}
    assert parse_android_comments_token(data) == "ANDROID_TOK"
    assert parse_android_comments_token({}) == ""
    assert parse_android_comments_token(None) == ""
    print("android comments token: ok")


def test_comments_model():
    calls = []

    async def fetch(client, video_id, bearer=None):
        calls.append(video_id)
        return ([Comment("@a", "hi", "1", "now", "2", "c1", "RTOK",
                         "https://a/a.jpg", "LIKEACT")], "PAGE2")

    async def fetch_page(client, token, bearer=None):
        calls.append(token)
        if token == "RTOK":
            return ([Comment("@r", "a reply", "", "now", "", "r1", "")], "")
        return ([Comment("@b", "more", "", "now", "", "c2", "")], "")

    m = CommentsModel(client=None, fetch_fn=fetch, page_fn=fetch_page,
                      cache_size=2)
    m.setCurrent("aaaaaaaaaaa")
    asyncio.run(m._load())
    assert calls == ["aaaaaaaaaaa"]
    assert m.items[0]["author"] == "@a"
    assert m.items[0]["hasReplies"] and not m.items[0]["expanded"]
    assert m.hasMore

    # Pagination appends and updates the token.
    asyncio.run(m._load_more())
    assert calls == ["aaaaaaaaaaa", "PAGE2"]
    assert [i["author"] for i in m.items] == ["@a", "@b"]
    assert not m.hasMore  # second page returned no token

    # Reply expansion inserts depth-1 items right after the parent.
    asyncio.run(m._toggle(0))
    assert calls[-1] == "RTOK"
    assert [(i["author"], i["depth"]) for i in m.items] == [
        ("@a", 0), ("@r", 1), ("@b", 0)]
    assert m.items[0]["expanded"]

    # Collapse removes them; re-expand hits the reply cache, no refetch.
    asyncio.run(m._toggle(0))
    assert [i["author"] for i in m.items] == ["@a", "@b"]
    n = len(calls)
    asyncio.run(m._toggle(0))
    assert len(calls) == n
    assert [i["author"] for i in m.items] == ["@a", "@r", "@b"]

    # Avatar and like action ride the items.
    assert m.items[0]["avatar"] == "https://a/a.jpg"
    assert m.items[0]["likeAction"] == "LIKEACT"

    # Liking a comment posts its action; no action -> login-required toast.
    acted, msgs = [], []
    m.message.connect(msgs.append)

    async def act(client, bearer, action):
        acted.append(action)
        return True

    class FakeAuth:
        async def bearer(self):
            return "tok"

    m._auth = FakeAuth()
    m._action_fn = act
    asyncio.run(m._like(0))
    assert acted == ["LIKEACT"]
    assert msgs[-1] == "comment liked"
    asyncio.run(m._like(2))  # "@b" has no like action
    assert msgs[-1] == "like unavailable (login?)"

    # Switching video clears items and pagination state.
    m.setCurrent("bbbbbbbbbbb")
    assert m.items == [] and not m.hasMore
    print("comments model: ok")


if __name__ == "__main__":
    test_parse_comments()
    test_create_comment_params()
    test_android_comments_token()
    test_comments_model()
    print("all checks passed")
