---
name: hyprland-config
description: "How the user's Hyprland is configured — the Lua config (bromigOS's defaults in /usr/share/bromigos/default/hypr/ plus their own hypr/hyprland.lua), adding or changing a keybind, window rule, layer rule, workspace rule, device or autostart program, checking it, and running dispatchers from scripts (`hyprctl dispatch 'hl.dsp…'`) — and the rollback to the old hyprland.conf."
when_to_use: "Adding, changing or finding a keybind, a window/layer/workspace rule, a monitor, a device (keyboard) setting, an environment variable or a login program; any script or tool that calls `hyprctl dispatch`; when a bind or rule doesn't work or Hyprland shows a config error."
---

# The Hyprland config (Lua)

Hyprland 0.56 runs a **Lua** config. The old `hyprland.conf` format (`bind=`, `windowrule =`,
`exec-once=`, `hyprctl dispatch workspace 3`) is deprecated and is removed in 0.57; the file
stays in the repo only as the rollback. Never add anything to it.

Two layers. bromigOS's defaults (package `bromigos-desktop`, read-only; updates replace
them, so never edit them) and the user's own file, which runs them and then adds its own:

| File | Holds |
|------|-------|
| `~/.config/hypr/hyprland.lua` (the user's; often kept in their dotfiles) | sets the `bromigos` table (`mod`, `monitors`, `workspaces`, `apps`, `config`, `unbind`, `autostart`), `dofile`s the defaults, then their own env, window rules and binds |
| `~/.config/hypr/bromigos/<module>.lua` (the user's) | a module of their own (a keyboard's macro keys, say), `require("bromigos.<module>")`d from their `hyprland.lua` |
| `/usr/share/bromigos/default/hypr/hyprland.lua` | the defaults: monitors and workspaces from the table, autostart, env, look, input, layer and window rules, the theme's colours; requires the four below |
| `/usr/share/bromigos/default/hypr/bromigos/binds.lua` | bromigOS's keybinds |
| `/usr/share/bromigos/default/hypr/bromigos/live.lua` | the live layer's start at login and its binds (its decks) |
| `/usr/share/bromigos/default/hypr/bromigos/vector.lua` | VECTOR's binds, when he's installed |
| `/usr/share/bromigos/default/hypr/bromigos/keys.lua` | the bind helpers `K.exec` / `K.dsp` (keep every bind's action as text for SHORTCUTS) |

A default changes in bromigOS's repo (`desktop/hypr/`) and arrives with a package update.
More: bromigOS `docs/desktop.md`.

Hyprland loads `~/.config/hypr/hyprland.lua` when it exists, else `hyprland.conf`. Saving any
file it loads reloads Hyprland by itself, so a change that spans files (or a stow, merge or
checkout under `~/.config/hypr`) can reload it half-written and trip its emergency mode. For
those: `hyprctl keyword misc:disable_autoreload true` first, make the change, verify it
(below), then one `hyprctl reload`, then check `hyprctl configerrors` and the bind count. API reference: `/usr/share/hypr/stubs/hl.meta.lua`
(every `hl.*` function and option) and https://wiki.hypr.land/Configuring/.
The user's own lines go in their `hyprland.lua` after the `dofile`, each with a comment line saying
what it is.

## Add a keybind

1. Find a free key: `hyprctl binds -j` (or the SHORTCUTS panel, SUPER+K).
2. In `~/.config/hypr/hyprland.lua`, after the defaults (`local K = require("bromigos.keys")` is
   already there), with the helpers. To replace a default bind, list its keys in the
   `bromigos.unbind` table and bind them yourself:
   ```lua
   K.exec("SUPER + Q", "~/.config/bromigos/bin/some-tool --flag")         -- a command (sh -c; ~ and $HOME expand)
   K.dsp("ALT + P", "hl.dsp.window.pin()")                                 -- a dispatcher, written as Lua source
   K.exec("SUPER + Z", "… scan-hold off", { release = true })              -- on key-up (hold binds: a press + a release)
   ```
   Keys are `"MODS + KEY"` with mods SUPER, ALT (the config's `mainMod`), CTRL, SHIFT; the key
   is a keysym (`E`, `RETURN`, `XF86AudioMute`, `mouse:272`) or `code:N` (evdev code + 8).
   Flags (third argument): `release`, `repeating`, `locked`, `mouse`, `device`, … (see `HL.BindOptions`).
   Use the helpers, not a bare `hl.bind`: they put the action in the bind's description, which is
   how SHORTCUTS and the rofi cheat sheet name and run it.
3. An exec bind SHORTCUTS should name: an `EXEC` row (regex, description, section) in
   bromigOS's `widgets/keybinds.py`. A new kind of dispatcher bind: a `DISPATCH` row there.
4. A default bind changes in bromigOS's repository (`desktop/hypr/`), with its `EXEC` row; there
   `tools/docs/generate.py keys` regenerates the manual's key table (CI fails while it's stale).

A keyboard's macro keys bound by keycode (`code:N`) are limited to that keyboard with
`{ device = { list = { "<tag>" } } }` (the tag its `hl.device()` entries set): in 0.56 a Lua
`code:N` bind also fires for keys with no keysym, so an unlimited one could go off from a
stray key on another keyboard.

## Add a rule, device, env or login program (in `~/.config/hypr/hyprland.lua`)

Login programs: the `bromigos.autostart` table (it runs after bromigOS's). Settings: the
`bromigos.config` table, merged over the defaults' `hl.config()`. Anything else, after the
`dofile`:

```lua
hl.window_rule({ match = { class = "^(pavucontrol)$" }, float = true, size = "800 600", center = true })
hl.layer_rule({ match = { namespace = "my-overlay" }, blur = true, ignore_alpha = 0.2 })
hl.workspace_rule({ workspace = "5", monitor = "DP-1" })
hl.device({ name = "some-keyboard", kb_options = "caps:escape" })    -- names: hyprctl devices
hl.env("SOME_VAR", "value")
hl.on("hyprland.start", function() hl.exec_cmd("some-daemon") end)  -- runs once at login, not on reload
hl.config({ general = { gaps_in = 4 } })                              -- any option: see the stubs
```
Window rules match regexes on `class` / `title` (`hyprctl clients` shows them); they are
anonymous and apply top to bottom. Effects that take a size or position are strings (`"25% 40%"`).

## Check it

- Before reloading: `Hyprland --verify-config -c ~/.config/hypr/hyprland.lua`
  (prints `config ok` or each error with its file and line).
- After saving: `hyprctl configerrors` (empty = fine). A mistake in one bind doesn't stop the
  binds after it; a Lua syntax error stops the reload and Hyprland keeps the last good config.
- `hyprctl binds -j | grep -c __lua` (bind count), the SHORTCUTS panel, `bromigos-docs keys --check`.
- Look around live: `hyprctl repl 'return hl.get_active_window().class'`.

## Dispatchers from scripts and tools

`hyprctl dispatch` takes **Lua** now; the old form is an error:

| Old (`.conf` era) | Now |
|-----|-----|
| `hyprctl dispatch exec kitty` | `hyprctl dispatch 'hl.dsp.exec_cmd("kitty")'` |
| `hyprctl dispatch exec [workspace 3 silent] kitty` | `hyprctl dispatch 'hl.dsp.exec_cmd("kitty", { workspace = "3 silent" })'` |
| `hyprctl dispatch workspace 3` | `hyprctl dispatch 'hl.dsp.focus({ workspace = 3 })'` |
| `hyprctl dispatch focuswindow address:0x…` | `hyprctl dispatch 'hl.dsp.focus({ window = "address:0x…" })'` |
| `hyprctl dispatch closewindow address:0x…` | `hyprctl dispatch 'hl.dsp.window.close({ window = "address:0x…" })'` |
| `hyprctl dispatch movetoworkspacesilent 3,address:0x…` | `hyprctl dispatch 'hl.dsp.window.move({ window = "address:0x…", workspace = 3, follow = false })'` |
| `hyprctl dispatch killactive` / `togglefloating` | `'hl.dsp.window.close()'` / `'hl.dsp.window.float()'` |
| `hyprctl dispatch fullscreen 1` | `'hl.dsp.window.fullscreen({ mode = "maximized" })'` |
| `hyprctl dispatch dpms off` | `'hl.dsp.dpms({ action = "off" })'` |
| `hyprctl dispatch exit` | `'hl.dsp.exit()'` |
| `hyprctl keyword general:gaps_in 8` | `hyprctl eval 'hl.config({ general = { gaps_in = 8 } })'` (until the next reload) |

`hyprctl dispatch` answers `ok` on success. Put any outside text (a command, a title) into
the Lua string safely: escape it (Python: `keybinds._lua_str()`), never paste it raw. Scripts
that must also work on the rollback use `~/.config/bromigos/bin/bromigos-dispatch '<Lua>' <old
form…>` (shell), `hypr.dispatch()` (live layer) or `desk._dispatch()` (VECTOR's desk tools).
`hyprctl` queries (`clients -j`, `activewindow -j`, `monitors -j`, `layers -j`) and the event
socket (`workspacev2>>`, `openwindow>>`, …) are unchanged.

## Rollback

`~/.config/bromigos/bin/bromigos-hyprconfig status | lua | conf`. `conf` unlinks
`hyprland.lua` so the next login loads `hyprland.conf`; it never switches a running Hyprland
back (that crashes 0.56.2) — log out and in. `lua` links the Lua config back, checks it and
switches the running Hyprland. The rollback is the user's call; tell them rather than
running it.

Lines in `hypr/` that change how VECTOR's own daemon starts (bromigos-holo) are refused by
his guard; those stay the user's.
