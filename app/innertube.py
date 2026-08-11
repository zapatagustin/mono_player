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
