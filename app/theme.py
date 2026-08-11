"""Theme tokens for the TUI look (DESIGN.md). Reads stylix's palette.json,
mirrors the Quickshell bar's semantic token map, falls back to
gruvbox-dark-medium, and follows the live dark/light toggle via qs-theme."""

import json
import os
import re
from pathlib import Path

from PySide6.QtCore import Property, QFileSystemWatcher, QObject, Signal

GRUVBOX_DARK_MEDIUM = {
    "base00": "282828", "base01": "3c3836", "base02": "504945",
    "base03": "665c54", "base04": "bdae93", "base05": "d5c4a1",
    "base06": "ebdbb2", "base07": "fbf1c7", "base08": "fb4934",
    "base09": "fe8019", "base0A": "fabd2f", "base0B": "b8bb26",
    "base0C": "8ec07c", "base0D": "83a598", "base0E": "d3869b",
    "base0F": "d65d0e",
}

# Semantic token -> base16 slot, same mapping as the Quickshell bar's theme
# object so both UIs read as one system (DESIGN.md, Palette).
SEMANTIC = {
    "bg": "base00", "bg1": "base01", "bg2": "base02",
    "fgDim": "base04", "fg": "base06",
    "accent": "base0A", "accentFg": "base00", "accent2": "base09",
    "red": "base08", "green": "base0B", "emptyDim": "base03",
}

_HEX = re.compile(r"[0-9a-fA-F]{6}")

PALETTE_PATH = Path.home() / ".config/stylix/palette.json"
QS_THEME_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "qs-theme"


def load_tokens(palette_path: Path) -> dict[str, str]:
    """Semantic token -> '#rrggbb'. Missing file, bad JSON or bad slot
    values degrade per-slot to the gruvbox-dark-medium fallback."""
    base = dict(GRUVBOX_DARK_MEDIUM)
    try:
        data = json.loads(palette_path.read_text())
        if isinstance(data, dict):
            for key, value in data.items():
                if key in base and isinstance(value, str) and _HEX.fullmatch(value):
                    base[key] = value
    except (OSError, ValueError):
        pass
    return {token: "#" + base[slot] for token, slot in SEMANTIC.items()}


class Theme(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tokens = load_tokens(PALETTE_PATH)
        # set-theme touches qs-theme AFTER relinking palette.json, so
        # re-reading on this signal is race-free (same contract as the bar).
        self._watcher = QFileSystemWatcher(self)
        if QS_THEME_PATH.exists():
            self._watcher.addPath(str(QS_THEME_PATH))
            self._watcher.fileChanged.connect(self._reload)

    def _reload(self, path: str):
        self._tokens = load_tokens(PALETTE_PATH)
        self.changed.emit()
        # A replaced (rather than rewritten) file drops the watch; re-add.
        if path not in self._watcher.files() and Path(path).exists():
            self._watcher.addPath(path)

    # Properties must live in the class body or the metaobject loses their
    # NOTIFY signal and QML bindings go stale ("non-bindable" warnings).
    bg = Property(str, lambda s: s._tokens["bg"], notify=changed)
    bg1 = Property(str, lambda s: s._tokens["bg1"], notify=changed)
    bg2 = Property(str, lambda s: s._tokens["bg2"], notify=changed)
    fg = Property(str, lambda s: s._tokens["fg"], notify=changed)
    fgDim = Property(str, lambda s: s._tokens["fgDim"], notify=changed)
    accent = Property(str, lambda s: s._tokens["accent"], notify=changed)
    accentFg = Property(str, lambda s: s._tokens["accentFg"], notify=changed)
    accent2 = Property(str, lambda s: s._tokens["accent2"], notify=changed)
    red = Property(str, lambda s: s._tokens["red"], notify=changed)
    green = Property(str, lambda s: s._tokens["green"], notify=changed)
    emptyDim = Property(str, lambda s: s._tokens["emptyDim"], notify=changed)

    # Typography constants (DESIGN.md): one mono font, bar-matching sizes.
    fontFamily = Property(str, lambda s: "Terminess Nerd Font Mono", constant=True)
    fontSize = Property(int, lambda s: 12, constant=True)
    fontSizeSmall = Property(int, lambda s: 11, constant=True)
    barHeight = Property(int, lambda s: 28, constant=True)
