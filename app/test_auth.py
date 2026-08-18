"""Checks for the subscriptions parser (ANDROID client shapes, defensive)
and AuthManager: master token lives only in the keyring, bearer tokens are
minted on demand and cached until expiry. gpsoauth/keyring are injected."""

import asyncio
import tempfile
from pathlib import Path

from PySide6.QtNetwork import QNetworkCookie

from auth import AuthManager, cookie_token
from innertube import Channel, parse_accounts_list
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


def account_item(name, gaia, selected=False, has_channel=True,
                 delegation="GAIA_DELEGATION_TYPE_LATE"):
    return {"accountItem": {
        "accountName": {"runs": [{"text": name}]},
        "isSelected": selected,
        "hasChannel": has_channel,
        "serviceEndpoint": {"signInEndpoint": {"directSigninIdentity": {
            "effectiveObfuscatedGaiaId": gaia,
            "gaiaDelegationType": delegation,
        }}},
    }}


def test_parse_accounts_list():
    data = {"anything": [
        account_item("Personal", "112910", selected=True, has_channel=False,
                     delegation="GAIA_DELEGATION_TYPE_NONE"),
        account_item("El Mono", "113497"),
        {"accountItem": {"accountName": "garbage"}},
    ]}
    chans = parse_accounts_list(data)
    assert chans == [
        Channel("Personal", "112910", True, False),
        Channel("El Mono", "113497", False, True),
    ]
    # delegated flag: NONE means base identity (no X-Goog-PageId header).
    assert not chans[0].delegated and chans[1].delegated
    assert parse_accounts_list({}) == []
    assert parse_accounts_list(None) == []
    print("accounts list parser: ok")


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


def test_channel_cycle():
    import innertube

    async def channels_fn(client, bearer):
        return [
            Channel("Personal", "112910", True, False),
            Channel("El Mono", "113497", False, True),
        ]

    with tempfile.TemporaryDirectory() as tmp:
        store = TabStore(Path(tmp) / "mono.db")
        kr = FakeKeyring()
        kr.set_password("mono_player", "a@b.c", "aas_et/master")
        store.meta_set("auth_email", "a@b.c")
        m = make_auth(store, kr)
        m._channels_fn = channels_fn
        names = []
        m.channelChanged.connect(names.append)

        # Cycle: Personal (base) -> El Mono (delegated, header set).
        # Channel state persists per account (namespaced meta keys).
        asyncio.run(m._cycle())
        assert names == ["El Mono"]
        assert store.meta_get("page_id:a@b.c") == "113497"
        assert innertube._page_id == "113497"

        # Cycle again: back to the base identity (no header).
        asyncio.run(m._cycle())
        assert names == ["El Mono", "Personal"]
        assert store.meta_get("page_id:a@b.c") in ("", None)
        assert innertube._page_id == ""

        # A fresh manager restores the persisted channel on init.
        asyncio.run(m._cycle())  # -> El Mono again
        m2 = make_auth(store, kr)
        assert innertube._page_id == "113497"
        assert m2.channelName == "El Mono"

        # Logout clears the delegation.
        m2.logout()
        assert innertube._page_id == ""
    print("channel cycle: ok")


def test_account_cycle():
    import json

    import innertube

    with tempfile.TemporaryDirectory() as tmp:
        store = TabStore(Path(tmp) / "mono.db")
        kr = FakeKeyring()
        kr.set_password("mono_player", "a@b.c", "master_a")
        store.meta_set("auth_email", "a@b.c")
        # Legacy single-account install: global channel keys, no list.
        store.meta_set("page_id", "999")
        store.meta_set("channel_name", "Legacy")

        # Init migrates: email -> account list, channel keys -> namespaced.
        m = make_auth(store, kr)
        assert json.loads(store.meta_get("auth_emails")) == ["a@b.c"]
        assert store.meta_get("page_id:a@b.c") == "999"
        assert store.meta_get("page_id") is None
        assert m.channelName == "Legacy" and innertube._page_id == "999"
        assert m.accountEmail == "a@b.c" and m.accountCount == 1
        m._set_channel("", "")  # back to base for the rest

        # Only one account: cycling is a no-op with a hint.
        errors = []
        m.loginError.connect(errors.append)
        m.cycleAccount()
        assert errors == ["no other account (gL adds)"]
        assert m.accountEmail == "a@b.c"

        # Logging in while logged in ADDS an account and switches to it.
        m.startLogin("b@x.y")
        asyncio.run(m._exchange("tok"))
        assert m.loggedIn and m.accountEmail == "b@x.y"
        assert m.accountCount == 2
        assert json.loads(store.meta_get("auth_emails")) == ["a@b.c", "b@x.y"]

        # Per-account channel: brand on b survives a round trip through a.
        m._set_channel("113497", "El Mono")
        accounts = []
        m.accountChanged.connect(accounts.append)
        m.cycleAccount()
        assert m.accountEmail == "a@b.c"
        assert m.channelName == "" and innertube._page_id == ""
        m.cycleAccount()
        assert m.accountEmail == "b@x.y"
        assert m.channelName == "El Mono" and innertube._page_id == "113497"
        assert accounts == ["a@b.c", "b@x.y"]

        # Switch clears the bearer cache: next mint uses the NEW master.
        mints = []

        def oauth(email, master, *a):
            mints.append((email, master))
            return {"Auth": "t", "Expiry": 5000.0}

        m._oauth_fn = oauth
        asyncio.run(m.bearer())
        m.cycleAccount()  # -> a@b.c
        asyncio.run(m.bearer())
        assert mints == [("b@x.y", "aas_et/master"), ("a@b.c", "master_a")]

        # Keyring entry gone: the dead account is dropped from the cycle.
        del kr.secrets[("mono_player", "b@x.y")]
        errors.clear()
        m.cycleAccount()
        assert errors == ["no other account (gL adds)"]
        assert json.loads(store.meta_get("auth_emails")) == ["a@b.c"]

        # Logout falls through to the remaining account; last one signs out.
        m.startLogin("b@x.y")
        asyncio.run(m._exchange("tok2"))
        m._set_channel("113497", "El Mono")
        m.logout()  # signs out b, falls through to a
        assert m.loggedIn and m.accountEmail == "a@b.c"
        assert kr.get_password("mono_player", "b@x.y") is None
        assert store.meta_get("page_id:b@x.y") is None
        m.logout()  # last account: fully signed out
        assert not m.loggedIn and m.accountEmail == ""
        assert json.loads(store.meta_get("auth_emails")) == []
        assert innertube._page_id == ""
    print("account cycle: ok")


if __name__ == "__main__":
    test_parse_subscriptions()
    test_parse_accounts_list()
    test_auth_manager()
    test_channel_cycle()
    test_account_cycle()
    print("all checks passed")
