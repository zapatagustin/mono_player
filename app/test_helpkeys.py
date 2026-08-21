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


def _letter_cases(src):
    """Single letters a key handler switches on: `case Qt.Key_X:` labels
    plus `key === Qt.Key_X` comparisons (tabKey style)."""
    return set(re.findall(r"(?:case|key ===) Qt\.Key_([A-Z])\b", src))


def help_bind_letters():
    """Letters named by HelpOverlay's static Bind lines. Chord tokens
    like "gc" contribute their second letter; non-letter tokens (esc,
    enter, space, 1-9, ?) are dropped. The Repeater's computed
    `k: "g" + ...` line is skipped via the (?!\\s*\\+) guard."""
    letters = set()
    for k in re.findall(r'k:\s*"([^"]+)"(?!\s*\+)', HELP):
        for tok in k.replace("/", " ").split():
            if len(tok) == 1 and tok.isalpha():
                letters.add(tok.upper())
            elif len(tok) == 2 and tok[0] == "g" and tok[1].isalpha():
                letters.add(tok[1].upper())
    return letters


def test_help_lists_every_dispatched_letter():
    """Every letter key some dispatcher acts on must appear in a Bind
    line, and every Bind letter must have a dispatcher. gKey's body is
    excluded: its chords are guarded against gChords above, and the
    overlay renders them from that same table.
    ecomono: letter-set equality across ALL handlers, not per-section --
    a key documented under the wrong Group passes. Upgrade: map prose
    Groups to handler regions if a misplaced entry ever bites."""
    fn = re.search(r"function gKey\(event\) \{.*?\n    \}", QML, re.S)
    dispatched = _letter_cases(QML.replace(fn.group(0), ""))
    listed = help_bind_letters()
    assert dispatched == listed, (
        f"undocumented: {sorted(dispatched - listed)}, "
        f"stale: {sorted(listed - dispatched)}")


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
