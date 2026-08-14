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


def _key(cid):
    return quote(base64.b64encode(
        b"\x12\x1d" + cid.encode() + b"/12 F(").decode())


def toolbar(cid, action, unlike=""):
    return {"payload": {"engagementToolbarSurfaceEntityPayload": {
        "key": _key(cid),
        "likeCommand": {"innertubeCommand": {
            "performCommentActionEndpoint": {"action": action}}},
        "unlikeCommand": {"innertubeCommand": {
            "performCommentActionEndpoint": {"action": unlike}}},
    }}}


def toolbar_state(cid, liked):
    return {"payload": {"engagementToolbarStateEntityPayload": {
        "key": _key(cid),
        "likeState": "TOOLBAR_LIKE_STATE_LIKED" if liked
                     else "TOOLBAR_LIKE_STATE_INDIFFERENT",
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
    data["frameworkUpdates"]["entityBatchUpdate"]["mutations"] += [
        toolbar("UgxAliceAAAAAAAAAAAAAAA", "LIKE_ACTION_C1", "UNLIKE_C1"),
        toolbar_state("UgxAliceAAAAAAAAAAAAAAA", liked=True),
        toolbar_state("UgxEveBBBBBBBBBBBBBBBBB", liked=False),
    ]
    comments, next_token = parse_comments(data)
    assert next_token == "PAGE2"
    assert comments == [
        Comment("@alice", "First", "5K", "1 day ago", "29",
                "UgxAliceAAAAAAAAAAAAAAA", "REPLIES_C1",
                "https://a/@alice.jpg", "LIKE_ACTION_C1", "UNLIKE_C1", True),
        Comment("@eve", "Second", "", "1 day ago", "",
                "UgxEveBBBBBBBBBBBBBBBBB", "",
                "https://a/@eve.jpg", "", "", False),
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
        calls.append((video_id, bearer))
        if bearer:
            # authenticated ANDROID: likes/avatars but NO reply tokens
            return ([Comment("@a", "hi", "1", "now", "2", "c1", "",
                             "https://a/a.jpg", "LIKEACT", "UNLIKEACT")],
                    "PAGE2")
        # anonymous WEB: reply tokens present
        return ([Comment("@a", "hi", "1", "now", "2", "c1", "RTOK")], "")

    async def fetch_page(client, token, bearer=None):
        calls.append(token)
        if token == "RTOK":
            return ([Comment("@r", "a reply", "", "now", "", "r1", "")],
                    "RMORE")
        if token == "RMORE":
            return ([Comment("@r2", "later reply", "", "now", "", "r2", "")],
                    "")
        return ([Comment("@b", "more", "", "now", "", "c2", "")], "")

    class FakeAuth:
        async def bearer(self):
            return "tok"

    m = CommentsModel(client=None, auth=FakeAuth(), fetch_fn=fetch,
                      page_fn=fetch_page, cache_size=2)
    m.setCurrent("aaaaaaaaaaa")
    asyncio.run(m._load())
    assert calls == [("aaaaaaaaaaa", "tok")]
    assert m.items[0]["author"] == "@a"
    # hasReplies comes from the reply COUNT: the auth shape has no tokens.
    assert m.items[0]["hasReplies"] and not m.items[0]["expanded"]
    assert m.items[0]["liked"] is False
    assert m.hasMore

    # Pagination appends and updates the token.
    asyncio.run(m._load_more())
    assert calls[-1] == "PAGE2"
    assert [i["author"] for i in m.items] == ["@a", "@b"]
    assert not m.hasMore  # second page returned no token

    # Reply expansion with no token: the anonymous WEB listing is fetched
    # once to map commentId -> reply token, then the replies load. A further
    # replies page exists, so a "more replies" row trails the block.
    asyncio.run(m._toggle(0))
    assert ("aaaaaaaaaaa", None) in calls  # anonymous refetch happened
    assert calls[-1] == "RTOK"
    assert [(i["author"], i["depth"], i["isMore"]) for i in m.items] == [
        ("@a", 0, False), ("@r", 1, False), ("", 1, True), ("@b", 0, False)]
    assert m.items[0]["expanded"]

    # Enter on the more-row loads the next replies page inline and, with
    # the thread exhausted, removes the row.
    asyncio.run(m._toggle(2))
    assert calls[-1] == "RMORE"
    assert [(i["author"], i["depth"]) for i in m.items] == [
        ("@a", 0), ("@r", 1), ("@r2", 1), ("@b", 0)]

    # Collapse removes the whole block; re-expand replays the accumulated
    # cache (both pages, no more-row, no refetch).
    asyncio.run(m._toggle(0))
    assert [i["author"] for i in m.items] == ["@a", "@b"]
    n = len(calls)
    asyncio.run(m._toggle(0))
    assert len(calls) == n
    assert [i["author"] for i in m.items] == ["@a", "@r", "@r2", "@b"]

    # Avatar and like action ride the items.
    assert m.items[0]["avatar"] == "https://a/a.jpg"
    assert m.items[0]["likeAction"] == "LIKEACT"

    # Liking a comment posts its action; no action -> login-required toast.
    acted, msgs = [], []
    m.message.connect(msgs.append)

    async def act(client, bearer, action):
        acted.append(action)
        return True

    m._action_fn = act
    # Like toggles: first LIKEACT and liked=True, then UNLIKEACT and back.
    asyncio.run(m._like(0))
    assert acted == ["LIKEACT"]
    assert msgs[-1] == "comment liked"
    assert m.items[0]["liked"] is True
    asyncio.run(m._like(0))
    assert acted == ["LIKEACT", "UNLIKEACT"]
    assert msgs[-1] == "comment unliked"
    assert m.items[0]["liked"] is False
    idx = next(i for i, it in enumerate(m.items) if it["author"] == "@b")
    asyncio.run(m._like(idx))  # "@b" has no like action
    assert msgs[-1] == "like unavailable (login?)"

    # Switching video clears items and pagination state.
    m.setCurrent("bbbbbbbbbbb")
    assert m.items == [] and not m.hasMore
    print("comments model: ok")


def test_comments_qt_model():
    # The model must mutate granularly (insert/remove/dataChanged), never
    # reset wholesale — a reset makes the view drop its scroll position.
    from PySide6.QtCore import QAbstractListModel

    async def fetch(client, video_id, bearer=None):
        return ([Comment("@a", "hi", "1", "now", "2", "c1", "RTOK"),
                 Comment("@b", "yo", "", "now", "", "c2", "")], "PAGE2")

    async def fetch_page(client, token, bearer=None):
        if token == "RTOK":
            return ([Comment("@r", "reply", "", "now", "", "r1", "")], "")
        return ([Comment("@c", "more", "", "now", "", "c3", "")], "")

    m = CommentsModel(client=None, fetch_fn=fetch, page_fn=fetch_page)
    assert isinstance(m, QAbstractListModel)
    resets, inserts, removes = [], [], []
    m.modelReset.connect(lambda: resets.append(1))
    m.rowsInserted.connect(lambda p, a, b: inserts.append((a, b)))
    m.rowsRemoved.connect(lambda p, a, b: removes.append((a, b)))

    m.setCurrent("aaaaaaaaaaa")
    asyncio.run(m._load())
    assert m.rowCount() == 2
    initial_resets = len(resets)  # initial load may reset; that's fine

    # Page append: rows inserted at the end, NO reset.
    asyncio.run(m._load_more())
    assert m.rowCount() == 3
    assert inserts[-1] == (2, 2)
    assert len(resets) == initial_resets

    # Reply expansion inserts right after the parent, NO reset.
    asyncio.run(m._toggle(0))
    assert m.rowCount() == 4
    assert inserts[-1] == (1, 1)
    assert len(resets) == initial_resets

    # Collapse removes those rows, NO reset.
    asyncio.run(m._toggle(0))
    assert m.rowCount() == 3
    assert removes[-1] == (1, 1)
    assert len(resets) == initial_resets

    # Role data is reachable (author role resolves).
    roles = {bytes(v): k for k, v in m.roleNames().items()}
    idx = m.index(0)
    assert m.data(idx, roles[b"author"]) == "@a"
    print("comments qt model: ok")


if __name__ == "__main__":
    test_parse_comments()
    test_create_comment_params()
    test_android_comments_token()
    test_comments_model()
    test_comments_qt_model()
    print("all checks passed")
