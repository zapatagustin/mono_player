"""Checks for the subscriptions parser (ANDROID client shapes, defensive)
and AuthManager: master token lives only in the keyring, bearer tokens are
minted on demand and cached until expiry. gpsoauth/keyring are injected."""

import asyncio
import tempfile
from pathlib import Path

from PySide6.QtNetwork import QNetworkCookie

from auth import AuthManager, cookie_token
from innertube import Video, parse_subscriptions
from tabstore import TabStore


# --- subscriptions parser ---

def vwc(vid, title, length=None):
    r = {
        "videoWithContextRenderer": {
            "videoId": vid,
            "headline": {"runs": [{"text": title}]},
            "shortBylineText": {"runs": [{"text": "chan"}]},
            "lengthText": length or {"runs": [{"text": "1:23"}]},
            "thumbnail": {"thumbnails": [{"url": "https://t/x.jpg"}]},
        }
    }
    return r


def subs_response(items):
    return {
        "contents": {
            "singleColumnBrowseResultsRenderer": {
                "tabs": [
                    {"tabRenderer": {"content": {"sectionListRenderer": {
                        "contents": [{"itemSectionRenderer": {"contents": items}}]
                    }}}}
                ]
            }
        }
    }


def test_parse_subscriptions():
    data = subs_response([
        vwc("dQw4w9WgXcQ", "One"),
        vwc("aqz-KE-bpKQ", "Two", length={"simpleText": "4:56"}),
    ])
    assert parse_subscriptions(data) == [
        Video("dQw4w9WgXcQ", "One", "chan", "1:23", "https://t/x.jpg"),
        Video("aqz-KE-bpKQ", "Two", "chan", "4:56", "https://t/x.jpg"),
    ]

    # Shelves/ads skipped; bad video ids skipped; garbage degrades.
    data = subs_response([{"shelfRenderer": {}}, vwc("aqz-KE-bpKQ", "Two")])
    assert [v.video_id for v in parse_subscriptions(data)] == ["aqz-KE-bpKQ"]
    assert parse_subscriptions(subs_response([vwc("../evil", "x")])) == []
    assert parse_subscriptions({}) == []
    assert parse_subscriptions(None) == []
    assert parse_subscriptions({"contents": 3}) == []
    print("subscriptions parser: ok")


# --- auth manager ---

class FakeKeyring:
    def __init__(self):
        self.secrets = {}

    def get_password(self, service, user):
        return self.secrets.get((service, user))

    def set_password(self, service, user, value):
        self.secrets[(service, user)] = value

    def delete_password(self, service, user):
        del self.secrets[(service, user)]


def make_auth(store, kr, exchange=None, oauth=None, now=None):
    return AuthManager(
        store,
        keyring_mod=kr,
        exchange_fn=exchange or (lambda *a: {"Token": "aas_et/master"}),
        oauth_fn=oauth or (lambda *a, **k: {"Auth": "bearer1"}),
        now_fn=now or (lambda: 1000.0),
    )


def test_auth_manager():
    with tempfile.TemporaryDirectory() as tmp:
        store = TabStore(Path(tmp) / "mono.db")
        kr = FakeKeyring()
        exchanges = []

        def exchange(email, token, android_id):
            exchanges.append((email, token, android_id))
            return {"Token": "aas_et/master"}

        m = make_auth(store, kr, exchange=exchange)
        assert not m.loggedIn
        assert not m.showLogin

        # android_id generated once, persisted, stable across instances.
        aid = store.meta_get("android_id")
        assert aid and make_auth(store, kr)._android_id == aid

        # Cookie filter: only google's oauth_token yields a value.
        c = QNetworkCookie(b"oauth_token", b"tok123")
        c.setDomain(".google.com")
        assert cookie_token(c) == "tok123"
        other = QNetworkCookie(b"SID", b"x")
        other.setDomain(".google.com")
        assert cookie_token(other) is None

        # Login: email first, then the intercepted cookie is exchanged; the
        # master token lands in the keyring, never in sqlite.
        errors = []
        m.loginError.connect(errors.append)
        m.startLogin("a@b.c")
        assert m.showLogin
        asyncio.run(m._exchange("tok123"))
        assert exchanges == [("a@b.c", "tok123", aid)]
        assert kr.get_password("mono_player", "a@b.c") == "aas_et/master"
        assert m.loggedIn and not m.showLogin and errors == []
        assert store.meta_get("auth_email") == "a@b.c"

        # Failed exchange: error surfaced, state unchanged.
        m2 = make_auth(store, FakeKeyring(),
                       exchange=lambda *a: {"Error": "BadAuthentication"})
        m2errors = []
        m2.loginError.connect(m2errors.append)
        m2.startLogin("x@y.z")
        asyncio.run(m2._exchange("bad"))
        assert m2errors == ["BadAuthentication"]

        # Bearer: minted once, cached until expiry, re-minted after.
        clock = [1000.0]
        mints = []

        def oauth(email, master, android_id, service, app, sig):
            mints.append(master)
            return {"Auth": f"bearer{len(mints)}", "Expiry": clock[0] + 3600}

        m3 = make_auth(store, kr, oauth=oauth, now=lambda: clock[0])
        assert m3.loggedIn  # restored: email in meta + token in keyring
        assert asyncio.run(m3.bearer()) == "bearer1"
        assert asyncio.run(m3.bearer()) == "bearer1"
        assert mints == ["aas_et/master"]
        clock[0] += 4000
        assert asyncio.run(m3.bearer()) == "bearer2"

        # Logout wipes the keyring entry and the email.
        m3.logout()
        assert not m3.loggedIn
        assert kr.get_password("mono_player", "a@b.c") is None
        assert asyncio.run(m3.bearer()) is None
    print("auth manager: ok")


if __name__ == "__main__":
    test_parse_subscriptions()
    test_auth_manager()
    print("all checks passed")
