---
name: update-visualization
description: How to change one of the desktop's existing visualizations (a widget panel, VECTOR's console or hologram, the gallery, a live-layer deck or the background) safely through the build loop — find the code, change the least, keep its data honest, check before and after renders.
when_to_use: "The user asks to change, fix, restyle or extend an existing panel, gauge, hologram, deck, the background or VECTOR's own console."
---

# Changing an existing visualization

## Find it

| What the user sees | Where it lives | Render offscreen |
|--------------------|-------------------------------|------------------|
| SYSTEM, NETWORK, STORAGE, LAB, WORKBENCH, SHORTCUTS panels | bromigOS `widgets/panels.py` (+ `draw.py`, `sources.py`) | `widgets/bromigos-widgets render <name> out.png` |
| A widget plugin | `~/.config/bromigos/plugins/widgets/<name>.py` (or an extension's) | same, with the file path |
| VECTOR's console and construct | VECTOR's `holo/vector/scene.py`, `avatar.py` | `tools/offscreen.py vector out.png` (in VECTOR's tree) |
| The gallery (SUPER+O), models | VECTOR's `holo/gallery.py`, `stage.py`, `render.py`; models: the hologram collections (`~/.config/bromigos/vector/holo/` the user's, `/usr/share/bromigos/{homelab,vector}/holo/`; tools `tools/holo/`) | `tools/offscreen.py gallery out.png <model>` |
| Background, decks (SUPER+H, Mind, Ops, …) | bromigOS `live/live/` (installed: `/usr/lib/bromigos/live/live/`) | `/usr/lib/bromigos/live/tools/offscreen.py <kind> out.png` |
| A background shader layer | `~/.config/bromigos/plugins/live/layers/` (or an extension's) | `python3 -m live.plugins test-layer` |
| The login screen (SDDM, before anyone signs in) | bromigOS `settings/sddm/bromigos/` (QML; package `bromigos-settings`) | `sddm-greeter-qt6 --test-mode` offscreen with `testShots` set (its README) |
| An element of the live wallpaper (meters, scope, relay beam, rain, …) | bromigOS `live/docs/ELEMENTS.md` names its code, uniforms and config key | `/usr/lib/bromigos/live/tools/offscreen.py bg out.png 3` |

Ask `knowledge_search` when a knowledge base is configured, else `docs_search`; bromigOS's
docs index is its `docs/README.md`.

## Change it

1. `build_begin`, then render it **before** you change anything, and look (so you can
   compare).
2. Read the code around what you change; reuse its helpers and patterns.
3. Change the least that does the job. Keep every reading real; keep or add the hover
   regions; palette tokens only.
4. `build_validate` (it renders the changed widget, layer or model and looks); for a core
   file it doesn't render, run the offscreen renderer yourself with `build_run` and
   `look` at the PNG. Compare with the before render.
5. `build_apply` with a clear message ("Updated: the NETWORK panel's latency radar shows
   jitter").

Core files are restarted for the trial (widgets reload, the live layer restarts, a change
to VECTOR's own console restarts him: say so in `say`). The trial watcher rolls back if a
host crashes, errors repeat, or nobody keeps it in 10 minutes.

## Worked example: the den's left meter shows RAM instead of CPU

1. Look it up: `docs_read /usr/share/doc/bromigos-live/ELEMENTS.md` (bromigOS `live/docs/ELEMENTS.md`) ("Meters"):
   the left meter is `meter0`, fed by `cpu` in `Background.render()` (`live/scene.py`);
   memory is already in the snapshot as `mem` (percent, psutil, every 1 s).
2. `build_begin`; render before: `tools/offscreen.py bg /tmp/before.png 3`, `look` at the
   desk crop.
3. The change is one word in `live/scene.py`:
   `for i, k in enumerate(("cpu", "gpu"))` becomes `for i, k in enumerate(("mem", "gpu"))`.
   The needle, easing and shader stay as they are.
4. Update the "Meters" row and section of `docs/ELEMENTS.md` (left = memory in use).
5. Render after, compare, `build_apply` "Updated: the den's left meter shows memory
   instead of CPU". The trial restarts the live layer.

The answer to "what does X on my wallpaper mean?" is the element's "Shows" line in
`docs/ELEMENTS.md`, said plainly with its current state if you can read it (for the
relay beam: whether the lab is all green right now).

## The login screen ships with a package

The SDDM theme (bromigOS `settings/sddm/bromigos/`, package `bromigos-settings`) is the one
visualization you can change but not apply: it reaches a machine with the package. Edit the
QML in a bromigOS checkout, run the greeter offscreen in test mode at 2560×1440 and
1920×1080 as its README shows, `look` at every shot, make sure it prints no QML warnings,
and commit; the next package update and login show it. Keep its rules: no network and no
readings (it runs as the `sddm` user before sign-in), no account name, callsign or
likeness on screen, and change the lock screens (bromigOS `desktop/gtklock/`,
`desktop/hypr/hyprlock.conf`) with it when the look changes.

You can't change VECTOR's safety code (the terminal's limits, tools, the brain's wiring,
the build loop, the plugin isolation); the build loop refuses it.
