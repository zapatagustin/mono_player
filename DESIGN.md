# DESIGN — a terminal program that happens to play video

mono_player must read as a TUI: flat, monospace, gruvbox, driven entirely from the
keyboard with vim grammar. The reference implementation of this aesthetic is the
owner's NixOS setup (`~/personal/nixos-config` — Hyprland + custom Quickshell bar);
every value below is lifted from it, not invented. When this doc and that config
disagree, the config wins.

## Philosophy

- **Terminal-native, not terminal-themed.** No skeuomorphism, no fake scanlines —
  the app looks like a TUI because it follows TUI constraints: one monospace font,
  16 colors, box-flat panels, visible focus, instant state changes.
- **Keyboard is the interface.** Every action reachable without the mouse. The mouse
  works but is never required and never exclusive (matches Hyprland config: mouse is
  modifier-gated, hjkl is first-class).
- **Zero decoration.** No rounded corners, no shadows, no blur, no animations. State
  changes are instant. (Hyprland: `rounding=0`, `shadow=false`, `blur=false`,
  `animations.enabled=false` — "enabled = no, please :)".)
- **Own the exact pixel.** Hand-tuned values over framework defaults, and every
  non-obvious decision justified in a comment — the same culture as the config.

## Palette — gruvbox-dark-medium (base16)

Default theme. Semantic tokens mirror the Quickshell bar's theme object
(`quickshell/bar/shell.qml`) so both UIs read as one system.

| Token | base16 | Hex | Use |
|---|---|---|---|
| `bg` | base00 | `#282828` | window background |
| `bg1` | base01 | `#3c3836` | inactive surfaces, inactive borders |
| `bg2` | base02 | `#504945` | panels, separators, borders, occupied-but-inactive |
| `fgDim` | base04 | `#bdae93` | secondary text (channel, duration, hints) |
| `fg` | base06 | `#ebdbb2` | primary text |
| `accent` | base0A | `#fabd2f` | selection, active tab, focus — always paired with `accentFg` |
| `accentFg` | base00 | `#282828` | text on accent |
| `accent2` | base09 | `#fe8019` | active gradient end (with `accent`, 45°) |
| `red` | base08 | `#fb4934` | errors only |
| `green` | base0B | `#b8bb26` | success/positive only |
| `emptyDim` | base03 | `#665c54` | disabled, empty-state text |

Full scheme for anything not covered: base05 `#d5c4a1`, base07 `#fbf1c7`,
base0C `#8ec07c`, base0D `#83a598`, base0E `#d3869b`, base0F `#d65d0e`.

