"""Offline checks for the soak driver's pure logic: the scancode table (it
must not drift from Main.qml's), token expansion, the QML error/warning
classifier, and the summary/verdict. No window, no engine, no network."""

import asyncio
import re
from pathlib import Path

from PySide6.QtCore import Qt, QtMsgType

import soak
from soak import (
    QUIT_STEP,
    STEPS,
    Step,
    expand,
    format_summary,
    is_qml_error,
    verdict_ok,
)

QML = (Path(__file__).resolve().parent.parent / "qml" / "Main.qml").read_text()


def test_scan_table_matches_main_qml():
    """soak.SCAN and Main.qml's scanKey describe the same physical keys: a
    silent drift here means the walk presses nothing the app listens to."""
    block = re.search(r"scanKey: \(\{(.*?)\}\)", QML, re.S).group(1)
    qml_table = {int(code): name
                 for code, name in re.findall(r"(\d+): Qt\.Key_(\w+)", block)}
    ours = {scan: char.upper() if char != "/" else "Slash"
            for char, scan in soak.SCAN.items()}
    assert ours == qml_table, set(ours.items()) ^ set(qml_table.items())


def test_named_keys_are_outside_the_table():
    # Return/Escape/Space must fall through posKey to the keysym; a scancode
    # that collided with the table would be remapped to a letter.
    for key, scan, _text in soak.NAMED.values():
        assert scan not in soak.SCAN.values(), (key, scan)


def test_expand_positional_named_and_typed():
    assert expand(("g", "h")) == [
        (Qt.Key.Key_G, 42, "g"), (Qt.Key.Key_H, 43, "h")]
    assert expand(("/",)) == [(Qt.Key.Key_Slash, 61, "/")]
    assert expand(("3",)) == [(Qt.Key.Key_3, 12, "3")]
    assert expand(("Enter",)) == [(Qt.Key.Key_Return, 36, "\r")]
    # typed text goes in as text with no scancode: TextField reads
    # event.text() and never routes through posKey
    assert expand(("type:ab",)) == [
        (Qt.Key.Key_A, 0, "a"), (Qt.Key.Key_B, 0, "b")]


def test_every_step_expands_and_quit_is_last():
    for step in STEPS:
        expand(step.keys)  # raises KeyError on an unmapped token
        assert step.timeout > 0 and step.delay >= 0, step.name
    assert STEPS[-1].name == QUIT_STEP
    assert sum(s.name == QUIT_STEP for s in STEPS) == 1
    # only idempotent steps may be re-posted while polling
    assert [s.name for s in STEPS if s.repeat] == ["back to browse (Esc)"]
    # the whole worst case must fit inside the watchdog
    budget = sum(s.delay + s.timeout for s in STEPS)
    assert budget < soak.WATCHDOG_SECS, budget


def test_qml_errors_fail_but_warnings_do_not():
    assert is_qml_error(QtMsgType.QtCriticalMsg, "anything")
    assert is_qml_error(QtMsgType.QtFatalMsg, "anything")
    assert is_qml_error(QtMsgType.QtWarningMsg,
                        "Main.qml:12: TypeError: Cannot read property 'x'")
    assert is_qml_error(QtMsgType.QtWarningMsg,
                        "Main.qml:9: ReferenceError: grid is not defined")
    assert is_qml_error(QtMsgType.QtWarningMsg,
                        "Main.qml:3: Unable to assign [undefined] to QString")
    assert not is_qml_error(QtMsgType.QtWarningMsg,
                            "QML Connections: Implicitly defined onFoo")
    assert not is_qml_error(QtMsgType.QtDebugMsg, "TypeError: not an error")
    assert not is_qml_error(QtMsgType.QtInfoMsg, "Error: still only info")


def _row(name, status, network=False):
    return (name, status, "", 1.0, network)


def test_verdict_skips_do_not_fail_the_run():
    assert verdict_ok([_row("a", "PASS"), _row("b", "SKIP")], [])
    assert not verdict_ok([_row("a", "PASS"), _row("b", "FAIL")], [])
    # a fatal (exception, QML error, watchdog) fails even an all-PASS walk
    assert not verdict_ok([_row("a", "PASS")], ["uncaught exception"])


