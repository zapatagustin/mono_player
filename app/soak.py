"""Live stability driver: `python main.py --soak` walks the REAL running app.

Every step posts genuine QKeyEvents into the live window -- with evdev
nativeScanCodes, so Main.qml's positional remapping (scanKey/posKey) resolves
them exactly as a keyboard would -- then polls observable state before moving
on. The harness is the feature: an uncaught Python exception, a QML error, or
a hung step ends the run with exit code 1 instead of hanging forever.

Network-dependent steps are marked in the log and in the summary, so a
YouTube outage reads differently from a crash. With no account or no network
the walk degrades: content-dependent steps are SKIPped and the chords, the
grid navigation and the quit path still get exercised.
"""

import asyncio
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEvent, Qt, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtQml import QQmlExpression, qmlContext

WATCHDOG_SECS = 180.0  # > the sum of every step budget below, with slack
POLL_SECS = 0.25

# nativeScanCode = evdev keycode + 8 (the historic X offset, preserved by Qt
# on Wayland and X11). Mirrors Main.qml's scanKey table keyed by the physical
# QWERTY position; app/test_soak.py asserts the two never drift apart.
SCAN = {
    "1": 10, "2": 11, "3": 12, "4": 13, "5": 14,
    "6": 15, "7": 16, "8": 17, "9": 18, "0": 19,
    "q": 24, "w": 25, "e": 26, "r": 27, "t": 28,
    "y": 29, "u": 30, "i": 31, "o": 32, "p": 33,
    "a": 38, "s": 39, "d": 40, "f": 41, "g": 42,
    "h": 43, "j": 44, "k": 45, "l": 46,
    "z": 52, "x": 53, "c": 54, "v": 55, "b": 56,
    "n": 57, "m": 58, "/": 61,
}

# Deliberately outside SCAN: posKey falls back to the keysym for these, which
# is what a real Return/Escape/Space delivers too.
NAMED = {
    "Enter": (Qt.Key.Key_Return, 36, "\r"),
    "Esc": (Qt.Key.Key_Escape, 9, "\x1b"),
    "Space": (Qt.Key.Key_Space, 65, " "),
}

# QML runtime errors reach the message handler as *warnings* (Qt logs the qml
# category at warning level), so plain warnings cannot be failed wholesale --
# match the engine's error vocabulary instead.
# ecomono: marker list, not a parser. Ceiling: an error shape not listed here
# is read as a warning. Add the marker when a real run surfaces one.
QML_ERROR_MARKERS = (
    "Error:",            # TypeError:, ReferenceError:, SyntaxError:, Error:
    "is not a function",
    "Unable to assign",
    "Cannot assign",
)


def key_of(char: str):
    """Qt.Key for a printable ASCII character (Qt::Key == uppercase ASCII)."""
    try:
        return Qt.Key(ord(char.upper()))
    except ValueError:
        return Qt.Key.Key_unknown


def expand(keys) -> list[tuple]:
    """Walk tokens -> (Qt.Key, nativeScanCode, text) triples.

    Tokens: one positional character ("j", "/", "3"), a NAMED key ("Enter"),
    or "type:<text>" for literal TextField input (scancode 0 -- TextFields
    never route through posKey, they read event.text()).

    ecomono: no modifier syntax; this walk needs none. Ceiling: shifted
    bindings (gT, S-s) are unreachable until a "S-" prefix is handled here.
    """
    out = []
    for tok in keys:
        if tok.startswith("type:"):
            out += [(key_of(c), 0, c) for c in tok[5:]]
        elif tok in NAMED:
            out.append(NAMED[tok])
        else:
            out.append((key_of(tok), SCAN[tok], tok))
    return out


def is_qml_error(msg_type, message: str) -> bool:
    """True when a Qt message must fail the run. Warnings pass unless they
    carry the QML engine's error vocabulary."""
    if msg_type in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        return True
    return msg_type == QtMsgType.QtWarningMsg and any(
        m in message for m in QML_ERROR_MARKERS)


