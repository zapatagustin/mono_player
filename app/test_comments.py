"""Checks for the comments parsers (modern commentEntityPayload + thread
ordering + pagination token) and the on-demand comments model with
load-more and reply expansion."""

import asyncio

from innertube import Comment, parse_comments
from comments import CommentsModel


def payload(cid, author, text, likes="", replies="", published="1 day ago"):
    return {"payload": {"commentEntityPayload": {
        "key": cid,
        "properties": {"commentId": cid, "content": {"content": text},
                       "publishedTime": published},
        "author": {"channelId": "UCx", "displayName": author},
        "toolbar": {"likeCountNotliked": likes, "replyCount": replies},
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
                thread("c1", reply_token="REPLIES_C1"),
                thread("c2"),
                {"continuationItemRenderer": {"continuationEndpoint": {
                    "continuationCommand": {"token": "PAGE2"}}}},
            ],
        }}],
        "frameworkUpdates": {"entityBatchUpdate": {"mutations": [
            payload("c1", "@alice", "First", likes="5K", replies="29"),
            payload("c2", "@eve", "Second"),
            payload("orphan", "@x", "not referenced by any thread"),
        ]}},
    }
    comments, next_token = parse_comments(data)
    assert next_token == "PAGE2"
    assert comments == [
        Comment("@alice", "First", "5K", "1 day ago", "29", "c1", "REPLIES_C1"),
        Comment("@eve", "Second", "", "1 day ago", "", "c2", ""),
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


def test_comments_model():
    calls = []

    async def fetch(client, video_id):
        calls.append(video_id)
        return ([Comment("@a", "hi", "1", "now", "2", "c1", "RTOK")], "PAGE2")

    async def fetch_page(client, token):
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

    # Switching video clears items and pagination state.
    m.setCurrent("bbbbbbbbbbb")
    assert m.items == [] and not m.hasMore
    print("comments model: ok")


if __name__ == "__main__":
    test_parse_comments()
    test_comments_model()
    print("all checks passed")
