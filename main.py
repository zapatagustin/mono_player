"""mono_player entry point: QML window over the MpvBridge C++ module, with
httpx's asyncio loop riding Qt's via qasync."""

import asyncio
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import qasync
from PySide6.QtCore import QStandardPaths, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

# QtWebEngine backs the login screen ONLY; without it the app runs logged out.
try:
    from PySide6.QtWebEngineQuick import QQuickWebEngineProfile, QtWebEngineQuick
    WEBENGINE = True
except ImportError:
    WEBENGINE = False

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "app"))

from auth import AuthManager  # noqa: E402
from theme import Theme  # noqa: E402
from feedmodel import FeedModel  # noqa: E402
from feedstore import FeedStore  # noqa: E402
from net import make_client  # noqa: E402
from comments import CommentsModel  # noqa: E402
from related import RelatedModel  # noqa: E402
from tabmanager import TabManager  # noqa: E402
from tabstore import TabStore  # noqa: E402
from thumbs import ThumbCache  # noqa: E402
from urlcache import StreamUrlCache  # noqa: E402

# The EmbeddedSetup login page expects a mobile UA; MinuteMaid is the marker
# the embedded-setup flow (microG, Aurora) identifies itself with.
LOGIN_UA = (
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/126.0.0.0 Mobile Safari/537.36 MinuteMaid"
)


POT_PORT = 4416  # bgutil provider default; the yt-dlp plugin polls it


