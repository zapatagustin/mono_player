"""Netscape cookies.txt export of the login webview's google session, so
yt-dlp (inside mpv's ytdl_hook) can mark played videos watched on the
account. This file is the ONLY place those cookies land outside the
WebEngine profile store; it is 0600 and dies on logout."""

import os
from pathlib import Path

# Everything yt-dlp needs to authenticate the videostats calls, nothing
# else google sets in the login flow.
EXPORT_DOMAINS = ("youtube.com", "google.com")

HEADER = "# Netscape HTTP Cookie File\n"


def exportable(domain: str) -> bool:
    host = domain.lstrip(".")
    return any(host == d or host.endswith("." + d) for d in EXPORT_DOMAINS)


def netscape(records) -> str:
    """Serialize (domain, path, secure, expiry, name, value) records.
    A leading-dot domain is the includeSubdomains flag."""
    lines = [HEADER]
    for domain, path, secure, expiry, name, value in records:
        lines.append("\t".join((
            domain,
            "TRUE" if domain.startswith(".") else "FALSE",
            path,
            "TRUE" if secure else "FALSE",
            str(int(expiry)),
            name,
            value,
        )) + "\n")
    return "".join(lines)


def record(cookie):
    """QNetworkCookie -> record tuple; None when out of scope."""
    domain = cookie.domain()
    if not exportable(domain):
        return None
    expiry = 0 if cookie.isSessionCookie() \
        else int(cookie.expirationDate().toSecsSinceEpoch())
    return (domain, cookie.path(), cookie.isSecure(), expiry,
            bytes(cookie.name()).decode(errors="replace"),
            bytes(cookie.value()).decode(errors="replace"))


class CookieJar:
    """Mirrors a webview cookie store into cookies.txt. Keyed by the cookie
    identity (domain, path, name) so re-sets overwrite instead of pile up."""

    def __init__(self, path):
        self.path = Path(path)
        self._cookies: dict[tuple[str, str, str], tuple] = {}
        self._store = None
        # Fired after the file is (re)written or removed, so callers can
        # track its existence (the QML cookieFile flag).
        self.on_change = None

    def attach(self, store):
        """loadAllCookies replays whatever the persistent profile already
        holds, so the export is complete without a fresh login."""
        self._store = store
        store.cookieAdded.connect(self.onCookieAdded)
        store.cookieRemoved.connect(self.onCookieRemoved)
        store.loadAllCookies()

    def onCookieAdded(self, cookie):
        rec = record(cookie)
        if rec is None:
            return
        self._cookies[(rec[0], rec[1], rec[4])] = rec
        self.write()

    def onCookieRemoved(self, cookie):
        rec = record(cookie)
        if rec is None:
            return
        if self._cookies.pop((rec[0], rec[1], rec[4]), None) is not None:
            self.write()

    def write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(netscape(sorted(self._cookies.values())))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        if self.on_change is not None:
            self.on_change()

    def clear(self) -> None:
        """Logout: drop the export and the profile's stored session."""
        if self._store is not None:
            self._store.deleteAllCookies()
        self._cookies.clear()
        self.path.unlink(missing_ok=True)
        if self.on_change is not None:
            self.on_change()
