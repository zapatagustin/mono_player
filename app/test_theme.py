"""Checks for the theme token loader: stylix palette.json in, semantic
'#rrggbb' tokens out; anything missing or malformed degrades to the
gruvbox-dark-medium fallback, never raises."""

import json
import tempfile
from pathlib import Path

from theme import load_tokens


def test_load_tokens():
    with tempfile.TemporaryDirectory() as tmp:
        palette = Path(tmp) / "palette.json"

        # Missing file -> full gruvbox-dark-medium fallback.
        t = load_tokens(palette)
        assert t["bg"] == "#282828"
        assert t["fg"] == "#ebdbb2"
        assert t["accent"] == "#fabd2f"
        assert t["accentFg"] == "#282828"
        assert t["accent2"] == "#fe8019"

        # A light palette flips bg/fg AND accentFg (text on accent must be
        # the theme's own bg, not hardcoded dark).
        palette.write_text(json.dumps({
            "base00": "fbf1c7", "base06": "3c3836", "base0A": "d79921",
            "author": "ignored", "scheme": "ignored",
        }))
        t = load_tokens(palette)
        assert t["bg"] == "#fbf1c7"
        assert t["fg"] == "#3c3836"
        assert t["accent"] == "#d79921"
        assert t["accentFg"] == "#fbf1c7"
        # Slots absent from the file keep fallback values.
        assert t["accent2"] == "#fe8019"

        # Malformed values are ignored per-slot; malformed JSON entirely.
        palette.write_text(json.dumps({"base00": "not-hex", "base06": "112233"}))
        t = load_tokens(palette)
        assert t["bg"] == "#282828"
        assert t["fg"] == "#112233"
        palette.write_text("{ not json")
        assert load_tokens(palette)["bg"] == "#282828"
    print("theme tokens: ok")


if __name__ == "__main__":
    test_load_tokens()
    print("all checks passed")
