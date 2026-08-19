"""Checks that the which-key popup (and HelpOverlay, fed by the same table)
lists every g-chord the dispatcher handles: root.gChords in Main.qml must
cover exactly the keys gKey()'s pending branch switches on. Pure regex over
the QML source -- no window, no compositor."""

import re
from pathlib import Path

_QML_DIR = Path(__file__).resolve().parent.parent / "qml"
QML = (_QML_DIR / "Main.qml").read_text()
HELP = (_QML_DIR / "HelpOverlay.qml").read_text()


def dispatched_keys():
    """Letters gKey() acts on, from its `case Qt.Key_X:` labels."""
    fn = re.search(r"function gKey\(event\) \{.*?\n    \}", QML, re.S)
    assert fn, "gKey() not found in Main.qml"
    keys = set(re.findall(r"case Qt\.Key_(\w+):", fn.group(0)))
    assert keys, "no case labels parsed from gKey()"
    return keys


def listed_keys():
    """Letters offered by the which-key table: [["t/T", "..."], ...]."""
    block = re.search(r"property var gChords: \[(.*?)\n    \]", QML, re.S)
    assert block, "gChords table not found in Main.qml"
    entries = re.findall(r'\["([^"]+)",\s*"[^"]*"\]', block.group(1))
    assert entries, "no entries parsed from gChords"
    # "t/T" is one chord key with a shift variant: one letter, uppercased.
    return {e.split("/")[0].upper() for e in entries}


def test_which_key_matches_dispatcher():
    assert listed_keys() == dispatched_keys()


def test_help_close_scancodes_match_scan_table():
    """HelpOverlay hardcodes the positional close keys (q, /) as scan
    codes; they must stay the codes Main.qml's scanKey maps to those keys."""
    table = re.search(r"property var scanKey: \(\{(.*?)\}\)", QML, re.S)
    assert table, "scanKey table not found in Main.qml"
    scan = {k: int(c) for c, k in
            re.findall(r"(\d+): Qt\.Key_(\w+)", table.group(1))}
    q = re.search(r"pos === (\d+)\n", HELP)  # the q check
    slash = re.search(r"pos === (\d+) && \(event\.modifiers", HELP)
    assert q and int(q.group(1)) == scan["Q"]
    assert slash and int(slash.group(1)) == scan["Slash"]


if __name__ == "__main__":
    test_which_key_matches_dispatcher()
    test_help_close_scancodes_match_scan_table()
    print("all checks passed")