# --- steps -----------------------------------------------------------------

@dataclass(frozen=True)
class Step:
    name: str
    keys: tuple = ()
    delay: float = 0.4          # human-ish pause after the keys land
    timeout: float = 2.0        # budget for `check` to become true
    check: Callable | None = None
    network: bool = False       # needs YouTube reachable
    auth: bool = False          # needs a logged-in account
    content: bool = False       # meaningless on an empty feed
    watch: bool = False         # meaningless outside watch mode
    repeat: bool = False        # re-post keys per poll; idempotent steps only


def _rows(s) -> bool:
    return s.feed.rowCount() > 0


def _mode(want):
    return lambda s: s.probe()[0] == want


def _paused(s) -> bool:
    return s.probe()[3]


def _resumed(s) -> bool:
    _, _, pos, paused = s.probe()
    if paused:
        return False
    s.marks["pos"] = pos  # baseline for the seek that follows
    return True


def _started(s) -> bool:
    mode, _, pos, _ = s.probe()
    if mode != "WATCH" or pos < 0:
        return False
    s.marks["pos"] = pos
    return True


def _pos_changed(delta: float):
    """Position moved by >= delta since the last passing position check --
    playback advancing, or a seek landing."""
    def check(s) -> bool:
        pos = s.probe()[2]
        if pos >= 0 and abs(pos - s.marks.get("pos", -1e9)) >= delta:
            s.marks["pos"] = pos
            return True
        return False
    return check


QUIT_STEP = "quit (q)"

# ecomono: hardcoded walk. Ceiling: one scripted path, no randomization and
# no repetition count. Both are a for-loop around STEPS when needed.
STEPS = (
    Step("home feed (gh)", ("g", "h"), delay=1.0, timeout=10.0,
         check=_rows, network=True, auth=True),
    Step("grid nav (j j k l h)", ("j", "j", "k", "l", "h"), delay=0.5,
         check=_mode("BROWSE")),
    Step("subscriptions (gs)", ("g", "s"), delay=1.0, timeout=10.0,
         check=_rows, network=True, auth=True),
    Step("history (gy)", ("g", "y"), delay=1.0, timeout=10.0,
         check=_rows, network=True, auth=True),
    Step("playlists (gp)", ("g", "p"), delay=1.0, timeout=10.0,
         check=_rows, network=True, auth=True),
    Step("home again (gh)", ("g", "h"), delay=1.0, timeout=10.0,
         check=_rows, network=True, auth=True),
    Step("search prompt (/)", ("/",), delay=0.5, check=_mode("PROMPT")),
    Step("type query", ("type:lofi",), delay=0.5, check=_mode("PROMPT")),
    Step("submit search (Enter)", ("Enter",), delay=1.0, timeout=15.0,
         check=_rows, network=True),
    Step("play first result (Enter)", ("Enter",), delay=1.0, timeout=30.0,
         check=_started, network=True, content=True),
    Step("playback advances", (), delay=3.0, timeout=10.0,
         check=_pos_changed(1.0), network=True, watch=True),
    Step("pause (Space)", ("Space",), delay=0.5, check=_paused, watch=True),
    Step("unpause (Space)", ("Space",), delay=0.5, check=_resumed, watch=True),
    Step("seek forward (l)", ("l",), delay=0.5, timeout=5.0,
         check=_pos_changed(4.0), watch=True),
    Step("seek back (h)", ("h",), delay=0.5, timeout=5.0,
         check=_pos_changed(4.0), watch=True),
    # Esc is idempotent from watch mode (closes a panel, then leaves), so it
    # is safe to re-post while polling.
    Step("back to browse (Esc)", ("Esc",), delay=0.5, timeout=5.0,
         check=_mode("BROWSE"), watch=True, repeat=True),
    Step(QUIT_STEP, ("q",), delay=0.5, timeout=5.0),
)


# --- summary ---------------------------------------------------------------

def verdict_ok(results, fatal) -> bool:
    """A run passes only with zero FAILs and zero fatals. SKIPs are a
    degraded environment, not a regression, so they do not fail the run."""
    return not fatal and not any(r[1] == "FAIL" for r in results)


