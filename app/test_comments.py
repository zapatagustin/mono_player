"""Checks for the comments parsers (modern commentEntityPayload shape, the
only one YouTube serves as of 2026) and the on-demand comments model."""

import asyncio

from innertube import Comment, parse_comments, parse_comments_token
from comments import CommentsModel


def payload(cid, author, text, likes="", replies="", published="1 day ago",
            reply_level=0):
    return {"payload": {"commentEntityPayload": {
        "key": cid,
        "properties": {
            "commentId": cid,
            "content": {"content": text},
            "publishedTime": published,
            "replyLevel": reply_level,
        },
        "author": {"channelId": "UCx", "displayName": author},
        "toolbar": {"likeCountNotliked": likes, "replyCount": replies},
    }}}


def test_parse_comments():
    data = {"frameworkUpdates": {"entityBatchUpdate": {"mutations": [
        payload("c1", "@alice", "First comment", likes="5K", replies="29"),
        payload("c2", "@bob", "A reply", reply_level=1),  # skipped
        payload("c3", "@eve", "Second", published="2 weeks ago"),
        {"payload": {"somethingElse": {}}},
    ]}}}
    assert parse_comments(data) == [
        Comment("@alice", "First comment", "5K", "1 day ago", "29"),
        Comment("@eve", "Second", "", "2 weeks ago", ""),
    ]
    assert parse_comments({}) == []
    assert parse_comments(None) == []
    print("comments parser: ok")


def test_comments_token():
    data = {"contents": [{"itemSectionRenderer": {
        "sectionIdentifier": "comment-item-section",
        "contents": [{"continuationItemRenderer": {"continuationEndpoint": {
            "continuationCommand": {"token": "COMMENT_TOKEN"}}}}],
    }}, {"itemSectionRenderer": {
        "sectionIdentifier": "related-items",
        "contents": [{"continuationItemRenderer": {"continuationEndpoint": {
            "continuationCommand": {"token": "OTHER_TOKEN"}}}}],
    }}]}
    assert parse_comments_token(data) == "COMMENT_TOKEN"
    assert parse_comments_token({}) == ""
    assert parse_comments_token(None) == ""
    print("comments token: ok")


def test_comments_model():
    calls = []

    async def fetch(client, video_id):
        calls.append(video_id)
        return [Comment("@a", "hi", "1", "now", "0")]

    m = CommentsModel(client=None, fetch_fn=fetch, cache_size=2)
    m.setCurrent("aaaaaaaaaaa")
    assert calls == []  # setCurrent never fetches — load is on demand

    asyncio.run(m._load())
    assert calls == ["aaaaaaaaaaa"]
    assert m.items[0]["author"] == "@a"

    # Cached: reopening the panel does not refetch.
    asyncio.run(m._load())
    assert calls == ["aaaaaaaaaaa"]

    # Switching video clears stale items; next load fetches the new one.
    m.setCurrent("bbbbbbbbbbb")
    assert m.items == []
    asyncio.run(m._load())
    assert calls == ["aaaaaaaaaaa", "bbbbbbbbbbb"]

    # Failure degrades to empty.
    async def boom(client, video_id):
        raise RuntimeError("net")

    m2 = CommentsModel(client=None, fetch_fn=boom)
    m2.setCurrent("ccccccccccc")
    asyncio.run(m2._load())
    assert m2.items == [] and not m2.loading
    print("comments model: ok")


if __name__ == "__main__":
    test_parse_comments()
    test_comments_token()
    test_comments_model()
    print("all checks passed")
