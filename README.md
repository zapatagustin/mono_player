# mono_player

A native Wayland YouTube client: Qt Quick UI over libmpv, styled like a TUI
(gruvbox, one monospace font, vim keys), with **browser-style tabs** over a
pool of players — switching tabs is instant, like a browser, because it is
show/pause, not reload.

The app is glue: mpv decodes (VA-API, zero-copy), yt-dlp extracts (as mpv's
ytdl_hook subprocess, with a managed PO-token provider), sqlite persists,
InnerTube serves the data. Measured against Chromium on the same machine:
~1.5x less CPU, less memory, at 1080p60 hardware decode.

## Run

```sh
nix develop        # NixOS flake: everything from PySide6 to libmpv headers
./check.sh         # asserts hw decode, imports, keyring, PO provider
cmake -B bridge/build bridge && cmake --build bridge/build   # once
python main.py
```

Requirements outside the flake: a Wayland session, VA-API capable hardware,
and — only for account features — a Secret Service daemon (gnome-keyring).
Playback never depends on login.

## Keys (vim grammar; the statusline always shows what applies)

| Browse | |
|---|---|
| `hjkl` / `gg` / `G` | move in the grid |
| `Enter` / `t` / `a` / `p` / `w` | play / new tab / enqueue / play next / watch later |
| `/` | search prompt |
| `gh` `gs` `gy` `gp` `gw` | home · subscriptions · history · playlists · watch later |
| `gc` / `S` | open channel / subscribe |
| `ga` / `gA` | switch acting channel (brand accounts) / switch Google account |
| `gl` / `gL` | sign in–out / add another Google account |
| `1-9` `gt` `gT` `x` | tabs; `Esc` back to the playing video; `q` quit |

| Watch | |
|---|---|
| `Space` `h/l` `j/k` `m` `s` `f` | pause · seek · volume · mute · subtitles · fullscreen |
| `r` / `c` / `b` | related · comments · save-to-playlist panels |
| `L` / `C` / `S` | like · write a comment · subscribe |
| in comments: `Enter` expands replies, `Shift+L` likes a comment | |
| `Esc` | back to browse (tab keeps playing) |

## Account

`gl` signs in with a Google account (embedded login screen, the only place
QtWebEngine runs). The master token lives in the OS keyring — never a file.
Every account action honors the acting channel chosen with `ga`.

## Mini-player / PiP

A Hyprland windowrule, not code:

```
windowrulev2 = float, title:^(mono_player — mini)$
windowrulev2 = pin,   title:^(mono_player — mini)$
```

## More

- `GUIDELINE.org` — architecture, decisions and their reasons, known debts.
- `DESIGN.md` — the TUI design system (palette, typography, key grammar).
- `main.py --stress` — playback/tab-churn stress harness; `MONO_HWDEC` and
  `MONO_QUIT_AFTER` env knobs for diagnosis.

## Note

Like every third-party YouTube client, this violates YouTube's ToS.
Personal use, personal risk.
