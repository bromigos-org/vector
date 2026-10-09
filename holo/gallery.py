"""The model gallery: one model at a time on the projection table, suit-diagnostic
callouts, flip between models. Pure scene logic; the window lives in app.py. It follows
the user's motion setting (holo/motion.py): reduced turns the table slower, calm; still
holds one frame and says what it shows (still_sig) so the window draws only on a change.

Models sit on shelves (fmt.groups()): the world in force first, then VECTOR's own, the
Drift's shared craft and relays, the other worlds, the extensions' themes' worlds. A
world's models are drawn in that world's colours (holo/worlds.py), the whole frame with
them; the strip along the bottom lists the worlds, each in its own colour, and the models
on the current one's shelf."""
import math
import sys
import time

import numpy as np

from . import bind, fmt, motion, worlds
from .render import Holo, col, tinted, wick
from .stage import Stage

HINTS = ("DRAG rotate   SCROLL explode   CLICK isolate a part   ←/→ model   PGUP/PGDN world   "
         "SPACE explode   S scan   R reset   ESC close")
SOURCE_NAME = {"local": "THIS MACHINE"}       # and the lab's, and the plugins' feeds' (source_name)


def source_name(src):
    """What a live source is called under the model: this machine, the lab (homelab.toml's
    `name`, as bromigOS's live layer shows it), or a plugin feed's label."""
    if src in SOURCE_NAME:
        return SOURCE_NAME[src]
    if src == "lab":
        try:
            import bromigos_homelab
            name = bromigos_homelab.lab_name(default="")
        except Exception:
            name = ""
        return f"{name.upper()} LAB" if name else "LAB"
    f = bind._feeds().get(src)
    return f["label"] if f else src.upper()


