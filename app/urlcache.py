"""In-memory cache of resolved stream URLs (what ytdl_hook put in mpv's
stream-open-filename), so re-materializing a tab skips the 2-4s yt-dlp
extraction. Entries die at the googlevideo expire= timestamp."""

import re

MARGIN_SECS = 600
FALLBACK_TTL_SECS = 1800

_EXPIRE = re.compile(r"[?&/]expire[=/](\d{10})")


class StreamUrlCache:
    def __init__(self):
        self._entries: dict[str, tuple[str, float]] = {}

    def get(self, video_id: str, now: float) -> str | None:
        entry = self._entries.get(video_id)
        if entry is None:
            return None
        resolved, expires = entry
        if now > expires - MARGIN_SECS:
            del self._entries[video_id]
            return None
        return resolved

    def put(self, video_id: str, resolved: str, now: float) -> None:
        # Only cache RESOLVED streams. An original page URL here would make
        # the next load skip ytdl and hand mpv a web page.
        if "youtube.com/watch" in resolved:
            return
        stamps = [int(m) for m in _EXPIRE.findall(resolved)]
        expires = min(stamps) if stamps else now + FALLBACK_TTL_SECS + MARGIN_SECS
        self._entries[video_id] = (resolved, expires)

    def invalidate(self, video_id: str) -> None:
        self._entries.pop(video_id, None)