def test_summary_reports_counts_net_and_fatals():
    out = format_summary(
        [_row("home feed (gh)", "PASS"), _row("history (gy)", "SKIP"),
         _row("submit search (Enter)", "FAIL", network=True)],
        ["watchdog: run exceeded 180s"], 42.5)
    assert "1 passed, 1 failed, 1 skipped in 42.5s" in out
    assert "FAIL/net" in out          # network-dependent, not a crash
    assert "degraded run" in out      # a SKIP was present
    assert "fatal: watchdog" in out
    assert out.rstrip().endswith("verdict: FAIL")
    # every step name survives into the table
    for name in ("home feed (gh)", "history (gy)", "submit search (Enter)"):
        assert name in out

    clean = format_summary([_row("grid nav", "PASS")], [], 1.0)
    assert clean.rstrip().endswith("verdict: PASS")
    assert "FAIL/net" not in clean and "degraded run" not in clean


def test_position_checks_track_the_mark():
    """_pos_changed only passes on real movement, and rebaselines when it
    does -- otherwise one seek would satisfy every later seek step."""
    class FakeSoak:
        def __init__(self, pos):
            self.marks, self._pos = {}, pos

        def probe(self):
            return "WATCH", 0, self._pos, False

    s = FakeSoak(10.0)
    check = soak._pos_changed(4.0)
    assert check(s)                 # no mark yet: any valid position passes
    assert s.marks["pos"] == 10.0
    assert not check(s)             # unchanged position: no movement
    s._pos = 12.0
    assert not check(s)             # drift below the threshold
    s._pos = 15.0
    assert check(s) and s.marks["pos"] == 15.0
    s._pos = 10.0                   # a backwards seek counts too
    assert check(s)

    started = soak._started
    s2 = FakeSoak(-1.0)             # no player yet
    assert not started(s2)
    s2._pos = 0.0
    assert started(s2) and s2.marks["pos"] == 0.0


def test_paginates_drives_growth_dedup_and_token_advance():
    """_paginates passes after N grown pages (or clean exhaustion), keeps
    polling while a page is in flight, and refuses duplicate ids or a stuck
    token -- offline: the feed is faked, no tasks, no network."""
    from types import SimpleNamespace

    def vid(i, duration=""):
        return SimpleNamespace(video_id=f"v{i:010d}", duration=duration)

    class FakeFeed:
        def __init__(self):
            self.loadingMore = False
            self._more = ("TOK1", None)
            self._videos = [vid(1), vid(2)]
            self.requested = 0

        def loadMore(self):
            self.requested += 1
            self.loadingMore = True

    class FakeSoak:
        def __init__(self):
            self.feed = FakeFeed()
            self.lines = []

        def log(self, msg):
            self.lines.append(msg)

    s = FakeSoak()
    check = soak._paginates(2)
    assert not check(s)                     # baseline + page 1 requested
    assert s.feed.requested == 1 and s.feed.loadingMore
    assert not check(s)                     # in flight: just polls
    s.feed._videos += [vid(3, "SHORT")]     # page 1 lands, token advances
    s.feed._more, s.feed.loadingMore = ("TOK2", None), False
    assert not check(s)                     # 1 page done, page 2 requested
    assert s.feed.requested == 2
    s.feed._videos += [vid(4)]              # page 2 lands, feed exhausted
    s.feed._more, s.feed.loadingMore = None, False
    assert check(s)                         # target reached: pass
    assert any("2 pages" in l and "1 shorts" in l for l in s.lines)

    # Clean exhaustion before the target also passes (a short live feed).
    s2 = FakeSoak()
    s2.feed._more = None
    assert soak._paginates(2)(s2)
    assert any("pages exhausted" in l for l in s2.lines)

    # Duplicate ids never pass, and say why.
    s3 = FakeSoak()
    s3.feed._videos = [vid(1), vid(1)]
    assert not soak._paginates(1)(s3)
    assert any("duplicate" in l for l in s3.lines)

    # A grown page whose token did not advance never passes.
    s4 = FakeSoak()
    check4 = soak._paginates(2)
    assert not check4(s4)                   # baseline, page requested
    s4.feed._videos += [vid(9)]
    s4.feed.loadingMore = False             # page landed, token unchanged
    assert not check4(s4)
    assert any("did not advance" in l for l in s4.lines)