class Gallery:
    def __init__(self, live, scale=1.0):
        self.live = live
        self.scale = scale
        self.shelves = []                 # [(group, [names])], fmt.groups()
        self.names = []
        self._shelve(fmt.groups())
        self.idx = 0
        self.stages = {}
        self.holo = None
        self.mouse = (0, 0)
        self.drag = None
        self.last_t = time.monotonic()
        self.hover_hint = ""
        self.size = (1, 1)
        self.dirty_pick = False
        self.clock = motion.Clock()
        self.motion = motion.current()
        self.strip = []

    def _shelve(self, shelves):
        self.shelves = [(g, list(ns)) for g, ns in shelves if ns]
        self.names = [n for _, ns in self.shelves for n in ns]
        self.group = {n: g for g, ns in self.shelves for n in ns}

    @property
    def stage(self):
        """The model on the table. One that won't load (a broken file in an extension's
        theme) is taken off its shelf (fmt remembers it until the file changes)."""
        while True:
            n = self.names[self.idx]
            if n in self.stages:
                return self.stages[n]
            try:
                self.stages[n] = Stage(n, self.live)
                return self.stages[n]
            except Exception as e:
                print(f"[gallery] {n}: can't load: {type(e).__name__}: {e}", file=sys.stderr)
                if len(self.names) == 1:
                    raise
                self._shelve([(g, [x for x in ns if x != n]) for g, ns in self.shelves])
                self.idx = min(self.idx, len(self.names) - 1)

    @property
    def shelf(self):
        """(group, names) of the model on the table."""
        g = self.group.get(self.names[self.idx])
        return next(((gg, ns) for gg, ns in self.shelves if gg == g), self.shelves[0])

    def rescan(self):
        """New .holo.npz files appear without a restart (a model VECTOR just baked, a theme
        extension installed); a theme switch puts the new world's shelf first."""
        shelves = fmt.groups()
        if shelves != self.shelves:
            cur = self.names[self.idx] if self.names else None
            self._shelve(shelves)
            self.idx = self.names.index(cur) if cur in self.names else 0
        import os
        for n in list(self.stages):                # gone, or rebaked since its stage was built
            try:
                m = os.path.getmtime(fmt.path_of(n))
            except (FileNotFoundError, OSError):
                m = None
            if m is None or getattr(self.stages[n], "_mtime", m) != m:
                del self.stages[n]
            else:
                self.stages[n]._mtime = m

    def show(self, name=None, step=0):
        self.rescan()
        if name in self.names:
            self.idx = self.names.index(name)
        else:
            self.idx = (self.idx + step) % len(self.names)
        s = self.stage
        s.born = time.monotonic()
        s.scan()
        s.iso = None

    def show_world(self, step=0, group=None):
        """The first model of another shelf: `group`, or `step` shelves on."""
        self.rescan()
        gs = [g for g, _ in self.shelves]
        if group not in gs:
            here = gs.index(self.shelf[0])
            group = gs[(here + step) % len(gs)]
        self.show(dict(self.shelves)[group][0])

    def palette(self):
        """The colours this frame is drawn in: the model's world's (None: the theme in force)."""
        return worlds.palette_for(self.group.get(self.names[self.idx]))

    def still_sig(self):
        """What a still frame shows (holo/motion.py): the window draws again when it changes."""
        from .render import T
        s = self.stage
        return (motion.current(), T.version(), self.idx, tuple(self.names), s.still_sig(), self.hover_hint,
                self.drag, self._sources_info(s, still=True), repr(self.palette()))

    def busy(self):
        """True while something is moving enough to want the higher frame rate."""
        s = self.stage
        return (self.drag is not None or abs(s.explode - s.explode_to) > 0.01 or
                time.monotonic() - s.born < 2.0 or time.monotonic() - s.scan_t0 < s.scan_dur + 0.3)

    # ------------------------------------------------------------------ render
    def render(self, fbo, w, h):
        with tinted(self.palette()):           # a world's model in that world's colours
            self._render(fbo, w, h)

    def _render(self, fbo, w, h):
        if self.holo is None:
            self.holo = Holo(self.scale)
        self.size = (w, h)
        t = time.monotonic()
        dt = min(0.1, t - self.last_t)
        self.last_t = t
        self.motion = m = motion.current()
        s = self.stage
        s.motion = m
        s.update(dt)
        H = self.holo
        H.begin(w, h, self.clock(t, m), calm=motion.calm(m))
        sc = self.scale
        top, bot = 96 * sc, 104 * sc
        vh = h - top - bot
        d = 3.5
        tgt = np.array([0, 0.5, 0], np.float32)
        eye = tgt + d * np.array([0, math.sin(s.pitch), math.cos(s.pitch)], np.float32)
        H.camera(eye, tgt, 30.0, viewport=(0, top, w, vh))
        s.draw(H, t, size=0.95)
        if self.dirty_pick and s.anchor_px is not None:
            s.hover = s.pick(H, *self.mouse) if self.drag is None else s.hover
            self.dirty_pick = False
        s.draw_callouts(H, (24 * sc, top, w - 48 * sc, vh), size=int(14 * sc), edges=True)
        self._hud(H, w, h, s, t)
        H.end(fbo, bg=wick((0.0, 0.02, 0.0, 0.9)), bloom=1.15)

    def _hud(self, H, w, h, s, t):
        sc = self.scale
        ph = col("phosphor")
        H.brackets(10 * sc, 10 * sc, w - 20 * sc, h - 20 * sc, (ph[0], ph[1], ph[2], 0.7), arm=22 * sc, width=1.5)
        # title block: what is on the table and where its readings come from
        x, y = 36 * sc, 26 * sc
        tw, th = H.label(s.meta["title"], x, y, ph, int(30 * sc), "bold", spacing=6)
        H.label(s.meta.get("subtitle", "").upper(), x + tw + 18 * sc, y + th - 22 * sc, col("soft", 1, 0.8), int(14 * sc), spacing=3)
        info = self._sources_info(s, still=self.motion == "still")
        H.label("   ".join(info), w - 36 * sc, y + 8 * sc, col("static", 1, 0.95), int(13 * sc), anchor="rt")
        st = "EXPLODED" if s.explode_to > 0.5 else "ASSEMBLED"
        if s.iso is not None:
            st = "ISOLATED: " + s.parts[s.iso]["label"]
        H.label(st, w - 36 * sc, y + 30 * sc, col("amber" if s.iso is not None else "dim", 1.3, 1), int(13 * sc), "bold", anchor="rt", spacing=2)
        self.strip = []
        # the worlds: every shelf, each named in its own colour, the current one lit
        here, names = self.shelf
        gy, fy = h - 84 * sc, h - 52 * sc
        xs = 36 * sc
        for g, ns in self.shelves:
            r, gg, b = worlds.primary(g)
            lit = g == here
            rgba = (r, gg, b, 1.0 if lit else 0.5)
            lw, lh = H.label(worlds.title(g), xs, gy, rgba, int(12 * sc), "bold" if lit else "normal", spacing=3)
            if lit:
                H.line2d(xs, gy + lh + 2, xs + lw, gy + lh + 2, rgba, 1.2)
            self.strip.append(("world", g, xs, gy, lw, lh))
            xs += lw + 26 * sc
        # the models on this shelf: the current one lit; click a name to switch
        xs = 36 * sc
        for n in names:
            lab = s.meta["title"] if n == s.name else fmt.title(n)
            on = n == s.name
            c = col("phosphor") if on else col("dim", 1.2, 0.9)
            lw, lh = H.label(lab, xs, fy, c, int(13 * sc), "bold" if on else "normal", spacing=2)
            if on:
                H.line2d(xs, fy + lh + 3, xs + lw, fy + lh + 3, ph, 1.6)
            self.strip.append(("model", n, xs, fy, lw, lh))
            xs += lw + 30 * sc
        hint = self.hover_hint or HINTS
        hw, _ = H.measure(hint, int(12 * sc))
        hy = fy if xs + hw < w - 36 * sc else h - 30 * sc     # below the names when they'd meet
        H.label(hint, w - 36 * sc, hy, col("static", 1, 0.9), int(12 * sc), anchor="rt")

    def _sources_info(self, s, still=False):
        """Where the readings come from and how fresh they are. A still frame says when they
        were read (a clock time), not how long ago, so it needn't be drawn again each second."""
        info = []
        for src in bind.sources(s.name, s.parts):
            d, err, at = self.live.get(src)
            if err and d is None:
                info.append(f"{source_name(src)}  unreachable")
            elif at and still:
                info.append(f"{source_name(src)}  read at {time.strftime('%H:%M:%S', time.localtime(at))}")
            elif at:
                info.append(f"{source_name(src)}  read {max(0, time.time() - at):.0f} s ago")
            else:
                info.append(f"{source_name(src)}  reading…")
        if s.name == "emblem":
            info.append("extruded from the canon vectors")
        elif not info:
            info.append("a model of its world: no live readings")
        return info

    def _strip_at(self, x, y):
        for kind, key, sx, sy, lw, lh in self.strip:
            if sx <= x <= sx + lw and sy <= y <= sy + lh:
                return kind, key
        return None

    def _strip_hint(self, kind, key):
        """What a name in the strip is and what a click on it does (hover text)."""
        if kind == "world":
            n = len(dict(self.shelves).get(key, []))
            what = {worlds.VECTOR: "his own models and yours, ", worlds.DRIFT: "the craft and relays every world shares, "}
            extra = (", the world in force" if key == worlds.current() else
                     ", in its own colours" if worlds.palette_for(key) is not None else "")
            return (f"{worlds.title(key)}: {what.get(key, '')}{n} hologram{'s' if n != 1 else ''}{extra}. "
                    "CLICK to go there")
        try:
            sub = fmt.load(key).meta.get("subtitle", "")
        except Exception:
            sub = ""
        return f"{fmt.title(key)}{('  ' + sub.upper()) if sub else ''}. CLICK to put it on the table"

    # ------------------------------------------------------------------ input
    def on_motion(self, x, y):
        if self.drag is not None:
            dx, dy = x - self.drag[0], y - self.drag[1]
            s = self.stage
            s.yaw += dx * 0.008
            s.pitch = min(0.9, max(-0.25, s.pitch + dy * 0.004))
            self.drag = (x, y)
        self.mouse = (x, y)
        self.dirty_pick = True
        s = self.stage
        at = self._strip_at(x, y) if self.drag is None else None
        if at:
            self.hover_hint = self._strip_hint(*at)
        else:
            self.hover_hint = s.parts[s.hover]["hint"] if s.hover is not None else ""

    def on_press(self, x, y, button):
        self.drag = (x, y)
        self.press_at = (x, y, time.monotonic())
        self.stage.dragging = True

    def on_release(self, x, y, button):
        s = self.stage
        s.dragging = False
        px, py, pt = getattr(self, "press_at", (x, y, 0))
        self.drag = None
        if abs(x - px) + abs(y - py) < 6 and time.monotonic() - pt < 0.6:
            at = self._strip_at(x, y)
            if at and at[0] == "world":
                self.show_world(group=at[1])
                return
            if at:
                self.show(at[1])
                return
            i = s.pick(self.holo, x, y) if self.holo else None
            s.isolate(i)
            if i is not None and s.explode_to < 0.5:
                s.scan()

    def on_scroll(self, dy):
        self.stage.toggle_explode(-0.25 * dy)

    def on_key(self, name):
        s = self.stage
        shelf = self.shelf[1]
        if name in ("Right", "l", "Tab"):
            self.show(step=1)
        elif name in ("Left", "h", "ISO_Left_Tab"):
            self.show(step=-1)
        elif name in ("Next", "Page_Down", "bracketright"):
            self.show_world(step=1)
        elif name in ("Prior", "Page_Up", "bracketleft"):
            self.show_world(step=-1)
        elif name in ("space", "e"):
            s.toggle_explode()
        elif name == "s":
            s.scan()
        elif name == "r":
            s.yaw, s.pitch, s.iso, s.explode_to = -0.55, 0.16, None, 0.0
        elif name in [str(i + 1) for i in range(min(9, len(shelf)))]:
            self.show(shelf[int(name) - 1])           # 1-9: the models on this shelf
        elif name in ("Up", "Down"):
            s.toggle_explode(0.25 if name == "Up" else -0.25)
        else:
            return False
        return True
