"""Anonymous InnerTube client. Parsing is a trust boundary: responses are
undocumented and shape-shift, so malformed data degrades, never raises."""

import re
from dataclasses import dataclass

SEARCH_URL = "https://www.youtube.com/youtubei/v1/search"
CLIENT_CONTEXT = {
    "client": {
        "clientName": "WEB",
        "clientVersion": "2.20260101.00.00",
        "hl": "en",
        "gl": "US",
    }
}

# YouTube video ids are exactly 11 chars of [A-Za-z0-9_-]. Anything else is
# rejected here because the id becomes a cache filename downstream.
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


@dataclass(frozen=True)
class Video:
    video_id: str
    title: str
    channel: str
    duration: str
    thumb_url: str
    channel_id: str = ""
    meta: str = ""  # "1.2M views · 3 days ago" — display-ready, localized


def _walk(node, *path):
    """Descend dicts by key and lists by index; None on any shape mismatch."""
    for step in path:
        if isinstance(step, int):
            if not isinstance(node, list) or not -len(node) <= step < len(node):
                return None
            node = node[step]
        else:
            if not isinstance(node, dict):
                return None
            node = node.get(step)
        if node is None:
            return None
    return node


def _parse_video(item) -> Video | None:
    vr = _walk(item, "videoRenderer")
    if not isinstance(vr, dict):
        return None
    vid = vr.get("videoId")
    title = _walk(vr, "title", "runs", 0, "text")
    if not isinstance(vid, str) or not _VIDEO_ID.fullmatch(vid):
        return None
    if not isinstance(title, str):
        return None
    channel = _walk(vr, "ownerText", "runs", 0, "text")
    duration = _walk(vr, "lengthText", "simpleText")
    thumb = _walk(vr, "thumbnail", "thumbnails", -1, "url")
    return Video(
        vid,
        title,
        channel if isinstance(channel, str) else "",
        duration if isinstance(duration, str) else "",
        thumb if isinstance(thumb, str) else "",
        _channel_id(vr.get("ownerText")),
    )


def parse_search(data) -> list[Video]:
    sections = _walk(
        data,
        "contents",
        "twoColumnSearchResultsRenderer",
        "primaryContents",
        "sectionListRenderer",
        "contents",
    )
    if not isinstance(sections, list):
        return []
    videos = []
    for section in sections:
        items = _walk(section, "itemSectionRenderer", "contents")
        if not isinstance(items, list):
            continue
        for item in items:
            video = _parse_video(item)
            if video is not None:
                videos.append(video)
    return videos


BROWSE_URL = "https://www.youtube.com/youtubei/v1/browse"

# Subscriptions ride the ANDROID client with a gpsoauth Bearer -- account
# data only, never stream URLs (GUIDELINE.org, Architecture).
# Stale clientVersions get 400 FAILED_PRECONDITION — bump when that appears.
ANDROID_CONTEXT = {
    "client": {
        "clientName": "ANDROID",
        "clientVersion": "20.10.38",
        "androidSdkVersion": 34,
        "hl": "en",
        "gl": "US",
    }
}
ANDROID_UA = "com.google.android.youtube/20.10.38 (Linux; U; Android 14) gzip"


def _text(node) -> str | None:
    """InnerTube text is either {"simpleText": s} or {"runs": [{"text": s}]}."""
    if not isinstance(node, dict):
        return None
    if isinstance(node.get("simpleText"), str):
        return node["simpleText"]
    text = _walk(node, "runs", 0, "text")
    return text if isinstance(text, str) else None


def _channel_id(text_node) -> str:
    """browseId living on a text's first run (owner/byline links)."""
    bid = _walk(text_node, "runs", 0, "navigationEndpoint",
                "browseEndpoint", "browseId")
    return bid if isinstance(bid, str) else ""


