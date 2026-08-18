"""Checks for the positional-keybinding table in Main.qml: every entry
must map the X keycode (evdev + 8) of a physical QWERTY position to that
position's Qt.Key, and posKey must fall back to event.key for unmapped
scancodes. Runs the QML file's actual scanKey/posKey source in QJSEngine
against simulated dvorak events — no window, no compositor."""

import re
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtQml import QJSEngine

# QJSEngine aborts without an application object
_app = QCoreApplication.instance() or QCoreApplication([])

QML = (Path(__file__).resolve().parent.parent / "qml" / "Main.qml").read_text()

# evdev keycodes from linux/input-event-codes.h (frozen kernel ABI);
# X/Wayland nativeScanCode = evdev + 8.
EVDEV = {
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9,
    "9": 10, "0": 11,
    "Q": 16, "W": 17, "E": 18, "R": 19, "T": 20, "Y": 21, "U": 22,
    "I": 23, "O": 24, "P": 25,
    "A": 30, "S": 31, "D": 32, "F": 33, "G": 34, "H": 35, "J": 36,
    "K": 37, "L": 38,
    "Z": 44, "X": 45, "C": 46, "V": 47, "B": 48, "N": 49, "M": 50,
    "Slash": 53,
}


def parse_table():
    block = re.search(r"scanKey: \(\{(.*?)\}\)", QML, re.S).group(1)
    pairs = re.findall(r"(\d+): Qt\.Key_(\w+)", block)
    return {int(code): name for code, name in pairs}, block


def test_table_matches_kernel_keycodes():
    table, _ = parse_table()
    expected = {code + 8: name for name, code in EVDEV.items()}
    assert table == expected


def make_engine():
    _, block = parse_table()
    fn = re.search(r"function posKey\(event\) \{.*?\n    \}", QML, re.S)
    qt_stub = "const Qt = {%s};" % ",".join(
        "Key_%s: %d" % (n, getattr(Qt, "Key_" + n).value) for n in EVDEV
    )
    engine = QJSEngine()
    err = engine.evaluate(qt_stub + "const scanKey = ({%s});" % block
                          + fn.group(0))
    assert not err.isError(), err.toString()
    return engine


def poskey(engine, scan, key):
    return engine.evaluate(
        "posKey({nativeScanCode: %d, key: %d})" % (scan, key)).toInt()


def test_dvorak_events_resolve_to_physical_position():
    engine = make_engine()
    # dvorak home row: physical HJKL deliver keysyms d/h/t/n
    dvorak = [("H", Qt.Key_D, Qt.Key_H), ("J", Qt.Key_H, Qt.Key_J),
              ("K", Qt.Key_T, Qt.Key_K), ("L", Qt.Key_N, Qt.Key_L),
              # physical / delivers Z under dvorak
              ("Slash", Qt.Key_Z, Qt.Key_Slash)]
    for pos, delivered, want in dvorak:
        got = poskey(engine, EVDEV[pos] + 8, delivered.value)
        assert got == want.value, (pos, got)


def test_unmapped_scancode_falls_back_to_keysym():
    engine = make_engine()
    # KEY_ENTER = 28 -> X 36, deliberately absent from the table
    assert poskey(engine, 36, Qt.Key_Return.value) == Qt.Key_Return.value
    assert poskey(engine, 9, Qt.Key_Escape.value) == Qt.Key_Escape.value


if __name__ == "__main__":
    test_table_matches_kernel_keycodes()
    test_dvorak_events_resolve_to_physical_position()
    test_unmapped_scancode_falls_back_to_keysym()
    print("all checks passed")