**Signature active marker:** the `#fabd2f → #fe8019` 45° gradient is the system-wide
"this is active" mark (Hyprland active border). Use it as the 1px border of the
playing/selected surface; use solid `accent` fill + `accentFg` text for the selected
item itself (like the bar's active workspace).

**Theme plumbing:** read stylix's generated `palette.json` and watch
`$XDG_RUNTIME_DIR/qs-theme` for the live dark/light toggle — the same mechanism the
bar uses. Hardcode gruvbox-dark-medium as the fallback when the file is absent
(non-NixOS or fresh install). Light mode is gruvbox-light-medium, arriving through
the same tokens; never branch on "dark vs light" in component code, only on tokens.

## Typography

| Role | Font | Size |
|---|---|---|
| Everything | Terminess Nerd Font Mono | 12px body, 11px secondary, 13px emphasis |
| Icons/glyphs | Symbols Nerd Font | inline with text size |

One font. No proportional text anywhere — a TUI has one grid. Sizes match the bar
(11–13px). No bold-as-hierarchy; hierarchy comes from color (`fg` vs `fgDim`) and
position, like a statusline.

## Chrome

| Property | Value |
|---|---|
| Corner radius | 0 everywhere |
| Borders | 1px `bg2`; active surface 1px `accent→accent2` gradient |
| Shadows / blur | none |
| Animations | none — instant show/hide (the current 200ms overlay fade must go) |
| Gaps / padding | tight: 3px inner, 6px outer (Hyprland `gaps_in/out`) |
| Bar heights | 28px (tab bar and statusline; matches the Quickshell bar) |
| Scrollbars | none visible — position lives in the statusline (`12/40`) |

## Keyboard model — vim grammar

hjkl are first-class; arrows are an alias, never the reverse. Single keys, no
chords, no modifier for app-level actions. `Esc` always steps back one level.

### Browse (normal mode)

| Key | Action |
|---|---|
| `h j k l` / arrows | move selection in the grid |
| `gg` / `G` | first / last item |
| `Enter` | play in active tab (fresh queue) |
| `t` | open in new tab (background, like a browser) |
| `p` | play next (paste after current — vim `p`) |
| `a` | append to queue (enqueue) |
| `w` | watch later |
| `/` | focus search prompt |
| `1`–`9` | switch to tab N |
| `gt` / `gT` | next / previous tab |
| `gs` / `gw` | subscriptions / watch-later feed (login required) |
| `gl` | sign in (email prompt, then webview) — or sign out when logged in |
| `x` | close active tab |
| `q` | quit |

### Watch (player mode)

| Key | Action |
|---|---|
| `Space` | pause / resume |
| `h` / `l` (and ←/→) | seek −5s / +5s |
| `j` / `k` (and ↓/↑) | volume −5 / +5 |
| `m` | mute |
| `s` | cycle subtitle track (statusline shows the language) |
| `f` | fullscreen |
| `1`–`9`, `gt`/`gT`, `x` | tab actions, same as browse |
| `Esc` | back to browse (tab keeps playing) |

### Prompt (search)

| Key | Action |
|---|---|
| `Enter` | run search, return to normal |
| `Esc` | cancel, return to normal |

Every view shows its mode and available keys in the statusline — recognition over
recall, the TUI way. Mouse equivalents (click to play, right-click menu, seek-bar
click) remain but never gate a feature.

## Components

**Tab bar (top, 28px).** DWM-style strip. Each tab: `[一] title…` — Japanese
numeral index (the bar's workspace glyphs: 一二三四五六七八九) + elided title.
Active tab: `accent` fill, `accentFg` text. Inactive: `bg1` fill, `fgDim` text,
1px `bg2` border. No close buttons — `x` closes; a mouse middle-click may too.

**Grid (browse).** Fixed cells, 1px `bg2` separation, no card chrome. Selected
cell: 1px `accent→accent2` gradient border. Thumbnail top; below it title in `fg`
(2 lines max) and `channel · duration` in `fgDim` (1 line). No hover effects —
selection is keyboard state, not pointer state.

**Search prompt.** dmenu-style strip (like the config's Launcher): 28px, `bg`
fill, `/` prompt glyph in `accent`, query text in `fg`. Lives at the top, replaces
nothing, never floats.

**Statusline (bottom, 28px, always visible).** Replaces the auto-hide overlay —
a TUI never hides its statusline. Left: mode tag (`BROWSE` / `WATCH` in `accent`).
Center: current video title (`fg`, elided). Right, watch mode: textual progress
`12:34 / 45:06`, a flat `accent`-on-`bg2` progress bar (1-cell tall, no handle),
volume `vol 85`, decode tag (`vaapi` in `green`, `sw` in `red`). Segments split by
1px `bg2` separators, like the bar.

**Login webview.** Exception by necessity: Google's page is Google's. Frame it
with app chrome (statusline hint: `Esc cancel`), theme nothing inside it.

## Out of scope

- Implementation plan and migration of the current QML (this doc is the target,
  not the diff).
- The `:` command line, marks, registers — vim grammar beyond the table is YAGNI
  until a real need shows up.
- Custom light-mode values: light arrives via tokens from the same theme pipe.
- zsh-style emacs bindings: the config's shell uses `bindkey -e`, but this app
  mirrors the WM/editor layer (vim), not the shell layer.