def _parse_video_with_context(item) -> Video | None:
    vr = _walk(item, "videoWithContextRenderer")
    if not isinstance(vr, dict):
        return None
    vid = vr.get("videoId")
    title = _text(vr.get("headline"))
    if not isinstance(vid, str) or not _VIDEO_ID.fullmatch(vid):
        return None
    if title is None:
        return None
    thumb = _walk(vr, "thumbnail", "thumbnails", -1, "url")
    return Video(
        vid,
        title,
        _text(vr.get("shortBylineText")) or "",
        _text(vr.get("lengthText")) or "",
        thumb if isinstance(thumb, str) else "",
        _channel_id(vr.get("shortBylineText")),
    )


def _find_renderers(node, names: frozenset):
    """Yield (name, renderer_dict) at any depth, in document order — the
    containers shift between clients and updates; the renderers don't."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in names and isinstance(value, dict):
                yield key, value
            else:
                yield from _find_renderers(value, names)
    elif isinstance(node, list):
        for item in node:
            yield from _find_renderers(item, names)


def _parse_playlist_video(vr) -> Video | None:
    vid = vr.get("videoId")
    title = _text(vr.get("title"))
    if not isinstance(vid, str) or not _VIDEO_ID.fullmatch(vid) or title is None:
        return None
    thumb = _walk(vr, "thumbnail", "thumbnails", -1, "url")
    return Video(
        vid,
        title,
        _text(vr.get("shortBylineText")) or "",
        _text(vr.get("lengthText")) or "",
        thumb if isinstance(thumb, str) else "",
        _channel_id(vr.get("shortBylineText")),
    )


_PLAYLIST_RENDERERS = frozenset({"playlistVideoRenderer", "videoWithContextRenderer"})


def parse_playlist(data) -> list[Video]:
    videos = []
    for name, vr in _find_renderers(data, _PLAYLIST_RENDERERS):
        if name == "playlistVideoRenderer":
            video = _parse_playlist_video(vr)
        else:
            video = _parse_video_with_context({"videoWithContextRenderer": vr})
        if video is not None:
            videos.append(video)
    return videos


EDIT_PLAYLIST_URL = "https://www.youtube.com/youtubei/v1/browse/edit_playlist"


def _account_headers(bearer: str) -> dict:
    return {
        "authorization": f"Bearer {bearer}",
        "user-agent": ANDROID_UA,
        "content-type": "application/json",
    }


async def watch_later(client, bearer: str) -> list[Video]:
    """The account's real WL playlist (browseId VL + playlist id)."""
    resp = await client.post(
        BROWSE_URL,
        json={"context": ANDROID_CONTEXT, "browseId": "VLWL"},
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    return parse_playlist(resp.json())


async def add_to_watch_later(client, bearer: str, video_id: str) -> bool:
    resp = await client.post(
        EDIT_PLAYLIST_URL,
        json={
            "context": ANDROID_CONTEXT,
            "playlistId": "WL",
            "actions": [{"action": "ACTION_ADD_VIDEO", "addedVideoId": video_id}],
        },
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    return resp.json().get("status") == "STATUS_SUCCEEDED"


NEXT_URL = "https://www.youtube.com/youtubei/v1/next"

# `next` related videos only exist in parseable form on the WEB client
# (ANDROID serves them as opaque elementRenderer protobufs).
WEB_CONTEXT = {
    "client": {
        "clientName": "WEB",
        "clientVersion": "2.20260101.00.00",
        "hl": "en",
        "gl": "US",
    }
}

_NEXT_RENDERERS = frozenset({
    "compactVideoRenderer", "videoWithContextRenderer", "videoRenderer",
    "lockupViewModel", "videoOwnerRenderer", "slimOwnerRenderer",
})

_TIME = re.compile(r"[\d:]+")


def _parse_lockup(vm) -> Video | None:
    """2024+ WEB view-model shape for list items."""
    if vm.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO":
        return None
    vid = vm.get("contentId")
    title = _walk(vm, "metadata", "lockupMetadataViewModel", "title", "content")
    if not isinstance(vid, str) or not _VIDEO_ID.fullmatch(vid) \
            or not isinstance(title, str):
        return None
    rows = _walk(vm, "metadata", "lockupMetadataViewModel", "metadata",
                 "contentMetadataViewModel", "metadataRows")
    rows = rows if isinstance(rows, list) else []
    channel = _walk(rows, 0, "metadataParts", 0, "text", "content")
    # Remaining rows carry views/upload date; join them display-ready.
    meta_parts = []
    for row in rows[1:]:
        parts = row.get("metadataParts") if isinstance(row, dict) else None
        for part in parts if isinstance(parts, list) else []:
            text = _walk(part, "text", "content")
            if isinstance(text, str) and text:
                meta_parts.append(text)
    duration = ""
    for _, badge in _find_renderers(vm.get("contentImage"),
                                    frozenset({"thumbnailBadgeViewModel"})):
        text = badge.get("text")
        if isinstance(text, str) and _TIME.fullmatch(text):
            duration = text
            break
    thumb = _walk(vm, "contentImage", "thumbnailViewModel", "image",
                  "sources", -1, "url")
    return Video(
        vid,
        title,
        channel if isinstance(channel, str) else "",
        duration,
        thumb if isinstance(thumb, str) else "",
        "",
        " · ".join(meta_parts),
    )


def _parse_compact_video(vr) -> Video | None:
    vid = vr.get("videoId")
    title = _text(vr.get("title"))
    if not isinstance(vid, str) or not _VIDEO_ID.fullmatch(vid) or title is None:
        return None
    thumb = _walk(vr, "thumbnail", "thumbnails", -1, "url")
    meta_parts = [t for t in (_text(vr.get("shortViewCountText")),
                              _text(vr.get("publishedTimeText"))) if t]
    return Video(
        vid,
        title,
        _text(vr.get("shortBylineText")) or "",
        _text(vr.get("lengthText")) or "",
        thumb if isinstance(thumb, str) else "",
        _channel_id(vr.get("shortBylineText")),
        " · ".join(meta_parts),
    )


def parse_next(data) -> tuple[str, str, list[Video]]:
    """(owner channel id, owner name, related videos) from a `next`
    response. Everything degrades to empty on shape mismatch."""
    owner_id, owner_name = "", ""
    related_videos = []
    for name, node in _find_renderers(data, _NEXT_RENDERERS):
        if name in ("videoOwnerRenderer", "slimOwnerRenderer"):
            if not owner_id:
                bid = _walk(node, "navigationEndpoint", "browseEndpoint",
                            "browseId")
                if not isinstance(bid, str) or not bid:
                    bid = _channel_id(node.get("title"))
                owner_id = bid or ""
                owner_name = _text(node.get("title")) or ""
            continue
        if name == "compactVideoRenderer":
            video = _parse_compact_video(node)
        elif name == "lockupViewModel":
            video = _parse_lockup(node)
        elif name == "videoRenderer":
            video = _parse_video({name: node})
        else:
            video = _parse_video_with_context({name: node})
        if video is not None:
            related_videos.append(video)
    return owner_id, owner_name, related_videos


async def related(client, video_id: str) -> tuple[str, str, list[Video]]:
    """Anonymous `next`: the current video's channel + related videos."""
    resp = await client.post(
        NEXT_URL,
        json={"context": WEB_CONTEXT, "videoId": video_id},
        headers={"content-type": "application/json"},
    )
    resp.raise_for_status()
    owner_id, owner_name, videos = parse_next(resp.json())
    return owner_id, owner_name, [v for v in videos if v.video_id != video_id]


# Stable protobuf param selecting a channel's Videos tab.
CHANNEL_VIDEOS_PARAMS = "EgZ2aWRlb3PyBgQKAjoA"


async def channel_videos(client, browse_id: str) -> list[Video]:
    """Anonymous channel browse (Videos tab; the defensive walker still
    finds videos if the params stop selecting the tab)."""
    resp = await client.post(
        BROWSE_URL,
        json={"context": WEB_CONTEXT, "browseId": browse_id,
              "params": CHANNEL_VIDEOS_PARAMS},
        headers={"content-type": "application/json"},
    )
    resp.raise_for_status()
    _, _, videos = parse_next(resp.json())
    return videos


def parse_subscriptions(data) -> list[Video]:
    tabs = _walk(data, "contents", "singleColumnBrowseResultsRenderer", "tabs")
    if not isinstance(tabs, list):
        return []
    videos = []
    for tab in tabs:
        sections = _walk(tab, "tabRenderer", "content", "sectionListRenderer",
                         "contents")
        if not isinstance(sections, list):
            continue
        for section in sections:
            items = _walk(section, "itemSectionRenderer", "contents")
            if not isinstance(items, list):
                continue
            for item in items:
                video = _parse_video_with_context(item)
                if video is not None:
                    videos.append(video)
    return videos


@dataclass(frozen=True)
class Comment:
    author: str
    text: str
    likes: str
    published: str
    replies: str


def parse_comments(data) -> list[Comment]:
    """Modern shape only: commentEntityPayload mutations (framework updates).
    Replies (replyLevel > 0) are skipped; malformed payloads too."""
    comments = []
    for _, payload in _find_renderers(data, frozenset({"commentEntityPayload"})):
        props = payload.get("properties")
        if not isinstance(props, dict):
            continue
        level = props.get("replyLevel")
        if isinstance(level, int) and level > 0:
            continue
        text = _walk(props, "content", "content")
        author = _walk(payload, "author", "displayName")
        if not isinstance(text, str) or not isinstance(author, str):
            continue
        likes = _walk(payload, "toolbar", "likeCountNotliked")
        replies = _walk(payload, "toolbar", "replyCount")
        published = props.get("publishedTime")
        comments.append(Comment(
            author,
            text,
            likes if isinstance(likes, str) else "",
            published if isinstance(published, str) else "",
            replies if isinstance(replies, str) else "",
        ))
    return comments


def parse_comments_token(data) -> str:
    """Continuation token of the comment-item-section in a `next` response."""
    if isinstance(data, dict):
        if data.get("sectionIdentifier") == "comment-item-section":
            for _, cont in _find_renderers(
                    data, frozenset({"continuationCommand"})):
                token = cont.get("token")
                if isinstance(token, str) and token:
                    return token
        for value in data.values():
            token = parse_comments_token(value)
            if token:
                return token
    elif isinstance(data, list):
        for item in data:
            token = parse_comments_token(item)
            if token:
                return token
    return ""


async def comments(client, video_id: str) -> list[Comment]:
    """Top-level comments, first page: `next` for the section token, then
    `next {continuation}` for the payloads."""
    headers = {"content-type": "application/json"}
    resp = await client.post(
        NEXT_URL,
        json={"context": WEB_CONTEXT, "videoId": video_id},
        headers=headers,
    )
    resp.raise_for_status()
    token = parse_comments_token(resp.json())
    if not token:
        return []
    resp = await client.post(
        NEXT_URL,
        json={"context": WEB_CONTEXT, "continuation": token},
        headers=headers,
    )
    resp.raise_for_status()
    return parse_comments(resp.json())


SUBSCRIBE_URL = "https://www.youtube.com/youtubei/v1/subscription/subscribe"


async def subscribe(client, bearer: str, channel_id: str) -> bool:
    resp = await client.post(
        SUBSCRIBE_URL,
        json={"context": ANDROID_CONTEXT, "channelIds": [channel_id]},
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    return True


async def subscriptions(client, bearer: str) -> list[Video]:
    """Fetch the account's subscriptions feed. Requires a Bearer token."""
    resp = await client.post(
        BROWSE_URL,
        json={"context": ANDROID_CONTEXT, "browseId": "FEsubscriptions"},
        headers={
            "authorization": f"Bearer {bearer}",
            "user-agent": ANDROID_UA,
            "content-type": "application/json",
        },
    )
    resp.raise_for_status()
    return parse_subscriptions(resp.json())


async def search(client, query: str) -> list[Video]:
    """POST an anonymous search. `client` is the app-wide httpx.AsyncClient."""
    resp = await client.post(
        SEARCH_URL,
        json={"context": CLIENT_CONTEXT, "query": query},
        headers={"content-type": "application/json"},
    )
    resp.raise_for_status()
    return parse_search(resp.json())