def format_summary(results, fatal, elapsed: float) -> str:
    """Fixed-width report. Last line is the verdict, and it is the only line
    a caller needs to grep."""
    width = max([len(r[0]) for r in results] + [12])
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    lines = ["", "soak summary", "=" * (width + 34)]
    for name, status, note, secs, network in results:
        counts[status] = counts.get(status, 0) + 1
        tag = status + ("/net" if network and status == "FAIL" else "")
        lines.append(f"  {name:<{width}}  {tag:<9} {secs:5.1f}s  {note}".rstrip())
    lines.append("=" * (width + 34))
    lines.append(f"  {counts['PASS']} passed, {counts['FAIL']} failed,"
                 f" {counts['SKIP']} skipped in {elapsed:.1f}s")
    if counts["SKIP"]:
        lines.append("  degraded run: skipped steps needed an account, a"
                     " non-empty feed or live playback")
    if any(r[4] for r in results if r[1] == "FAIL"):
        lines.append("  FAIL/net = network-dependent step; suspect YouTube or"
                     " credentials before the app")
    for msg in fatal:
        lines.append("  fatal: " + msg.rstrip().replace("\n", "\n         "))
    lines.append("  verdict: "
                 + ("PASS" if verdict_ok(results, fatal) else "FAIL"))
    return "\n".join(lines)


# --- driver ----------------------------------------------------------------

_PROBE = ("(function(){var p = playerView.activePlayer;"
          " return [root.mode, grid.currentIndex, p ? p.position : -1,"
          " p ? p.paused : false].join('|')})()")


