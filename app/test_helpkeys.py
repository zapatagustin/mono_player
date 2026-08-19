"""Checks that the which-key popup (and HelpOverlay, fed by the same table)
lists every g-chord the dispatcher handles: root.gChords in Main.qml must
cover exactly the keys gKey()'s pending branch switches on. Pure regex over
the QML source -- no window, no compositor."""

import re
from pathlib import Path

QML = (Path(__file__).resolve().parent.parent / "qml" / "Main.qml").read_text()


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


if __name__ == "__main__":
    test_which_key_matches_dispatcher()
    print("all checks passed")
