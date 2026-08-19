#!/usr/bin/env bash
# One runnable check for the dev shell. Every item here is something that fails
# silently in production: hardware decode degrades to software without an error,
# and a missing binding only surfaces at the first import.
set -euo pipefail

fail=0
ok()   { printf '  ok    %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; fail=1; }
warn() { printf '  warn  %s\n' "$1"; }

if vainfo 2>/dev/null | grep -q 'VAProfileAV1Profile0.*VAEntrypointVLD'; then
  ok "AV1 hardware decode exposed by VA-API"
else
  bad "AV1 absent -- check hardware.graphics.extraPackages / LIBVA_DRIVERS_PATH"
fi

if vainfo 2>/dev/null | grep -q 'VAProfileVP9Profile2.*VAEntrypointVLD'; then
  ok "VP9 profile 2 (10-bit) hardware decode"
else
  bad "VP9 profile 2 absent -- HDR and 8K ladders will fall back to software"
fi

if python -c '
import PySide6.QtQuick, yt_dlp, gpsoauth, keyring, httpx, h2, qasync
' 2>/dev/null; then
  ok "python imports (PySide6.QtQuick, yt_dlp, gpsoauth, keyring, httpx, h2, qasync)"
else
  bad "python imports -- rerun to see the traceback:"
  python -c 'import PySide6.QtQuick, yt_dlp, gpsoauth, keyring, httpx, h2, qasync' || true
fi

if pkg-config --exists mpv; then
  ok "libmpv headers visible to pkg-config ($(pkg-config --modversion mpv))"
else
  bad "libmpv headers missing -- the C++ QQuickItem bridge will not compile"
fi

if python -c '
import keyring
keyring.get_keyring().get_password("mono_player", "__probe__")
' 2>/dev/null; then
  ok "Secret Service reachable (keyring backend works)"
else
  bad "Secret Service missing -- gnome-keyring-daemon is not running"
fi

if command -v node >/dev/null &&
   [ -f "$(python -c 'import sys; print(sys.prefix)')/share/bgutil-ytdlp-pot-provider/build/main.js" ] &&
   python -c 'import yt_dlp_plugins.extractor.getpot_bgutil_http' 2>/dev/null; then
  ok "PO token provider (bgutil plugin + node server) present"
else
  bad "PO token provider missing -- googlevideo will 403 intermittently"
fi

if [ "${XDG_SESSION_TYPE:-}" = wayland ]; then
  ok "Wayland session"
else
  bad "not a Wayland session (XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-unset})"
fi

# Stale yt-dlp pins fail loud but far from the cause: every load 403s with
# nothing pointing at flake.nix. Offline check -- age of the dated marker only.
pin_date=$(grep -oP 'pinned-nightly-date: \K[0-9-]+' "$(dirname "$0")/flake.nix" || true)
if [ -n "$pin_date" ]; then
  age_days=$(( ($(date +%s) - $(date -d "$pin_date" +%s)) / 86400 ))
  if [ "$age_days" -le 30 ]; then
    ok "yt-dlp nightly pin ${age_days}d old"
  else
    warn "yt-dlp nightly pin ${age_days}d old -- if loads 403, re-point rev+hash in flake.nix first"
  fi
else
  warn "pinned-nightly-date marker missing from flake.nix"
fi

exit "$fail"
