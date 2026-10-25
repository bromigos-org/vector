# Holograms

`holo/` is a small hologram system. One renderer draws wireframe models with glowing
shells, scan bands and bloom, on a projection table, with leader-line callouts. It has two
faces:

- **VECTOR's console.** His construct on its emitter, a table beside it for a model of
  whatever he's talking about, and the conversation.
- **The gallery.** Every model on the machine, shelf by shelf.

Both run in one daemon (`holo/app.py`), as layer-shell windows on top of the desktop.
Hidden windows draw nothing.

## The gallery

Open it with Super+O, or `bromigos-holo gallery`.

| Input | What it does |
|---|---|
| Drag | Turns the model |
| Scroll, Space, ↑/↓ | Explodes the model into its parts, or puts it back |
| Click a part | Isolates that part |
| ←/→ | Previous or next model |
| PgUp/PgDn | Previous or next world |
| 1–9 | A model on the current shelf |
| S | Runs a scan |
| R | Resets the view |
| Esc | Closes the gallery |

A part bound to a live reading shows it in a callout, such as a GPU's temperature. A part
bound to nothing only shapes its model, and is drawn lit.

The strip along the bottom names every world in its own colour. Hover a world to see what
it holds, and click it to go there. The strip also lists the models on the current shelf.

## Collections and shelves

Models are `.holo.npz` files in collections. When two collections hold a model of the same
name, the first one wins (`holo/fmt.py`).

| Collection | Shelf |
|---|---|
| `~/.config/bromigos/vector/holo/`, your own (new models go here) | VECTOR |
| `/usr/share/bromigos/homelab/holo/`, bromigos-homelab's rack, shown with a homelab configured | VECTOR |
| `/usr/share/bromigos/vector/holo/`, his own: the workstation, the Wick and the emblem | VECTOR |
| `/usr/share/bromigos/themes/<world>/holo/`, a bromigOS world's (package `bromigos-core`) | That world |
| `/usr/share/bromigos/theme/holo/`, the Drift's shared craft and relays | THE DRIFT |
| `~/.local/share/bromigos/ext/active/themes/<world>/holo/`, a theme extension's | That world |

A world is one of bromigOS's themed settings, each with its own colours and models. The
Wick and the Drift are two of them.

The gallery orders its shelves like this (`holo/worlds.py`):

1. the world you're in
2. VECTOR's shelf
3. the Drift
4. the other bromigOS worlds
5. extensions' worlds, by name

On a world's shelf, its post comes first. A post is a model with a part bound to a live
reading.

`$BROMIGOS_SHARE_DIR` replaces `/usr/share/bromigos`, as everywhere in bromigOS.

### Colours

A world's model is drawn in that world's colours, and so is the whole frame around it. The
colours come from the world's `colors.toml`, with your changes from
`~/.config/bromigos/themes/<world>/`. They resolve the way `bromigos theme set` resolves
them.

These are drawn in the theme you're using:

- the world you're in
- VECTOR's models and the Drift's
- every model, when high contrast is on (`bromigos accessibility`)

If a world's colours don't resolve, its models use the theme you're using. A model file
that doesn't load is left off its shelf until the file changes.

### Read, never written

The gallery and VECTOR only read collections. The packaged ones are owned by root.
Extensions' checkouts are read-only to his terminal. Theme extensions are data, and
`numpy` refuses a pickled object in a model file.

`tools/test-gallery-worlds.py` tests the shelves, the colours, the gallery's keys and
hover text, a broken model, and that nothing is written.

## Making a model

[assets/holo/README.md](../assets/holo/README.md) describes the `.holo` format, how a
collection is laid out, and the tools in `tools/holo/` that bake models.

To make one by asking, say "make me a hologram of a coffee mug". VECTOR makes a concept
image, a cutout and a 3D mesh on your homelab (`make_hologram`). Then his build loop bakes
it into `~/.config/bromigos/vector/holo/`, which needs your dotfiles as a git repository.

## Reduced motion

The holograms follow bromigOS's motion setting (`holo/motion.py`). Set it with:

```bash
bromigos accessibility motion full      # or reduced, or still
```

The setting is `motion` in `~/.config/bromigos/accessibility.toml`. Without it, the
theme's `palette.json` decides, and without that, `full`. The live layer reads the same
setting, so the whole desktop moves as one. A change takes effect within a second.

| What moves | full | reduced | still |
|---|---|---|---|
| Turning: the table's rings, a model's idle turn, his core | As designed | At 0.4 of the pace | None |
| His sway and gaze | Springy, with random twitches | Slower and damped, no twitches | Held |
| Flicker: the light cone, the shimmer, the alarm's crackle | Yes | Steady | Steady |
| Blinking: his iris, pulsing notices | Yes | None | None |
| Scan bands, and the glitch at a voice change | Yes | None | None |
| His words | Typed in | Typed in | Shown whole |
| Fades, exhibits, explodes | Eased | Eased | At once |
| Frames | 30–60 a second while shown | The same | One, redrawn only on a change |

What you drive moves as designed under reduced. That means a drag, an explode or a click.

Under still, a window runs no frame timer. Four times a second it asks its scene whether
the picture changed (`still_sig()`), and draws once if it did. The gallery shows the time
its readings were taken, rather than how long ago.

His colours change with his state in every mode, because they tell you what he's doing.
`bromigos-holo status` shows `motion`, and whether a window is `held`.

Tests:

- `tools/test-motion.py` checks the setting, the clock and the scenes, without GL.
- `tools/test-motion-render.py` renders the gallery and his console offscreen under each
  setting. It checks that reduced has no flashes, and that still holds its pixels until
  something changes.

## Using the renderer

The renderer needs a current OpenGL 3.3 context. One frame looks like this:

```python
from holo.render import Holo
from holo.stage import Stage
from holo.live import Live

holo, stage = Holo(), Stage("wick", Live())
holo.begin(w, h, t)
holo.camera(eye, target, 30)
stage.update(dt)
stage.draw(holo, t)                        # the table and the model
stage.draw_callouts(holo, (x, y, w, h))    # leader lines and readouts
holo.end(target_fbo, bg=(0, 0.02, 0, 0.9)) # bloom, composite, overlay
```

`tools/offscreen.py` renders the gallery or his console to a PNG without a window, for
screenshots and tuning:

```bash
python3 tools/offscreen.py gallery out.png workstation 0.5
python3 tools/offscreen.py vector out.png speaking wick
```

It renders through EGL, on an NVIDIA GPU when there is one, else through Mesa. It uses the
live readings of the machine it runs on, and your private overlay when there is one. The
background is bromigOS's empty den, `bromigos-den-empty`, when it's installed. `OFF_BG`
names another picture.

For a picture to publish, use made-up readings and a throwaway `HOME`, so nothing of
yours shows:

```bash
HOME=$(mktemp -d) OFF_DEMO=1 python3 tools/offscreen.py gallery out.png wick 1
```

Without an NVIDIA GPU, add `EGL_PLATFORM=surfaceless LIBGL_ALWAYS_SOFTWARE=1` for a
software render.

`OFF_ATTACH=1` adds a message sent with attachments to his console's conversation. His
input box and its attachment chips are GTK widgets, not holograms. `vector-input` draws
them with GTK's broadway backend, on a `broadwayd` of its own, so nothing reaches your
screen. `OFF_CHIPS` sets how many chips it shows:

```bash
HOME=$(mktemp -d) OFF_DEMO=1 OFF_ATTACH=1 python3 tools/offscreen.py vector out.png
OFF_CHIPS=5 python3 tools/offscreen.py vector-input box.png
```