class Soak:
    """Owns the walk, the failure hooks and the exit code."""

    def __init__(self, app, root, feed, tabs, auth,
                 steps=STEPS, watchdog: float = WATCHDOG_SECS):
        self.app, self.root = app, root
        self.feed, self.tabs, self.auth = feed, tabs, auth
        self.steps, self.watchdog = steps, watchdog
        self.results: list[tuple] = []
        self.fatal: list[str] = []
        self.marks: dict[str, float] = {}
        self.exit_code = 1  # a run that never reports is a failed run
        self._t0 = time.monotonic()
        self._pending: str | None = None
        self._done = False

    # --- observation ---

    def qml(self, expr: str, default=None):
        """Evaluate a QML expression in Main.qml's own scope (so ids like
        playerView and grid resolve). Errors degrade to `default`."""
        e = QQmlExpression(qmlContext(self.root), self.root, expr)
        value, undefined = e.evaluate()
        if e.hasError() or undefined:
            e.clearError()
            return default
        return value

    def probe(self) -> tuple[str, int, float, bool]:
        """One QML round trip -> (mode, gridIndex, position, paused)."""
        mode, idx, pos, paused = str(
            self.qml(_PROBE, "?|-1|-1|false")).split("|")
        return mode, int(idx), float(pos), paused == "true"

    def state_line(self) -> str:
        mode, idx, pos, paused = self.probe()
        return (f"mode={mode} rows={self.feed.rowCount()} idx={idx}"
                f" tabs={self.tabs.rowCount()} active={self.tabs.activeIndex}"
                f" pos={pos:.1f} paused={str(paused).lower()}")

    # --- actuation ---

    def post(self, keys) -> None:
        """Press+release real key events at the window; the focused item
        routes them exactly as it routes the keyboard's."""
        for key, scan, text in expand(keys):
            for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                QGuiApplication.postEvent(self.root, QKeyEvent(
                    kind, key, Qt.KeyboardModifier.NoModifier,
                    scan, 0, 0, text))

    # --- logging ---

    def log(self, msg: str) -> None:
        print(f"soak {time.monotonic() - self._t0:7.2f}s  {msg}", flush=True)

    def record(self, step: Step, status: str, note: str = "",
               secs: float = 0.0) -> None:
        self._pending = None
        self.results.append((step.name, status, note, secs, step.network))
        tag = status + ("/net" if step.network and status == "FAIL" else "")
        self.log(f"{tag:<9} {step.name}"
                 + (f" -- {note}" if note else "")
                 + f"  [{self.state_line()}]")

    # --- run ---

    def start(self) -> None:
        sys.excepthook = self._excepthook
        qInstallMessageHandler(self._message)
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(self._loop_exception)
        self.app.aboutToQuit.connect(self._on_quit)
        self._task = loop.create_task(self._run())

    async def _run(self) -> None:
        self.log(f"start: {len(self.steps)} steps,"
                 f" watchdog {self.watchdog:.0f}s,"
                 f" loggedIn={self.auth.loggedIn}")
        try:
            await asyncio.wait_for(self._walk(), self.watchdog)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            self._abort(f"watchdog: run exceeded {self.watchdog:.0f}s"
                        f" (hung at: {self._pending})")
        except Exception:
            self._abort("driver crashed:\n" + traceback.format_exc())
        self._finish()
        self.app.quit()

    async def _walk(self) -> None:
        for step in self.steps:
            self._pending = step.name
            if step.name == QUIT_STEP:
                # Terminal: Qt.quit() tears the loop down, so this step is
                # recorded from the aboutToQuit hook, not from here.
                self.post(step.keys)
                await asyncio.sleep(step.timeout)
                self.record(step, "FAIL", "app still running after q")
                continue
            started = time.monotonic()
            skip = self._skip_reason(step)
            # Keys go out even when skipping: the chord still exercises the
            # dispatcher, only the state assertion is meaningless.
            self.post(step.keys)
            await asyncio.sleep(step.delay)
            if skip:
                self.record(step, "SKIP", skip, time.monotonic() - started)
                continue
            ok = await self._await_check(step)
            self.record(step, "PASS" if ok else "FAIL",
                        "" if ok else f"check unmet after {step.timeout:.0f}s",
                        time.monotonic() - started)

    def _skip_reason(self, step: Step) -> str:
        if step.auth and not self.auth.loggedIn:
            return "no account (login required)"
        if step.content and self.feed.rowCount() == 0:
            return "empty feed (no network or no account)"
        if step.watch and self.probe()[0] != "WATCH":
            return "no live playback"
        return ""

    async def _await_check(self, step: Step) -> bool:
        if step.check is None:
            return True
        deadline = time.monotonic() + step.timeout
        while True:
            if step.check(self):
                return True
            if time.monotonic() >= deadline:
                return False
            if step.repeat:
                self.post(step.keys)
            await asyncio.sleep(POLL_SECS)

    # --- failure hooks ---

    def _abort(self, msg: str) -> None:
        self.fatal.append(msg)
        self.log("FATAL " + msg.splitlines()[0])

    def _excepthook(self, etype, value, tb) -> None:
        self._abort("uncaught exception:\n" + "".join(
            traceback.format_exception(etype, value, tb)))
        self._finish()
        self.app.quit()

    def _loop_exception(self, loop, context) -> None:
        exc = context.get("exception")
        self._abort("asyncio: " + ("".join(traceback.format_exception(
            type(exc), exc, exc.__traceback__)) if exc
            else context.get("message", "unknown")))
        self._finish()
        self.app.quit()

    def _message(self, msg_type, context, message) -> None:
        if not self._done and is_qml_error(msg_type, message):
            self._abort("qml: " + message)
        print(message, file=sys.stderr, flush=True)

    def _on_quit(self) -> None:
        """aboutToQuit: the loop is about to go away, so report from here."""
        if self._pending is not None:
            expected = self._pending == QUIT_STEP
            self.record(Step(self._pending), "PASS" if expected else "FAIL",
                        "app quit" if expected else "app quit mid-step")
        self._finish()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        qInstallMessageHandler(None)  # teardown noise is not this run's result
        print(format_summary(self.results, self.fatal,
                             time.monotonic() - self._t0), flush=True)
        self.exit_code = 0 if verdict_ok(self.results, self.fatal) else 1