def start_pot_provider() -> subprocess.Popen | None:
    """Launch the PO token provider (Node) so yt-dlp's plugin can fetch
    tokens; without it googlevideo intermittently 403s. Skips if one is
    already listening (another instance) or the pieces are missing."""
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", POT_PORT)) == 0:
            return None  # already running
    script = Path(sys.prefix) / "share/bgutil-ytdlp-pot-provider/build/main.js"
    node = shutil.which("node")
    if node is None or not script.exists():
        print("pot: provider unavailable (node or main.js missing);"
              " playback may hit 403s")
        return None
    return subprocess.Popen(
        [node, str(script)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    # The libmpv render API is OpenGL-only; QQuickFramebufferObject breaks on
    # any other RHI backend (GUIDELINE.org, Rendering).
    QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.OpenGL)
    if WEBENGINE:
        QtWebEngineQuick.initialize()  # must precede QGuiApplication

    app = QGuiApplication(sys.argv)
    app.setApplicationName("mono_player")

    theme = Theme()
    font = QFont(theme.fontFamily)
    font.setPixelSize(theme.fontSize)
    app.setFont(font)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    cache_dir = Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
    )
    data_dir = Path(
        QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    )
    tab_store = TabStore(data_dir / "mono.db")
    auth = AuthManager(tab_store)
    client = make_client()
    thumb_cache = ThumbCache(cache_dir / "thumbs")
    feed = FeedModel(
        client, FeedStore(data_dir / "mono.db"), thumb_cache, auth=auth,
    )
    tabs = TabManager(tab_store, url_cache=StreamUrlCache())
    related = RelatedModel(client, thumb_cache=thumb_cache)
    comments = CommentsModel(client)
    # Prefetch related whenever the active video changes — the panel and
    # the channel jump (gc) are then instant. Comments only track the id;
    # they load when their panel opens.
    tabs.currentVideoChanged.connect(related.load)
    tabs.currentVideoChanged.connect(comments.setCurrent)

    engine = QQmlApplicationEngine()

    # The login webview profile is created lazily on the first login open:
    # normal runs never touch WebEngine (no deprecation warning, no
    # Chromium libs). Connected BEFORE engine.load so this slot runs ahead
    # of the QML Loader's binding when showLogin flips.
    login_profile: list = [None]

    def ensure_login_profile():
        if not auth.showLogin or login_profile[0] is not None:
            return
        # Off-the-record: the google session cookies never touch disk; only
        # the exchanged master token survives, in the keyring.
        profile = QQuickWebEngineProfile()
        profile.setOffTheRecord(True)
        profile.setHttpUserAgent(LOGIN_UA)
        profile.cookieStore().cookieAdded.connect(auth.onCookieAdded)
        login_profile[0] = profile
        engine.rootContext().setContextProperty("loginProfile", profile)

    if WEBENGINE:
        auth.showLoginChanged.connect(ensure_login_profile)
    engine.addImportPath(str(ROOT / "bridge" / "build"))
    engine.rootContext().setContextProperty("feed", feed)
    engine.rootContext().setContextProperty("tabs", tabs)
    engine.rootContext().setContextProperty("auth", auth)
    engine.rootContext().setContextProperty("th", theme)
    engine.rootContext().setContextProperty("related", related)
    engine.rootContext().setContextProperty("comments", comments)
    engine.rootContext().setContextProperty("authAvailable", WEBENGINE)
    engine.rootContext().setContextProperty("loginProfile", None)
    engine.load(str(ROOT / "qml" / "Main.qml"))
    if not engine.rootObjects():
        return 1

    # Tab positions hit sqlite on switch/close/quit and on this heartbeat.
    persist_timer = QTimer(interval=10_000)
    persist_timer.timeout.connect(tabs.persistActive)
    persist_timer.start()
    app.aboutToQuit.connect(tabs.persistActive)

    pot = start_pot_provider()
    if pot is not None:
        app.aboutToQuit.connect(pot.terminate)

    # Dev knob: clean-quit after N seconds (exercises the real teardown
    # path, which SIGTERM'd test runs skip).
    quit_after = os.environ.get("MONO_QUIT_AFTER")
    if quit_after:
        QTimer.singleShot(int(float(quit_after) * 1000), app.quit)

    # Session restore: reopen where the app was closed — the active tab
    # resumes at its persisted position (browser-style). Behind it, the
    # browse view refreshes to the personalized home (cached feed paints
    # first, home replaces it when it arrives).
    if len(sys.argv) <= 1:
        if auth.loggedIn:
            QTimer.singleShot(0, feed.loadHome)
        if tabs.activeIndex >= 0:
            QTimer.singleShot(0, lambda: tabs.activate(tabs.activeIndex))

    # Dev harness: `main.py "query"` searches; `--play <id>` opens a tab.
    if len(sys.argv) > 2 and sys.argv[1] == "--play":
        QTimer.singleShot(0, lambda: tabs.playVideo(sys.argv[2], sys.argv[2]))
    elif len(sys.argv) > 1 and sys.argv[1] == "--login":
        QTimer.singleShot(0, lambda: auth.startLogin("dev@example.com"))
    elif len(sys.argv) > 1 and sys.argv[1] == "--stress":
        # Crash repro: rapid tab/loadfile churn (AV1 decoder teardown race).
        # Optional argv[2] = ytdl-format override (e.g. codec isolation).
        if len(sys.argv) > 2:
            fmt = sys.argv[2]
            QTimer.singleShot(
                0, lambda: tabs.mpvCommand.emit(["set", "ytdl-format", fmt]))
        vids = ["rZHrffKAc6k", "aqz-KE-bpKQ", "dQw4w9WgXcQ"]
        stress_timer = QTimer(interval=4000)
        cycle = [0]

        def stress():
            i = cycle[0]
            if i < 3:  # build up the pool
                tabs.openInNewTab(vids[i], f"stress tab {i}")
                tabs.activate(i)
            elif i % 4 == 3:  # reload into the active tab's player
                tabs.playVideo(vids[i % 3], f"stress replace {i}")
            else:  # live switch between paused players
                tabs.activate(i % 3)
            cycle[0] += 1
            print(f"stress: cycle {cycle[0]}")

        stress_timer.timeout.connect(stress)
        stress_timer.start()
    elif len(sys.argv) > 1:
        QTimer.singleShot(0, lambda: feed.search(sys.argv[1]))

    close_event = asyncio.Event()
    app.aboutToQuit.connect(close_event.set)
    with loop:
        loop.run_until_complete(close_event.wait())
    # Destroy the engine before the context objects (feed/tabs/theme/...)
    # go out of scope: live QML bindings otherwise re-evaluate against
    # dead context properties and spam TypeErrors on every exit.
    del engine
    return 0


if __name__ == "__main__":
    sys.exit(main())
