"""mono_player entry point: QML window over the MpvBridge C++ module, with
httpx's asyncio loop riding Qt's via qasync."""

import asyncio
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
from tabmanager import TabManager  # noqa: E402
from tabstore import TabStore  # noqa: E402
from thumbs import ThumbCache  # noqa: E402

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
    feed = FeedModel(
        make_client(), FeedStore(data_dir / "mono.db"),
        ThumbCache(cache_dir / "thumbs"), auth=auth,
    )
    tabs = TabManager(tab_store)

    login_profile = None
    if WEBENGINE:
        # Off-the-record: the google session cookies never touch disk; only
        # the exchanged master token survives, in the keyring.
        login_profile = QQuickWebEngineProfile()
        login_profile.setOffTheRecord(True)
        login_profile.setHttpUserAgent(LOGIN_UA)
        login_profile.cookieStore().cookieAdded.connect(auth.onCookieAdded)

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(ROOT / "bridge" / "build"))
    engine.rootContext().setContextProperty("feed", feed)
    engine.rootContext().setContextProperty("tabs", tabs)
    engine.rootContext().setContextProperty("auth", auth)
    engine.rootContext().setContextProperty("th", theme)
    engine.rootContext().setContextProperty("authAvailable", WEBENGINE)
    engine.rootContext().setContextProperty("loginProfile", login_profile)
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

    # Dev harness: `main.py "query"` searches; `--play <id>` opens a tab;
    # `--resume` re-materializes the restored active tab.
    if len(sys.argv) > 2 and sys.argv[1] == "--play":
        QTimer.singleShot(0, lambda: tabs.playVideo(sys.argv[2], sys.argv[2]))
    elif len(sys.argv) > 1 and sys.argv[1] == "--resume":
        QTimer.singleShot(0, lambda: tabs.activate(tabs.activeIndex))
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
            tabs.playVideo(vids[cycle[0] % 3], f"stress {cycle[0]}")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
