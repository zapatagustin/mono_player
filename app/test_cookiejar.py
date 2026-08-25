"""Checks for the cookies.txt export: Netscape field order and flags,
the youtube/google domain filter, and the on-disk file (0600, replaced
in place) that mpv's ytdl_hook reads."""

import stat
import tempfile
from pathlib import Path

from cookiejar import HEADER, CookieJar, exportable, netscape


class FakeStamp:
    def __init__(self, secs):
        self._secs = secs

    def toSecsSinceEpoch(self):
        return self._secs


class FakeCookie:
    """Stands in for QNetworkCookie: name/value are QByteArray-ish."""

    def __init__(self, domain, path, name, value, secure=False, expiry=0):
        self._domain, self._path = domain, path
        self._name, self._value = name, value
        self._secure, self._expiry = secure, expiry

    def domain(self):
        return self._domain

    def path(self):
        return self._path

    def name(self):
        return self._name.encode()

    def value(self):
        return self._value.encode()

    def isSecure(self):
        return self._secure

    def isSessionCookie(self):
        return self._expiry == 0

    def expirationDate(self):
        return FakeStamp(self._expiry)


def test_netscape_format():
    out = netscape([
        # Host-spanning, secure, with an expiry.
        (".youtube.com", "/", True, 1893456000, "SAPISID", "abc"),
        # Single host, insecure, session cookie.
        ("accounts.google.com", "/o", False, 0, "PREF", "x=1"),
    ])
    lines = out.splitlines()
    assert out.startswith(HEADER)
    assert lines[1] == ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSAPISID\tabc"
    assert lines[2] == "accounts.google.com\tFALSE\t/o\tFALSE\t0\tPREF\tx=1"
    assert len(lines) == 3
    print("netscape format: ok")


def test_domain_filter():
    assert exportable(".youtube.com")
    assert exportable("youtube.com")
    assert exportable("accounts.google.com")
    # Suffix match only on a label boundary.
    assert not exportable("evilyoutube.com")
    assert not exportable("google.com.attacker.net")
    assert not exportable("doubleclick.net")
    print("domain filter: ok")


def test_jar_writes_file():
    with tempfile.TemporaryDirectory() as tmp:
        jar = CookieJar(Path(tmp) / "sub" / "cookies.txt")
        jar.onCookieAdded(FakeCookie(".youtube.com", "/", "SID", "one",
                                     secure=True, expiry=1893456000))
        jar.onCookieAdded(FakeCookie(".doubleclick.net", "/", "id", "no"))
        # Same (domain, path, name): overwrites, never duplicates.
        jar.onCookieAdded(FakeCookie(".youtube.com", "/", "SID", "two",
                                     secure=True, expiry=1893456000))
        body = jar.path.read_text()
        assert body.splitlines()[1].endswith("\tSID\ttwo"), body
        assert "doubleclick" not in body
        assert len(body.splitlines()) == 2
        # Owner-only, and the temp file is gone (renamed, not left behind).
        assert stat.S_IMODE(jar.path.stat().st_mode) == 0o600
        assert not (jar.path.parent / "cookies.txt.tmp").exists()

        jar.onCookieRemoved(FakeCookie(".youtube.com", "/", "SID", "two",
                                       secure=True, expiry=1893456000))
        assert jar.path.read_text() == HEADER

        jar.clear()
        assert not jar.path.exists()
    print("jar file: ok")


if __name__ == "__main__":
    test_netscape_format()
    test_domain_filter()
    test_jar_writes_file()
    print("all checks passed")
