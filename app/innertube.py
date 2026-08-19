"""Anonymous InnerTube client. Parsing is a trust boundary: responses are
undocumented and shape-shift, so malformed data degrades, never raises."""

import re
from dataclasses import dataclass

SEARCH_URL = "https://www.youtube.com/youtubei/v1/search"

# Stale clientVersions get 400 FAILED_PRECONDITION — bump here when that
# appears (GUIDELINE.org).
WEB_CLIENT_VERSION = "2.20260101.00.00"
ANDROID_CLIENT_VERSION = "20.10.38"

CLIENT_CONTEXT = {
    "client": {
        "clientName": "WEB",
        "clientVersion": WEB_CLIENT_VERSION,
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
    playlist_id: str = ""  # set (with empty video_id) for playlist entries


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
ANDROID_CONTEXT = {
    "client": {
        "clientName": "ANDROID",
        "clientVersion": ANDROID_CLIENT_VERSION,
        "androidSdkVersion": 34,
        "hl": "en",
        "gl": "US",
    }
}
ANDROID_UA = f"com.google.android.youtube/{ANDROID_CLIENT_VERSION} (Linux; U; Android 14) gzip"


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
    headers = {
        "authorization": f"Bearer {bearer}",
        "user-agent": ANDROID_UA,
        "content-type": "application/json",
    }
    return headers


async def watch_later(client, bearer: str) -> list[Video]:
    """The account's real WL playlist (browseId VL + playlist id)."""
    resp = await client.post(
        BROWSE_URL,
        json={"context": _account_context(), "browseId": "VLWL"},
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    return parse_playlist(resp.json())


async def add_to_watch_later(client, bearer: str, video_id: str) -> bool:
    return await add_to_playlist(client, bearer, video_id, "WL")


NEXT_URL = "https://www.youtube.com/youtubei/v1/next"

# `next` related videos only exist in parseable form on the WEB client
# (ANDROID serves them as opaque elementRenderer protobufs).
WEB_CONTEXT = {
    "client": {
        "clientName": "WEB",
        "clientVersion": WEB_CLIENT_VERSION,
        "hl": "en",
        "gl": "US",
    }
}

_NEXT_RENDERERS = frozenset({
    "compactVideoRenderer", "videoWithContextRenderer", "videoRenderer",
    "gridVideoRenderer", "lockupViewModel", "videoOwnerRenderer",
    "slimOwnerRenderer",
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
        if name in ("compactVideoRenderer", "gridVideoRenderer"):
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
    comment_id: str = ""
    reply_token: str = ""  # continuation for this comment's replies
    avatar_url: str = ""
    like_action: str = ""  # perform_comment_action params (auth fetches only)
    unlike_action: str = ""
    liked: bool = False


_COMMENT_ID_IN_KEY = re.compile(rb"(Ug[0-9A-Za-z_-]{10,})/")


def _key_comment_id(key) -> str:
    """Entity keys embed the comment id (base64, '<commentId>/...')."""
    import base64
    from urllib.parse import unquote
    if not isinstance(key, str):
        return ""
    try:
        decoded = base64.b64decode(unquote(key) + "===")
    except Exception:
        return ""
    match = _COMMENT_ID_IN_KEY.search(decoded)
    return match.group(1).decode() if match else ""


def _toolbar_like_actions(data) -> dict:
    """commentId -> (like, unlike) action params, from the engagement
    toolbar surface payloads. Auth fetches only — anonymous toolbars carry
    a sign-in modal instead."""
    actions = {}
    for _, tb in _find_renderers(
            data, frozenset({"engagementToolbarSurfaceEntityPayload"})):
        cid = _key_comment_id(tb.get("key"))
        if not cid:
            continue
        like = _walk(tb, "likeCommand", "innertubeCommand",
                     "performCommentActionEndpoint", "action")
        unlike = _walk(tb, "unlikeCommand", "innertubeCommand",
                       "performCommentActionEndpoint", "action")
        if isinstance(like, str) and like:
            actions[cid] = (like, unlike if isinstance(unlike, str) else "")
    return actions


def _toolbar_like_states(data) -> dict:
    """commentId -> liked bool, from the toolbar state payloads."""
    states = {}
    for _, st in _find_renderers(
            data, frozenset({"engagementToolbarStateEntityPayload"})):
        cid = _key_comment_id(st.get("key"))
        if cid:
            states[cid] = st.get("likeState") == "TOOLBAR_LIKE_STATE_LIKED"
    return states


def _parse_comment_payload(payload) -> Comment | None:
    props = payload.get("properties")
    if not isinstance(props, dict):
        return None
    cid = props.get("commentId")
    text = _walk(props, "content", "content")
    author = _walk(payload, "author", "displayName")
    if not isinstance(text, str) or not isinstance(author, str):
        return None
    likes = _walk(payload, "toolbar", "likeCountNotliked")
    replies = _walk(payload, "toolbar", "replyCount")
    published = props.get("publishedTime")
    avatar = _walk(payload, "author", "avatarThumbnailUrl")
    return Comment(
        author,
        text,
        likes if isinstance(likes, str) else "",
        published if isinstance(published, str) else "",
        replies if isinstance(replies, str) else "",
        cid if isinstance(cid, str) else "",
        "",
        avatar if isinstance(avatar, str) else "",
    )


def _first_token(node) -> str:
    for _, cont in _find_renderers(node, frozenset({"continuationCommand"})):
        token = cont.get("token")
        if isinstance(token, str) and token:
            return token
    return ""


def parse_comments(data) -> tuple[list[Comment], str]:
    """(comments, next_page_token). Threads carry ordering and per-comment
    reply tokens; a replies page has no threads, so payloads fall back to
    document order. Data lives in commentEntityPayload mutations — the only
    shape YouTube serves now."""
    by_id: dict[str, Comment] = {}
    in_order: list[Comment] = []
    for _, payload in _find_renderers(data, frozenset({"commentEntityPayload"})):
        comment = _parse_comment_payload(payload)
        if comment is not None:
            in_order.append(comment)
            if comment.comment_id:
                by_id[comment.comment_id] = comment

    like_actions = _toolbar_like_actions(data)
    like_states = _toolbar_like_states(data)

    def _with_extras(base: Comment, reply_token: str) -> Comment:
        like, unlike = like_actions.get(base.comment_id, ("", ""))
        return Comment(base.author, base.text, base.likes, base.published,
                       base.replies, base.comment_id, reply_token,
                       base.avatar_url, like, unlike,
                       like_states.get(base.comment_id, False))

    comments = []
    for _, thread in _find_renderers(data, frozenset({"commentThreadRenderer"})):
        cid = _walk(thread, "commentViewModel", "commentViewModel", "commentId")
        base = by_id.get(cid) if isinstance(cid, str) else None
        if base is None:
            continue
        comments.append(_with_extras(base, _first_token(thread.get("replies"))))
    if not comments:
        comments = [_with_extras(c, c.reply_token) for c in in_order]

    next_token = ""
    endpoints = data.get("onResponseReceivedEndpoints") \
        if isinstance(data, dict) else None
    for endpoint in endpoints if isinstance(endpoints, list) else []:
        if not isinstance(endpoint, dict):
            continue
        action = endpoint.get("reloadContinuationItemsCommand") \
            or endpoint.get("appendContinuationItemsAction") or {}
        items = action.get("continuationItems") \
            if isinstance(action, dict) else None
        for item in items if isinstance(items, list) else []:
            cont = item.get("continuationItemRenderer") \
                if isinstance(item, dict) else None
            if isinstance(cont, dict):
                next_token = _first_token(cont) or next_token
    if not next_token:
        # ANDROID pages carry it as legacy nextContinuationData instead.
        for _, cont in _find_renderers(
                data, frozenset({"nextContinuationData"})):
            token = cont.get("continuation")
            if isinstance(token, str) and token:
                next_token = token
                break
    return comments, next_token


def parse_comments_token(data) -> str:
    """Continuation token of the comment-item-section in a `next` response."""
    if isinstance(data, dict):
        if data.get("sectionIdentifier") == "comment-item-section":
            token = _first_token(data)
            if token:
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


def parse_android_comments_token(data) -> str:
    """The comments continuation in an ANDROID `next`: it lives inside the
    engagement panel whose panelIdentifier is comment-item-section."""
    for _, panel in _find_renderers(
            data, frozenset({"engagementPanelSectionListRenderer"})):
        if panel.get("panelIdentifier") != "comment-item-section":
            continue
        for _, cont in _find_renderers(
                panel, frozenset({"reloadContinuationData",
                                  "nextContinuationData"})):
            token = cont.get("continuation")
            if isinstance(token, str) and token:
                return token
    return ""


def _comments_request(token_or_video: dict, bearer: str | None) -> tuple:
    """(context, headers) for a comments call: authenticated ANDROID when a
    bearer is available (toolbars carry real like actions), anonymous WEB
    otherwise."""
    if bearer:
        return _account_context(), _account_headers(bearer)
    return WEB_CONTEXT, {"content-type": "application/json"}


async def comments_page(client, token: str,
                        bearer: str | None = None) -> tuple[list[Comment], str]:
    """One `next {continuation}` call: a further comments page, or a
    comment's replies (same endpoint, thread-less response)."""
    context, headers = _comments_request({}, bearer)
    resp = await client.post(
        NEXT_URL,
        json={"context": context, "continuation": token},
        headers=headers,
    )
    resp.raise_for_status()
    return parse_comments(resp.json())


async def comments(client, video_id: str,
                   bearer: str | None = None) -> tuple[list[Comment], str]:
    """Top-level comments, first page: `next` for the section token, then
    the continuation for the payloads."""
    context, headers = _comments_request({}, bearer)
    resp = await client.post(
        NEXT_URL,
        json={"context": context, "videoId": video_id},
        headers=headers,
    )
    resp.raise_for_status()
    data = resp.json()
    token = (parse_android_comments_token(data) if bearer
             else parse_comments_token(data))
    if not token:
        return [], ""
    return await comments_page(client, token, bearer)


COMMENT_ACTION_URL = \
    "https://www.youtube.com/youtubei/v1/comment/perform_comment_action"


async def comment_action(client, bearer: str, action: str) -> bool:
    resp = await client.post(
        COMMENT_ACTION_URL,
        json={"context": _account_context(), "actions": [action]},
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    return True


_PLAYLIST_LIST_RENDERERS = frozenset({
    "gridPlaylistRenderer", "compactPlaylistRenderer", "playlistRenderer",
    "compactPlaylistModel",
})


def _parse_compact_playlist_model(pr) -> Video | None:
    """Authenticated ANDROID FEplaylist_aggregation shape: data under
    compactPlaylistData, id as a VL-prefixed browseId."""
    pd = pr.get("compactPlaylistData")
    if not isinstance(pd, dict):
        return None
    browse_id = _walk(pd, "onTap", "innertubeCommand",
                      "browseEndpoint", "browseId")
    title = _walk(pd, "metadata", "title")
    if (not isinstance(browse_id, str) or not browse_id.startswith("VL")
            or len(browse_id) <= 2 or not isinstance(title, str)):
        return None
    thumb = _walk(pd, "thumbnail", "image", "sources", -1, "url")
    count = _walk(pd, "thumbnail", "videoCountA11y")
    return Video(
        "",
        title,
        "",
        "",
        thumb if isinstance(thumb, str) else "",
        "",
        count if isinstance(count, str) else "",
        browse_id[2:],
    )


def parse_playlists_list(data) -> list[Video]:
    """The account's playlists as feed entries: empty video_id,
    playlist_id set, count in meta."""
    playlists = []
    for name, pr in _find_renderers(data, _PLAYLIST_LIST_RENDERERS):
        if name == "compactPlaylistModel":
            video = _parse_compact_playlist_model(pr)
            if video is not None:
                playlists.append(video)
            continue
        pid = pr.get("playlistId")
        title = _text(pr.get("title"))
        if not isinstance(pid, str) or not pid or title is None:
            continue
        thumb = _walk(pr, "thumbnail", "thumbnails", -1, "url")
        playlists.append(Video(
            "",
            title,
            "",
            "",
            thumb if isinstance(thumb, str) else "",
            "",
            _text(pr.get("videoCountText")) or "",
            pid,
        ))
    return playlists


async def account_feed(client, bearer: str, browse_id: str) -> list[Video]:
    """Authenticated ANDROID browse parsed by the generic video walker —
    home (FEwhat_to_watch) serves gridVideoRenderer, history (FEhistory)
    compactVideoRenderer."""
    resp = await client.post(
        BROWSE_URL,
        json={"context": _account_context(), "browseId": browse_id},
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    _, _, videos = parse_next(resp.json())
    return videos


async def my_playlists(client, bearer: str) -> list[Video]:
    resp = await client.post(
        BROWSE_URL,
        json={"context": _account_context(), "browseId": "FEplaylist_aggregation"},
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    return parse_playlists_list(resp.json())


async def playlist_videos(client, bearer: str, playlist_id: str) -> list[Video]:
    resp = await client.post(
        BROWSE_URL,
        json={"context": _account_context(), "browseId": "VL" + playlist_id},
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    return parse_playlist(resp.json())


@dataclass(frozen=True)
class Channel:
    name: str
    gaia_id: str          # effectiveObfuscatedGaiaId — the delegation id
    selected: bool
    delegated: bool       # False = base identity (no X-Goog-PageId header)


ACCOUNTS_LIST_URL = "https://www.youtube.com/youtubei/v1/account/accounts_list"

# Active channel delegation, module-level on purpose: every account
# endpoint flows through _account_context, and threading a page id through
# each call site buys nothing in a single-interpreter app. Delegation rides
# context.user.onBehalfOfUser (the X-Goog-PageId header gets 401 with a
# gpsoauth Bearer).
_page_id = ""


def set_page_id(page_id: str) -> None:
    global _page_id
    _page_id = page_id or ""


def _account_context() -> dict:
    if not _page_id:
        return ANDROID_CONTEXT
    return {**ANDROID_CONTEXT, "user": {"onBehalfOfUser": _page_id}}


def parse_accounts_list(data) -> list[Channel]:
    channels = []
    for _, item in _find_renderers(data, frozenset({"accountItem"})):
        name = _text(item.get("accountName"))
        identity = _walk(item, "serviceEndpoint", "signInEndpoint",
                         "directSigninIdentity")
        gaia = _walk(identity, "effectiveObfuscatedGaiaId")
        if name is None or not isinstance(gaia, str) or not gaia:
            continue
        delegation = _walk(identity, "gaiaDelegationType")
        channels.append(Channel(
            name,
            gaia,
            bool(item.get("isSelected")),
            delegation != "GAIA_DELEGATION_TYPE_NONE",
        ))
    return channels


async def list_channels(client, bearer: str) -> list[Channel]:
    resp = await client.post(
        ACCOUNTS_LIST_URL,
        json={"context": ANDROID_CONTEXT},
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    return parse_accounts_list(resp.json())


@dataclass(frozen=True)
class PlaylistOption:
    playlist_id: str
    title: str
    contains: bool  # video already in this playlist


ADD_TO_PLAYLIST_URL = \
    "https://www.youtube.com/youtubei/v1/playlist/get_add_to_playlist"
LIKE_URL = "https://www.youtube.com/youtubei/v1/like/like"


def parse_playlist_options(data) -> list[PlaylistOption]:
    options = []
    for _, opt in _find_renderers(
            data, frozenset({"playlistAddToOptionRenderer"})):
        pid = opt.get("playlistId")
        title = _text(opt.get("title"))
        if not isinstance(pid, str) or not pid or title is None:
            continue
        options.append(PlaylistOption(
            pid, title, opt.get("containsSelectedVideos") == "ALL"))
    return options


async def playlist_options(client, bearer: str,
                           video_id: str) -> list[PlaylistOption]:
    resp = await client.post(
        ADD_TO_PLAYLIST_URL,
        json={"context": _account_context(), "videoIds": [video_id]},
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    return parse_playlist_options(resp.json())


async def add_to_playlist(client, bearer: str, video_id: str,
                          playlist_id: str) -> bool:
    resp = await client.post(
        EDIT_PLAYLIST_URL,
        json={
            "context": _account_context(),
            "playlistId": playlist_id,
            "actions": [{"action": "ACTION_ADD_VIDEO",
                         "addedVideoId": video_id}],
        },
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    return resp.json().get("status") == "STATUS_SUCCEEDED"


async def remove_from_playlist(client, bearer: str, video_id: str,
                               playlist_id: str) -> bool:
    resp = await client.post(
        EDIT_PLAYLIST_URL,
        json={
            "context": _account_context(),
            "playlistId": playlist_id,
            "actions": [{"action": "ACTION_REMOVE_VIDEO_BY_VIDEO_ID",
                         "removedVideoId": video_id}],
        },
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    return resp.json().get("status") == "STATUS_SUCCEEDED"


async def like(client, bearer: str, video_id: str) -> bool:
    resp = await client.post(
        LIKE_URL,
        json={"context": _account_context(), "target": {"videoId": video_id}},
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    return True


CREATE_COMMENT_URL = \
    "https://www.youtube.com/youtubei/v1/comment/create_comment"


def parse_create_comment_params(data) -> str:
    """Per-video token authorizing comment creation; only present in
    AUTHENTICATED next responses (commentComposerControlsEntityPayload)."""
    for _, payload in _find_renderers(
            data, frozenset({"commentComposerControlsEntityPayload"})):
        params = payload.get("createCommentParams")
        if isinstance(params, str) and params:
            return params
    return ""


async def create_comment(client, bearer: str, video_id: str,
                         text: str) -> bool:
    """Post a top-level comment: authenticated next for the per-video
    createCommentParams, then create_comment. Acts as the selected channel."""
    resp = await client.post(
        NEXT_URL,
        json={"context": _account_context(), "videoId": video_id},
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    params = parse_create_comment_params(resp.json())
    if not params:
        return False
    resp = await client.post(
        CREATE_COMMENT_URL,
        json={"context": _account_context(),
              "createCommentParams": params,
              "commentText": text},
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    return True


SUBSCRIBE_URL = "https://www.youtube.com/youtubei/v1/subscription/subscribe"


async def subscribe(client, bearer: str, channel_id: str) -> bool:
    resp = await client.post(
        SUBSCRIBE_URL,
        json={"context": _account_context(), "channelIds": [channel_id]},
        headers=_account_headers(bearer),
    )
    resp.raise_for_status()
    return True


async def subscriptions(client, bearer: str) -> list[Video]:
    """Fetch the account's subscriptions feed. Requires a Bearer token."""
    resp = await client.post(
        BROWSE_URL,
        json={"context": _account_context(), "browseId": "FEsubscriptions"},
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