def test_degraded_environment_skips_instead_of_failing():
    """No account / empty feed / no player must SKIP the steps that depend on
    them -- the walk still presses the keys, so navigation and quit are
    exercised on a logged-out or offline machine."""
    class FakeSoak:
        loggedIn, rows, mode = True, 10, "WATCH"
        feed = tabs = auth = None

        def __init__(self, **kw):
            self.__dict__.update(kw)
            self.auth = type("A", (), {"loggedIn": self.loggedIn})()
            self.feed = type("F", (), {"rowCount": lambda _s: self.rows})()

        def probe(self):
            return self.mode, 0, 0.0, False

    reason = soak.Soak._skip_reason
    needs_auth = Step("gh", auth=True)
    needs_content = Step("play", content=True)
    needs_watch = Step("pause", watch=True)
    plain = Step("grid nav")

    good = FakeSoak()
    assert all(reason(good, s) == "" for s in
               (needs_auth, needs_content, needs_watch, plain))
    assert "no account" in reason(FakeSoak(loggedIn=False), needs_auth)
    assert "empty feed" in reason(FakeSoak(rows=0), needs_content)
    assert "no live playback" in reason(FakeSoak(mode="BROWSE"), needs_watch)
    # an ungated step is never skipped, whatever the environment
    assert reason(FakeSoak(loggedIn=False, rows=0, mode="BROWSE"), plain) == ""


def _harness(steps, mode="BROWSE", rows=3, logged_in=True):
    """A Soak with the Qt edges stubbed out: __init__ only stores its
    collaborators, so the walk runs with no window and no engine."""
    class H(soak.Soak):
        posted: list = []

        def post(self, keys):
            self.posted.extend(keys)

        def probe(self):
            return mode, 0, 0.0, False

        def log(self, msg):
            pass

    stub = type("Stub", (), {"rowCount": lambda _s: rows, "activeIndex": 0,
                             "loggedIn": logged_in})()
    h = H(None, None, stub, stub, stub, steps=steps)
    h.posted = []
    return h


def test_walk_sequences_steps_and_records_every_outcome():
    steps = (
        Step("nav", ("j",), delay=0.0, check=soak._mode("BROWSE")),
        Step("broken", ("k",), delay=0.0, timeout=0.1, check=lambda s: False),
        Step("gated", ("x",), delay=0.0, watch=True, check=lambda s: False),
        Step(QUIT_STEP, ("q",), delay=0.0, timeout=0.05),
    )
    h = _harness(steps)
    asyncio.run(h._walk())
    # skipped steps still press their keys: the chord is exercised, only the
    # state assertion is meaningless
    assert h.posted == ["j", "k", "x", "q"]
    assert [(r[0], r[1]) for r in h.results] == [
        ("nav", "PASS"), ("broken", "FAIL"), ("gated", "SKIP"),
        (QUIT_STEP, "FAIL")]
    # q that does not quit is a failure, not a hang
    assert "still running" in h.results[-1][2]
    assert not verdict_ok(h.results, h.fatal)


def test_watchdog_fails_a_hung_step():
    h = _harness((Step("hangs", ("j",), delay=0.0, timeout=60.0,
                       check=lambda s: False),))

    async def run():
        try:
            await asyncio.wait_for(h._walk(), 0.15)
        except asyncio.TimeoutError:
            h._abort("watchdog: run exceeded 0s (hung at: %s)" % h._pending)

    asyncio.run(run())
    assert h._pending == "hangs"      # the hung step is named in the report
    assert h.fatal and "watchdog" in h.fatal[0]
    assert not verdict_ok(h.results, h.fatal)


def test_step_defaults_are_inert():
    s = Step("bare")
    assert s.keys == () and s.check is None
    assert not (s.network or s.auth or s.content or s.watch or s.repeat)


if __name__ == "__main__":
    test_scan_table_matches_main_qml()
    test_named_keys_are_outside_the_table()
    test_expand_positional_named_and_typed()
    test_every_step_expands_and_quit_is_last()
    test_qml_errors_fail_but_warnings_do_not()
    test_verdict_skips_do_not_fail_the_run()
    test_summary_reports_counts_net_and_fatals()
    test_position_checks_track_the_mark()
    test_paginates_drives_growth_dedup_and_token_advance()
    test_degraded_environment_skips_instead_of_failing()
    test_walk_sequences_steps_and_records_every_outcome()
    test_watchdog_fails_a_hung_step()
    test_step_defaults_are_inert()
    print("all checks passed")
