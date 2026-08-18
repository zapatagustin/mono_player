"""Google account auth via gpsoauth (the protocol microG speaks). The master
token grants the whole account: it lives in the Secret Service keyring only —
never a file, never a log line, never sqlite. Bearer tokens are short-lived
and never persisted. Login is optional; only sync features need it."""

import asyncio
import json
import secrets
import time

from PySide6.QtCore import Property, QObject, Signal, Slot

import innertube

KEYRING_SERVICE = "mono_player"

# InnerTube ANDROID scope: the official YouTube app's oauth service triplet.
YT_SERVICE = "oauth2:https://www.googleapis.com/auth/youtube"
YT_APP = "com.google.android.youtube"
YT_CLIENT_SIG = "24bb24c05e47e0aefa68a58a766179d9b613a600"

BEARER_TTL_FALLBACK = 3000.0  # gpsoauth may omit Expiry; ~50min is safe
BEARER_SLACK = 60.0


def cookie_token(cookie) -> str | None:
    """The oauth_token cookie google sets after EmbeddedSetup login."""
    if bytes(cookie.name()).decode(errors="replace") != "oauth_token":
        return None
    if "google" not in cookie.domain():
        return None
    return bytes(cookie.value()).decode(errors="replace")


class AuthManager(QObject):
    loggedInChanged = Signal()
    showLoginChanged = Signal()
    loginError = Signal(str)
    channelChanged = Signal(str)  # active YouTube channel (brand) name
    accountChanged = Signal(str)  # active Google account email

    def __init__(self, store, client=None, keyring_mod=None, exchange_fn=None,
                 oauth_fn=None, now_fn=None, parent=None):
        super().__init__(parent)
        if keyring_mod is None:
            import keyring as keyring_mod
        if exchange_fn is None or oauth_fn is None:
            import gpsoauth
            exchange_fn = exchange_fn or gpsoauth.exchange_token
            oauth_fn = oauth_fn or gpsoauth.perform_oauth
        self._store = store
        self._keyring = keyring_mod
        self._exchange_fn = exchange_fn
        self._oauth_fn = oauth_fn
        self._now = now_fn or time.time

        aid = store.meta_get("android_id")
        if not aid:
            aid = secrets.token_hex(8)
            store.meta_set("android_id", aid)
        self._android_id = aid

        self._client = client
        self._channels_fn = innertube.list_channels
        self._email = store.meta_get("auth_email")
        # Known Google logins, in cycle order. Migrate legacy installs:
        # a lone auth_email becomes the list, the global channel keys
        # become that account's namespaced ones.
        try:
            self._emails = json.loads(store.meta_get("auth_emails") or "[]")
        except ValueError:
            self._emails = []
        if self._email and self._email not in self._emails:
            self._emails.append(self._email)
            store.meta_set("auth_emails", json.dumps(self._emails))
        if self._email and store.meta_get(f"page_id:{self._email}") is None \
                and store.meta_get("page_id") is not None:
            store.meta_set(f"page_id:{self._email}",
                           store.meta_get("page_id"))
            store.meta_set(f"channel_name:{self._email}",
                           store.meta_get("channel_name") or "")
            store.meta_set("page_id", None)
            store.meta_set("channel_name", None)
        # Restore the active channel delegation before any account fetch.
        self._page_id = (store.meta_get(f"page_id:{self._email}") or "") \
            if self._email else ""
        self._channel_name = (store.meta_get(f"channel_name:{self._email}")
                              or "") if self._email else ""
        innertube.set_page_id(self._page_id)
        self._pending_email = None
        self._show_login = False
        self._bearer = None
        self._bearer_expiry = 0.0

    # --- properties ---

    def _get_logged_in(self) -> bool:
        return self._email is not None and self._master() is not None

    loggedIn = Property(bool, _get_logged_in, notify=loggedInChanged)

    def _get_show_login(self) -> bool:
        return self._show_login

    showLogin = Property(bool, _get_show_login, notify=showLoginChanged)

    def _master(self) -> str | None:
        if self._email is None:
            return None
        try:
            return self._keyring.get_password(KEYRING_SERVICE, self._email)
        except Exception as exc:
            print(f"auth: keyring unavailable: {type(exc).__name__}")
            return None

    # --- login flow ---

    @Slot(str)
    def startLogin(self, email: str):
        email = email.strip()
        if not email:
            return
        self._pending_email = email
        self._set_show_login(True)

    @Slot()
    def cancelLogin(self):
        self._pending_email = None
        self._set_show_login(False)

    def onCookieAdded(self, cookie):
        token = cookie_token(cookie)
        if token and self._pending_email:
            asyncio.get_event_loop().create_task(self._exchange(token))

    async def _exchange(self, oauth_token: str):
        email = self._pending_email
        resp = await asyncio.to_thread(
            self._exchange_fn, email, oauth_token, self._android_id
        )
        master = resp.get("Token")
        if not master:
            self.loginError.emit(resp.get("Error", "token exchange failed"))
            return
        self._keyring.set_password(KEYRING_SERVICE, email, master)
        if email not in self._emails:
            self._emails.append(email)
            self._store.meta_set("auth_emails", json.dumps(self._emails))
        self._pending_email = None
        self._set_show_login(False)
        self._activate(email)

    # --- bearer tokens (minted on demand, never persisted) ---

    async def bearer(self) -> str | None:
        if not self._get_logged_in():
            return None
        if self._bearer and self._now() < self._bearer_expiry - BEARER_SLACK:
            return self._bearer
        resp = await asyncio.to_thread(
            self._oauth_fn, self._email, self._master(), self._android_id,
            YT_SERVICE, YT_APP, YT_CLIENT_SIG,
        )
        auth = resp.get("Auth")
        if not auth:
            self.loginError.emit(resp.get("Error", "bearer mint failed"))
            return None
        self._bearer = auth
        self._bearer_expiry = float(
            resp.get("Expiry", self._now() + BEARER_TTL_FALLBACK)
        )
        return auth

    # --- Google account (login) switching ---

    def _activate(self, email: str):
        """Make email the active login: fresh bearer, its channel state."""
        self._email = email
        self._store.meta_set("auth_email", email)
        self._bearer = None
        self._bearer_expiry = 0.0
        self._page_id = self._store.meta_get(f"page_id:{email}") or ""
        self._channel_name = (
            self._store.meta_get(f"channel_name:{email}") or "")
        innertube.set_page_id(self._page_id)
        self.loggedInChanged.emit()
        self.channelChanged.emit(self._channel_name)  # binding + home reload
        self.accountChanged.emit(email)

    @Slot()
    def cycleAccount(self):
        order = list(self._emails)
        if self._email in order:
            i = order.index(self._email)
            order = order[i + 1:] + order[:i]
        for cand in order:
            master = None
            try:
                master = self._keyring.get_password(KEYRING_SERVICE, cand)
            except Exception as exc:
                print(f"auth: keyring unavailable: {type(exc).__name__}")
            if master:
                self._activate(cand)
                return
            # Dead entry (keyring wiped externally): drop from the cycle.
            self._emails.remove(cand)
            self._store.meta_set("auth_emails", json.dumps(self._emails))
        self.loginError.emit("no other account (gL adds)")

    accountEmail = Property(str, lambda s: s._email or "",
                            notify=loggedInChanged)
    accountCount = Property(int, lambda s: len(s._emails),
                            notify=loggedInChanged)

    # --- channel (brand account) switching ---

    @Slot()
    def cycleChannel(self):
        asyncio.get_event_loop().create_task(self._cycle())

    async def _cycle(self):
        bearer = await self.bearer()
        if bearer is None:
            self.loginError.emit("login required (gl)")
            return
        try:
            channels = await self._channels_fn(self._client, bearer)
        except Exception as exc:
            print(f"auth: channel list failed: {exc!r}")
            self.loginError.emit("channel list failed")
            return
        if len(channels) < 2:
            self.loginError.emit("only one channel on this account")
            return
        # Match the current channel locally (isSelected may not reflect the
        # delegation header), then step to the next one.
        current = next(
            (i for i, c in enumerate(channels)
             if (c.gaia_id if c.delegated else "") == self._page_id), 0)
        target = channels[(current + 1) % len(channels)]
        self._set_channel(target.gaia_id if target.delegated else "",
                          target.name)
        self.channelChanged.emit(target.name)

    def _set_channel(self, page_id: str, name: str):
        self._page_id = page_id
        self._channel_name = name
        if self._email:
            self._store.meta_set(f"page_id:{self._email}", page_id)
            self._store.meta_set(f"channel_name:{self._email}", name)
        innertube.set_page_id(page_id)

    channelName = Property(str, lambda s: s._channel_name,
                           notify=channelChanged)

    @Slot()
    def logout(self):
        """Sign out the ACTIVE account; fall through to the next one."""
        email = self._email
        if email is not None:
            try:
                self._keyring.delete_password(KEYRING_SERVICE, email)
            except Exception:
                pass
            if email in self._emails:
                self._emails.remove(email)
                self._store.meta_set("auth_emails", json.dumps(self._emails))
            self._store.meta_set(f"page_id:{email}", None)
            self._store.meta_set(f"channel_name:{email}", None)
        self._bearer = None
        if self._emails:
            self._activate(self._emails[0])
            return
        self._store.meta_set("auth_email", None)
        self._email = None
        self._set_channel("", "")
        self.loggedInChanged.emit()

    def _set_show_login(self, value: bool):
        if value != self._show_login:
            self._show_login = value
            self.showLoginChanged.emit()
