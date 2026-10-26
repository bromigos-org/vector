"""The holo projector: what another app's projector shows, drawn as holograms.

BRODEC's desktop panel has a holo projector under VECTOR's portrait: one projection at a
time on a table of its own, the world's model when nothing is happening. The panel decides
*what* to show; this draws it, with VECTOR's own renderer (holo.render.Holo: the table,
part-indexed models with fresnel shells and fat edges, free 3D lines, bloom, Pango labels),
so a projection is a real hologram in the theme's colours. holo/portrait.py runs it in the
same process and GL context as his portrait (`--projector W H`).

A projection is a JSON object (the `project` command on portrait.py's stdin):

  {"kind": KIND, "key": "...", ...}

`key` names what it shows: a projection with a new key builds up from the table (its parts
rise, its text types on); the same key with new numbers updates in place. Every string is
the caller's, drawn as given (one line each, clipped); nothing here reads a network or a
private file except the images a projection names. The kinds:

  world    the world's own model, slow and dim (the idle projection). {"model": name}
           picks one; without it, the world in force's (holo/worlds.py), else VECTOR's.
  contact  a person: their portrait card (or their initial) over a small table, with their
           words. {"who", "text", "meta", "eyebrow", "portrait": path|null, "call": bool}.
           A call rings: rings run out from the table.
  card     a picture someone sent, as a card on the table. {"image": path, "eyebrow",
           "title", "caption", "meta"}
  lab      the lab in miniature: machines as racks (height by kind, an inner column at
           their CPU), every service as a tick on the outer ring, grouped; a machine with a
           critical alert flickers red, a warning one is amber. {"machines": [{"id", "name",
           "kind", "up", "cpu", "level": ok|warn|crit|off}], "groups": [{"name", "states":
           ["ok", "warn", ...]}], "callouts": [{"machine", "title", "detail", "level"}],
           "focus": id|null}. At most 12 machines are drawn.
  nolab    no lab connected: a dim table with three ghost outlines.
  chart    a measurement over time, with its threshold and the trend run on. {"title",
           "subtitle", "unit", "min", "max", "points": [[ms, v], ...], "threshold": {"value",
           "label"}, "forecast": {"at": ms, "value", "label"}, "level": ok|warn|crit,
           "gauge": bool}. A gauge (a percentage) fills a cylinder beside the chart.
  orbit    who's around: codec portraits riding an orbit, the most recent in front.
           {"people": [{"name", "ago", "bright": 0..1, "portrait": path|null}]}
  media    what's playing: a reel turning, its progress round it. {"eyebrow", "title",
           "subtitle", "meta", "progress": 0..1, "paused": bool}
  weather  outside: the ground as a wire terrain, the sun's track over it. {"eyebrow",
           "temp", "words", "meta", "sun": 0..1 (through the day), "night": bool}
  list     a few rows with a state and progress each (running jobs). {"title", "rows":
           [{"label", "detail", "progress": 0..1|null, "level"}]}

draw() returns the hit regions of what's on the projector, in logical pixels, for the
caller's hover hints and clicks: [{"id": "machine:node-3", "x", "y", "w", "h"}, ...].
"""
import math
import os
import time

import numpy as np
from OpenGL import GL

from . import fmt
from . import gl as hgl
from .render import GpuModel, Holo, col, ease, lin

KINDS = ("world", "contact", "card", "lab", "nolab", "chart", "orbit", "media", "weather", "list")
MAX_MACHINES = 12                      # two parts each (rack and CPU column): shaders.MAXP is 24
BUILD_S = 1.6                          # how long a new projection takes to build up


def rgba(name, k=1.0, a=1.0):
    r, g, b = lin(name, k)
    return (r, g, b, a)


def level_colour(level):
    return {"crit": "danger", "warn": "amber", "off": "static"}.get(level, "phosphor")


def clip(text, n=60):
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------------------------------------------------------- a textured card in the HDR scene
CARD_VS = """
layout(location=0) in vec3 a_pos;
layout(location=1) in vec2 a_uv;
uniform mat4 u_vp, u_model;
out vec2 v_uv;
void main() { v_uv = a_uv; gl_Position = u_vp * u_model * vec4(a_pos, 1.0); }
"""
CARD_FS = """
in vec2 v_uv;
uniform sampler2D u_tex;
uniform vec3 u_col;
uniform vec3 u_hi;
uniform float u_a, u_time, u_round, u_reveal, u_lines, u_calm;
out vec4 o;
void main() {
    vec2 uv = v_uv;
    float band = step(0.5, fract(uv.y * u_lines * 0.5));
    // a row jitter, like a projector settling (none under reduced motion)
    uv.x += (1.0 - u_calm) * 0.004 * sin(uv.y * 160.0 + u_time * 37.0) * (1.0 - u_reveal * 0.8);
    vec3 t = texture(u_tex, vec2(uv.x, 1.0 - uv.y)).rgb;
    float l = dot(t, vec3(0.299, 0.587, 0.114));
    vec3 c = u_col * (0.05 + 1.25 * pow(l, 1.15)) + u_hi * pow(l, 4.0) * 0.9;
    c *= 0.72 + 0.28 * band;
    float edge = smoothstep(0.0, 0.03, uv.x) * smoothstep(0.0, 0.03, 1.0 - uv.x)
               * smoothstep(0.0, 0.03, uv.y) * smoothstep(0.0, 0.03, 1.0 - uv.y);
    if (u_round > 0.5) {
        float r = length(uv - 0.5) * 2.0;
        edge = smoothstep(1.0, 0.94, r);
        c += u_col * smoothstep(0.88, 0.97, r) * smoothstep(1.0, 0.97, r) * 1.4;
    }
    // reveal from the bottom up with a bright front
    float front = u_reveal * 1.08;
    float shown = step(uv.y, front);
    c += u_hi * 3.0 * smoothstep(0.03, 0.0, abs(uv.y - front)) * step(u_reveal, 0.999);
    // a slow scan band
    c *= 1.0 + (1.0 - u_calm) * 0.35 * smoothstep(0.06, 0.0, abs(fract(u_time * 0.18) - uv.y));
    o = vec4(c * edge * shown * u_a, 1.0);
}
"""


class Card:
    """A picture drawn as a hologram: luminance in the theme's colour, scan rows, a reveal."""

    def __init__(self):
        self.p = hgl.Program(CARD_VS, CARD_FS)
        q = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 0, 0], [1, 1, 0], [0, 1, 0]], np.float32)
        self.mesh = hgl.Mesh([(0, q), (1, q[:, :2].copy())])
        self.tex = {}                  # (path, mtime, crop) -> (tex, aspect)

    def texture(self, path, crop=None):
        from PIL import Image
        try:
            key = (path, os.path.getmtime(path), crop)
        except OSError:
            return None
        if key not in self.tex:
            if len(self.tex) > 16:     # a few portraits and pictures; the oldest go
                for k in list(self.tex)[:8]:
                    GL.glDeleteTextures([self.tex.pop(k)[0]])
            try:
                im = Image.open(path).convert("RGB")
            except Exception:          # noqa: BLE001  (not a picture: nothing drawn)
                return None
            if crop == "square":
                s = min(im.width, im.height)
                im = im.crop(((im.width - s) // 2, 0, (im.width - s) // 2 + s, s))
            w = min(512, im.width)
            im = im.resize((w, max(1, int(w * im.height / im.width))), Image.LANCZOS)
            tid = hgl.texture(im.width, im.height, np.asarray(im, np.uint8).tobytes(), fmt=GL.GL_RGB8, src=GL.GL_RGB)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tid)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
            self.tex[key] = (tid, im.width / im.height)
        return self.tex[key]

    def aspect(self, path, crop=None):
        t = self.texture(path, crop)
        return t[1] if t else None

    def draw(self, holo, path, model, colour="phosphor", a=1.0, round_=False, reveal=1.0, lines=90.0,
             crop=None, calm=False):
        t = self.texture(path, crop)
        if t is None:
            return False
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDepthMask(False)
        p = self.p.use()
        p.m4("u_vp", holo.vp)
        p.m4("u_model", model)
        p.f("u_col", *lin(colour))
        p.f("u_hi", *lin("white", 0.6))
        p.f("u_a", a)
        p.f("u_time", holo.t)
        p.f("u_round", 1.0 if round_ else 0.0)
        p.f("u_reveal", reveal)
        p.f("u_lines", lines)
        p.f("u_calm", 1.0 if calm else 0.0)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, t[0])
        p.i("u_tex", 0)
        self.mesh.draw(GL.GL_TRIANGLES)
        GL.glDepthMask(True)
        return True

    def release(self):
        for tid, _ in self.tex.values():
            GL.glDeleteTextures([tid])
        self.tex.clear()
        self.mesh.delete()


# ---------------------------------------------------------------- procedural part-indexed meshes
class Builder:
    def __init__(self):
        self.pos, self.nrm, self.part, self.tri, self.edge = [], [], [], [], []

    def v(self, p, part, centre):
        n = np.asarray(p, np.float32) - np.asarray(centre, np.float32)
        n = n / (np.linalg.norm(n) + 1e-6)
        self.pos.append(p)
        self.nrm.append(n)
        self.part.append(part)
        return len(self.pos) - 1

    def box(self, x0, x1, y0, y1, z0, z1, part, slots=0, bays=0):
        c = ((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2)
        ids = [self.v((x, y, z), part, c) for y in (y0, y1) for z in (z0, z1) for x in (x0, x1)]
        faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        for a, b, cc, d in faces:
            self.tri += [(ids[a], ids[b], ids[cc]), (ids[a], ids[cc], ids[d])]
        for a, b in [(0, 1), (1, 3), (3, 2), (2, 0), (4, 5), (5, 7), (7, 6), (6, 4), (0, 4), (1, 5), (2, 6), (3, 7)]:
            self.edge.append((ids[a], ids[b]))
        for k in range(1, slots + 1):          # rack units round the front and sides
            y = y0 + (y1 - y0) * k / (slots + 1)
            r = [self.v((x, y, z), part, c) for z in (z0, z1) for x in (x0, x1)]
            self.edge += [(r[2], r[3]), (r[0], r[2]), (r[1], r[3])]
        for k in range(1, bays + 1):           # drive bays: verticals on the front
            x = x0 + (x1 - x0) * k / (bays + 1)
            a, b = self.v((x, y0 + 0.01, z1), part, c), self.v((x, y1 - 0.01, z1), part, c)
            self.edge.append((a, b))

    def gpu(self, parts):
        return GpuModel(np.asarray(self.pos, np.float32), np.asarray(self.nrm, np.float32),
                        np.asarray(self.part, np.uint8), np.asarray(self.tri, np.uint32),
                        np.asarray(self.edge, np.uint32), parts)


# a rack's (height, width, depth, rack units, drive bays) by machine kind
RACK = {"host": (0.30, 0.12, 0.12, 7, 0), "node": (0.20, 0.075, 0.09, 4, 0), "vm": (0.16, 0.07, 0.08, 3, 0),
        "gpu": (0.25, 0.12, 0.11, 5, 0), "nas": (0.17, 0.13, 0.10, 3, 5), "pi": (0.06, 0.05, 0.05, 0, 0),
        "other": (0.14, 0.07, 0.08, 2, 0)}


def lab_layout(machines):
    """Machine centres on the table: hosts in the middle, the rest round them."""
    hosts = [m for m in machines if m.get("kind") == "host"][:2]
    ring = [m for m in machines if m not in hosts]
    out = {}
    for i, m in enumerate(hosts):
        out[m["id"]] = (0.0 if len(hosts) == 1 else (-0.08 + 0.16 * i), 0.0)
    for i, m in enumerate(ring):
        a = math.radians(-90 + 360 * i / max(1, len(ring)) + 22)
        out[m["id"]] = (0.40 * math.cos(a), 0.40 * math.sin(a))
    return out


def build_lab(machines):
    """Racks (parts 0..n-1) and their CPU columns (parts n..2n-1, unit height: scaled by the
    part matrix, so a new reading needs no new mesh)."""
    b = Builder()
    lay = lab_layout(machines)
    n = len(machines)
    for i, m in enumerate(machines):
        x, z = lay[m["id"]]
        h, w, d, slots, bays = RACK.get(m.get("kind"), RACK["other"])
        b.box(x - w / 2, x + w / 2, 0.0, h, z - d / 2, z + d / 2, i, slots=slots, bays=bays)
    for i, m in enumerate(machines):
        x, z = lay[m["id"]]
        h, w, d, *_ = RACK.get(m.get("kind"), RACK["other"])
        b.box(x - w * 0.32, x + w * 0.32, 0.0, 1.0, z - d * 0.32, z + d * 0.32, n + i)
    return b.gpu([{"id": m["id"]} for m in machines] + [{"id": m["id"] + "-cpu"} for m in machines]), lay


# ---------------------------------------------------------------- helpers
def ring(r, y=0.0, n=96, a0=0.0, a1=2 * math.pi):
    a = np.linspace(a0, a1, n + 1)
    p = np.stack([r * np.cos(a), np.full_like(a, y), r * np.sin(a)], 1).astype(np.float32)
    return np.repeat(p, 2, 0)[1:-1]


def polyline(points):
    p = np.asarray(points, np.float32)
    return np.repeat(p, 2, 0)[1:-1]


def flick(t, seed, rate=13.0):
    return 0.5 + 0.5 * math.sin(t * rate + seed * 5.1) * math.sin(t * rate * 0.37 + seed)


def hours(ms):
    """A timestamp's weekday, in the computer's own time zone: THU."""
    return time.strftime("%a", time.localtime(ms / 1000.0)).upper()


# ---------------------------------------------------------------- the projector
class Projector:
    """Draws one projection at a time into a logical w x h frame (pixels: x scale)."""

    def __init__(self, w, h, scale=1.0):
        self.w, self.h, self.scale = int(w), int(h), float(scale)
        self.holo = Holo(self.scale)
        self.card = Card()
        self.models = {}
        self.lab = None                    # (machine ids/kinds, GpuModel, layout)
        self.spec = {"kind": "world"}
        self.key = None
        self.since = time.monotonic()
        self.regions = []
        self.meta = {}
        self.calm = False
        self.still = False

    # ------------------------------------------------------------ state
    def set(self, spec):
        """Show `spec`. A new key starts it building from the table."""
        if not isinstance(spec, dict) or spec.get("kind") not in KINDS:
            return False
        key = str(spec.get("key") or spec.get("kind"))
        if key != self.key:
            self.key = key
            self.since = time.monotonic()
        self.spec = spec
        return True

    def age(self, now=None):
        return (now or time.monotonic()) - self.since

    def rate(self, fps, now=None):
        """Frames a second it wants: `fps` while it builds up, 15 while it moves on its own (a
        call ringing, a critical machine flickering, a reel turning), 10 otherwise."""
        k = self.spec.get("kind")
        if self.age(now) < BUILD_S + 0.6:
            return fps
        moving = (k == "contact" and self.spec.get("call")) \
            or (k == "lab" and any(m.get("level") == "crit" for m in self.spec.get("machines", []))) \
            or (k == "media" and not self.spec.get("paused"))
        return min(fps, 15) if moving else min(fps, 10)

    def busy(self, now=None):
        return self.rate(30, now) > 10

    # ------------------------------------------------------------ drawing
    def model(self, name):
        if name not in self.models:
            m = fmt.load(name)
            self.models[name] = (m, GpuModel.from_holo(m))
        return self.models[name]

    def camera(self, dist=2.25, pitch=0.40, aim_y=0.2, fov=30.0, aim_x=0.0):
        aim = np.array([aim_x, aim_y, 0.0], np.float32)
        eye = aim + dist * np.array([0, math.sin(pitch), math.cos(pitch)], np.float32)
        self.holo.camera(eye, aim, fov)

    def lines(self, pts, colour, model=None, width=1.2, depth=True):
        pts = np.asarray(pts, np.float32)
        if len(pts) < 2:
            return
        cols = np.tile(np.asarray(colour, np.float32), (len(pts), 1)) if np.ndim(colour) == 1 \
            else np.asarray(colour, np.float32)
        self.holo.draw_lines3d(pts, cols, model, width=width, depth=depth)

    def label(self, text, x, y, name="soft", size=11, a=1.0, weight="normal", anchor="lt", spacing=0.0, k=1.0):
        s = self.holo.scale
        return self.holo.label(clip(text, 48), x * s, y * s, col(name, k, a), size=size, weight=weight,
                               anchor=anchor, spacing=spacing)

    def at(self, p3):
        """A world point's place on the projector, in logical pixels."""
        (px, py), = self.holo.project(np.asarray(p3, np.float32)[None])[0]
        return float(px) / self.holo.scale, float(py) / self.holo.scale

    def leader(self, p3, x, y, name, a=1.0):
        s = self.holo.scale
        px, py = self.at(p3)
        self.holo.line2d(px * s, py * s, x * s, y * s, col(name, 1.0, 0.8 * a), width=1.0)
        self.holo.line2d(px * s - 3, py * s, px * s + 3, py * s, col(name, 1.0, a), width=1.2)

    def table(self, t, fade=1.0, radius=0.62, power=0.8, colour="phosphor", cone_h=0.5):
        self.holo.draw_table(hgl.rot_y(-t * 0.05), radius=radius, cone_top=radius * 0.95, cone_h=cone_h,
                             power=power * fade, colour=colour)

    def region(self, rid, x, y, w, h):
        self.regions.append({"id": rid, "x": round(float(x), 1), "y": round(float(y), 1),
                             "w": round(float(w), 1), "h": round(float(h), 1)})

    def region_around(self, rid, pts3):
        pts = np.asarray([self.at(p) for p in pts3], np.float32)
        x0, y0 = pts.min(0)
        x1, y1 = pts.max(0)
        self.region(rid, x0 - 3, y0 - 3, x1 - x0 + 6, y1 - y0 + 6)

    def draw(self, fbo, t, mode="full", fade=1.0, now=None):
        """One frame into `fbo` (RGBA8, w*scale x h*scale). t: the animation clock (holo/motion.py
        Clock: slower under reduced motion, standing when still); mode: the motion setting
        (reduced: no flicker, sweep or jitter; still: everything shown whole at once).
        Returns the hit regions."""
        self.calm = mode != "full"
        self.still = mode == "still"
        self.regions = []
        self.meta = {}
        W, H = int(self.w * self.scale), int(self.h * self.scale)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
        self.holo.begin(W, H, t, calm=self.calm)
        age = BUILD_S + 10 if self.still else self.age(now)
        kind = self.spec.get("kind", "world")
        getattr(self, "p_" + kind)(t, fade, age, self.spec)
        self.holo.end(fbo, bg=(0.0, 0.0, 0.0, 0.0), bloom=1.15, fade=1.0)
        return self.regions

    # ------------------------------------------------------------ the projections
    def world_model(self, spec):
        """The model to idle on: the one named, the world in force's, VECTOR's own."""
        want = spec.get("model")
        try:
            if want:
                fmt.path_of(want)
                return want
            shelves = fmt.groups()
        except Exception:                  # noqa: BLE001
            return None
        from . import worlds
        here = worlds.current()
        for g, names in shelves:
            if g == here and names:
                return names[0]
        for g, names in shelves:
            if g == worlds.VECTOR and here in names:
                return here
        for g, names in shelves:
            if g == worlds.DRIFT and names:
                return names[0]
        return next((names[0] for _, names in shelves if names), None)

    def p_world(self, t, fade, age, spec):
        self.camera(dist=2.1, pitch=0.36, aim_y=0.22)
        self.table(t, fade, radius=0.5, power=0.45, cone_h=0.42)
        name = self.world_model(spec)
        if not name:
            self.lines(ring(0.3, 0.12, 96), rgba("dim", 0.6 * fade, 0.6 * fade), width=1.0)
            return
        try:
            m, gm = self.model(name)
        except Exception:                  # noqa: BLE001
            return
        h, r = m.meta.get("height", 1.0), m.meta.get("radius", 0.5)
        s = 0.62 / max(h, 1.6 * r, 0.6)
        bob = 0.0 if self.calm else 0.01 * math.sin(t * 0.8)
        mm = hgl.translate(0, 0.05 + bob, 0) @ hgl.rot_y(-0.6 + t * 0.12) @ hgl.scale(s)
        n = gm.n
        pm = np.tile(np.eye(4, dtype=np.float32), (n, 1, 1))
        pc = np.zeros((n, 4), np.float32)
        for i in range(n):
            arrive = ease((age - 0.12 * i) / 1.0)
            k = 0.55 * (1.0 if self.calm else 0.94 + 0.06 * math.sin(t * 9 + i * 1.7))
            pc[i] = (*lin("phosphor", k), fade * arrive)
            pm[i] = hgl.translate(0, (1 - arrive) * 0.4, 0)
        self.holo.draw_model(gm, mm, pm, pc, np.ones(n, np.float32), scan=(0, (t * 0.15) % 1.2, 0),
                             fill=0.45, width=1.2, ghost=0.1)
        self.region("world", self.w * 0.25, 10, self.w * 0.5, self.h - 20)
        self.meta = {"model": name, "title": fmt.title(name)}

    def p_nolab(self, t, fade, age, spec):
        self.camera(dist=2.05, pitch=0.48, aim_y=0.08)
        self.table(t, fade, radius=0.55, power=0.25, colour="dim", cone_h=0.25)
        b = 0.18 + (0.0 if self.calm else 0.04 * math.sin(t * 1.3))
        for (x, z, h) in [(-0.22, 0.0, 0.18), (0.0, -0.05, 0.26), (0.22, 0.02, 0.14)]:
            w = 0.09
            pts = []
            for y in (0.0, h):
                sq = [(x - w, y, z - w), (x + w, y, z - w), (x + w, y, z + w), (x - w, y, z + w), (x - w, y, z - w)]
                pts += list(polyline(sq))
            for cx, cz in [(x - w, z - w), (x + w, z - w), (x + w, z + w), (x - w, z + w)]:
                pts += [(cx, 0, cz), (cx, h, cz)]
            self.lines(pts, rgba("dim", b * fade * 3.0, 0.8 * fade), width=1.0)
        self.lines(ring(0.6, 0.0, 120), rgba("dim", 0.6 * fade, 0.6 * fade), width=1.0)

    def p_lab(self, t, fade, age, spec):
        machines = [m for m in spec.get("machines", []) if isinstance(m, dict) and m.get("id")][:MAX_MACHINES]
        if not machines:
            return self.p_nolab(t, fade, age, spec)
        sig = tuple((m["id"], m.get("kind")) for m in machines)
        if not self.lab or self.lab[0] != sig:
            if self.lab:
                self.lab[1].delete()
            gm, lay = build_lab(machines)
            self.lab = (sig, gm, lay)
        _, gm, lay = self.lab
        self.camera(dist=2.05, pitch=0.48, aim_y=0.08)
        self.table(t, fade, radius=0.44, power=0.7, cone_h=0.3)
        spin = -0.35 + t * 0.06
        base = hgl.rot_y(spin)
        focus = spec.get("focus")
        n = len(machines)
        pm = np.tile(np.eye(4, dtype=np.float32), (2 * n, 1, 1))
        pc = np.zeros((2 * n, 4), np.float32)
        pw = np.ones(2 * n, np.float32)
        for i, m in enumerate(machines):
            rise = ease((age - 0.07 * i) / 0.7)
            x, z = lay[m["id"]]
            h = RACK.get(m.get("kind"), RACK["other"])[0]
            grow = hgl.translate(x, 0, z) @ np.diag([1, max(rise, 0.02), 1, 1]).astype(np.float32) @ hgl.translate(-x, 0, -z)
            pm[i] = grow
            cpu = max(0.02, min(1.0, float(m.get("cpu") or 0) / 100.0))
            pm[n + i] = grow @ hgl.translate(x, 0.004, z) @ np.diag([1, h * cpu, 1, 1]).astype(np.float32) @ hgl.translate(-x, 0, -z)
            level = m.get("level", "ok")
            name = level_colour(level)
            k = 0.75 + 0.25 * cpu
            if level == "crit":
                k = 0.7 + (0.5 if self.calm else 0.9 * (flick(t, 3, 17) > 0.35))
            elif level == "warn":
                k = 0.85
            elif level == "off" or not m.get("up", True):
                name, k = "static", 0.45
            dim = 1.0
            if focus and m["id"] != focus:
                dim = 0.45
            if focus == m["id"]:
                k *= 1.5
                pw[i] = 1.6
            pc[i] = (*lin(name, k * dim), fade * rise)
            pc[n + i] = (*lin(name, 1.3 * k * dim), fade * rise * 0.9)
        self.holo.draw_model(gm, base, pm, pc, pw, scan=(0, (t * 0.25) % 1.0, 0), fill=0.6, width=1.25, ghost=0.15)
        # links: the middle to each machine, with a packet running along
        pts, cs = [], []
        for i, m in enumerate(machines):
            x, z = lay[m["id"]]
            if abs(x) + abs(z) < 0.2:
                continue
            c = rgba(level_colour(m.get("level")) if m.get("level") == "crit" else "dim", 0.6 * fade, 0.7 * fade)
            pts += [(0, 0.003, 0), (x, 0.003, z)]
            cs += [c, c]
            if not self.calm:
                f = (t * 0.6 + i * 0.37) % 1.0
                pts += [(x * f, 0.004, z * f), (x * min(1, f + 0.06), 0.004, z * min(1, f + 0.06))]
                c2 = rgba("white", 1.2 * fade, fade)
                cs += [c2, c2]
        self.lines(pts, cs, base, width=1.0)
        # services: a tick each on the outer ring, grouped with gaps
        ticks = self.service_ring(spec)
        if ticks is not None:
            aa, inner, outer, names, kk0, crit, arcs = ticks
            reveal = ease((age - 0.4) / 1.0)
            shown = aa / (2 * math.pi) <= reveal
            kk = kk0.copy()
            if not self.calm:
                flk = np.array([flick(t, k, 11) > 0.3 for k in range(len(aa))], np.float32)
                kk = np.where(crit, 0.8 + 1.0 * flk, kk)
                kk = kk * (1.0 + 0.8 * np.maximum(0.0, np.cos(aa - t * 1.2)) ** 30)
            pts = np.repeat(np.stack([inner, outer], 1)[shown].reshape(-1, 3), 1, 0)
            base_cols = np.array([lin(n) for n in names], np.float32)[shown]
            cols = np.concatenate([base_cols * (kk[shown] * fade)[:, None], np.full((shown.sum(), 1), fade, np.float32)], 1)
            cols = np.repeat(cols, 2, 0)
            arc_cols = np.tile(np.asarray(rgba("phosphor", 0.5 * fade, 0.8 * fade), np.float32), (len(arcs), 1))
            if len(pts):
                self.lines(np.concatenate([pts, arcs]), np.concatenate([cols, arc_cols]), base, width=1.15)
            else:
                self.lines(arcs, arc_cols, base, width=1.15)
        # hit regions: each machine's rack
        for m in machines:
            x, z = lay[m["id"]]
            h, w, d, *_ = RACK.get(m.get("kind"), RACK["other"])
            corners = [(base @ np.array([x + sx * w / 2, y, z + sz * d / 2, 1.0], np.float32))[:3]
                       for sx in (-1, 1) for sz in (-1, 1) for y in (0.0, h)]
            self.region_around("machine:" + m["id"], corners)
        # callouts: up to two, worst first, with a beam over the machine
        label_in = ease((age - 0.8) / 0.4)
        slots = [(self.w - 8, 12, "r"), (8, 14, "l")]
        for (lx, ly, side), c in zip(slots, spec.get("callouts", [])[:2]):
            mid = c.get("machine")
            if mid not in lay or age < 0.6:
                continue
            x, z = lay[mid]
            m = next(mm for mm in machines if mm["id"] == mid)
            h = RACK.get(m.get("kind"), RACK["other"])[0]
            top = (base @ np.array([x, h, z, 1.0], np.float32))[:3]
            name = level_colour(c.get("level", "crit"))
            tone = "danger_text" if name == "danger" else name
            if c.get("level", "crit") == "crit":
                beam = [(top[0], top[1], top[2]), (top[0], top[1] + 0.22, top[2])]
                self.lines(beam, rgba(name, 1.2 * fade * (1.0 if self.calm else 0.6 + 0.4 * flick(t, 9)), fade), width=1.6)
                anchor = (top[0], top[1] + 0.22, top[2])
            else:
                anchor = (top[0], top[1], top[2])
            title = clip(c.get("title", mid), 16).upper()
            detail = clip(c.get("detail", ""), 24).upper()
            al = "rt" if side == "r" else "lt"
            w1 = self.holo.measure(title, size=12, weight="bold", spacing=2)[0] / self.holo.scale
            w2 = self.holo.measure(detail, size=11, spacing=1)[0] / self.holo.scale
            end_x = lx - max(w1, w2) - 6 if side == "r" else lx + max(w1, w2) + 6
            self.leader(anchor, end_x, ly + 8, name, label_in)
            self.label(title, lx, ly, name, size=12, weight="bold", a=label_in, spacing=2, anchor=al)
            self.label(detail, lx, ly + 16, tone, size=11, a=label_in * 0.9, spacing=1, anchor=al)

    def service_ring(self, spec):
        """The service ticks' geometry for this projection's groups, cached by their states:
        angles, inner and outer ends, colour names, base brightness, which are critical, and
        the groups' arcs."""
        groups = [g for g in spec.get("groups", []) if isinstance(g, dict) and g.get("states")]
        sig = tuple((g.get("name"), tuple(g["states"])) for g in groups)
        if getattr(self, "_ring_sig", None) == sig:
            return self._ring
        self._ring_sig = sig
        total = sum(len(g["states"]) for g in groups)
        if not total:
            self._ring = None
            return None
        gap = 0.05
        span = 2 * math.pi - gap * len(groups)
        a = 0.0
        aa, inner, outer, names, kk, crit, arcs = [], [], [], [], [], [], []
        for g in groups:
            a0 = a
            for k, state in enumerate(g["states"]):
                ang = a + span * (k + 0.5) / total
                bad = state in ("warn", "crit")
                r0, r1 = 0.60, (0.72 if bad else 0.645)
                aa.append(ang)
                inner.append((r0 * math.cos(ang), 0.002, r0 * math.sin(ang)))
                outer.append((r1 * math.cos(ang), 0.002 + (r1 - r0) * 0.4, r1 * math.sin(ang)))
                names.append(level_colour(state))
                kk.append({"warn": 1.4, "crit": 1.4, "off": 0.6}.get(state, 1.15))
                crit.append(state == "crit")
            a += span * len(g["states"]) / total
            arcs.append(ring(0.585, 0.0, 24, a0, a))
            a += gap
        self._ring = (np.array(aa, np.float32), np.array(inner, np.float32), np.array(outer, np.float32), names,
                      np.array(kk, np.float32), np.array(crit, bool), np.concatenate(arcs).astype(np.float32))
        return self._ring

    def p_contact(self, t, fade, age, spec):
        call = bool(spec.get("call"))
        self.camera(dist=2.1, pitch=0.30, aim_y=0.26, aim_x=0.36)
        self.table(t, fade, radius=0.34, power=1.1 if call else 0.85, cone_h=0.12)
        reveal = 1.0 if self.calm else ease((age - 0.15) / 0.9)
        h = 0.5
        sway = 0.0 if self.calm else 0.10 * math.sin(t * 0.6)
        bob = 0.0 if self.calm else 0.008 * math.sin(t * 1.4)
        mm = hgl.translate(0, 0.07 + bob, 0) @ hgl.rot_y(sway) @ hgl.translate(-h / 2, 0, 0) @ hgl.scale(h)
        path = spec.get("portrait")
        a = fade * (1.0 if self.calm else 0.85 + 0.15 * flick(t, 2, 23))
        drawn = bool(path) and self.card.draw(self.holo, path, mm, "phosphor", a=a, reveal=reveal, lines=110,
                                             crop="square", calm=self.calm)
        if not drawn:                       # no portrait: their initial on a codec card
            frame = polyline([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 0)])
            self.lines(frame, rgba("dim", 0.9 * fade, fade * reveal), mm, width=1.0)
            cx, cy = self.at((mm @ np.array([0.5, 0.5, 0, 1.0], np.float32))[:3])
            initial = (str(spec.get("who") or "?").strip() or "?")[0].upper()
            self.label(initial, cx, cy, "primary", size=40, weight="bold", a=fade * reveal, anchor="cm")
        br = []
        s, e = 0.05, 0.035
        for (cx, cy, dx, dy) in [(0, 0, 1, 1), (1, 0, -1, 1), (0, 1, 1, -1), (1, 1, -1, -1)]:
            br += [(cx - dx * e, cy - dy * e, 0), (cx - dx * e + dx * s * 2, cy - dy * e, 0),
                   (cx - dx * e, cy - dy * e, 0), (cx - dx * e, cy - dy * e + dy * s * 2, 0)]
        self.lines(br, rgba("phosphor", 1.4 * fade, fade * reveal), mm, width=1.4)
        self.region_around("contact", [(mm @ np.array([u, v, 0, 1.0], np.float32))[:3] for u in (0, 1) for v in (0, 1)])
        if call:
            for k in range(3):
                f = 0.5 if self.calm else (t * 0.7 + k / 3) % 1.0
                if self.calm and k:
                    break
                self.lines(ring(0.3 + 0.35 * f, 0.004, 96), rgba("phosphor", 1.6 * (1 - f) * fade, (1 - f) * fade), width=1.3)
        ti = 1.0 if self.calm else ease((age - 0.6) / 0.5)
        x = 244
        who = clip(spec.get("who", ""), 12).upper()
        self.label(spec.get("eyebrow") or ("CODEC CALL" if call else "NEW MESSAGE"), x, 34, "dim", size=11, a=ti, spacing=3)
        self.label(who, x, 50, "primary", size=20, weight="bold", a=ti, spacing=5)
        if call:
            self.label(clip(spec.get("text", "ringing"), 22), x, 84, "text", size=12, a=ti)
            blink = 1.0 if self.calm else 0.6 + 0.4 * flick(t, 1, 5)
            self.label(clip(spec.get("meta", ""), 24), x, 104, "muted", size=11, a=ti * blink)
        else:
            text = clip(spec.get("text", ""), 26)
            if not self.calm:
                text = text[: int(len(text) * ease((age - 0.9) / 0.7)) + (1 if age > 0.9 else 0)]
            self.label(f"“{text}”" if text else "", x, 84, "white", size=12, a=ti)
            self.label(clip(spec.get("meta", ""), 24), x, 104, "muted", size=11, a=ti)
        self.region("words", x - 4, 30, self.w - x, 92)

    def p_card(self, t, fade, age, spec):
        self.camera(dist=2.1, pitch=0.30, aim_y=0.26, aim_x=0.32)
        self.table(t, fade, radius=0.42, power=0.8, cone_h=0.12)
        reveal = 1.0 if self.calm else ease((age - 0.15) / 0.9)
        path = spec.get("image")
        aspect = self.card.aspect(path) if path else None
        w = 0.78
        hh = min(0.6, w / aspect) if aspect else 0.44
        sway = 0.0 if self.calm else 0.05 * math.sin(t * 0.5)
        bob = 0.0 if self.calm else 0.008 * math.sin(t * 1.3)
        mm = hgl.translate(0, 0.08 + bob, 0) @ hgl.rot_y(0.22 + sway) @ hgl.translate(-w / 2, 0, 0) @ \
            np.diag([w, hh, 1, 1]).astype(np.float32)
        if path:
            self.card.draw(self.holo, path, mm, "phosphor", a=fade, reveal=reveal, lines=120, calm=self.calm)
        fr = polyline([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 0)])
        self.lines(fr, rgba("phosphor", 0.9 * fade, fade * reveal), mm, width=1.1)
        self.region_around("card", [(mm @ np.array([u, v, 0, 1.0], np.float32))[:3] for u in (0, 1) for v in (0, 1)])
        ti = 1.0 if self.calm else ease((age - 0.6) / 0.5)
        x = 262
        self.label(clip(spec.get("eyebrow", ""), 20).upper(), x, 34, "dim", size=11, a=ti, spacing=2)
        self.label(clip(spec.get("title", ""), 18), x, 52, "white", size=13, weight="bold", a=ti)
        cap = clip(spec.get("caption", ""), 20)
        self.label(f"“{cap}”" if cap else "", x, 74, "text", size=12, a=ti)
        self.label(clip(spec.get("meta", ""), 22), x, 94, "muted", size=11, a=ti)

    def p_chart(self, t, fade, age, spec):
        pts = [p for p in spec.get("points", []) if isinstance(p, (list, tuple)) and len(p) == 2]
        gauge = bool(spec.get("gauge"))
        self.camera(dist=2.0, pitch=0.22, aim_y=0.28, aim_x=0.05 if gauge else 0.0)
        self.table(t, fade, radius=0.42, power=0.5, cone_h=0.1)
        if len(pts) < 2:
            return
        xs = np.array([p[0] for p in pts], np.float64)
        ys = np.array([p[1] for p in pts], np.float64)
        fc = spec.get("forecast") if isinstance(spec.get("forecast"), dict) else None
        thr = spec.get("threshold") if isinstance(spec.get("threshold"), dict) else None
        lo = spec.get("min")
        hi = spec.get("max")
        vals = list(ys) + ([fc["value"]] if fc else []) + ([thr["value"]] if thr else [])
        if lo is None or hi is None:
            span = max(vals) - min(vals) or 1.0
            lo = min(vals) - 0.1 * span if lo is None else lo
            hi = max(vals) + 0.1 * span if hi is None else hi
        # zoom to where the line is: never wider than its range plus a margin
        if spec.get("unit") == "%" and min(vals) > lo + 20:
            lo = max(lo, math.floor((min(vals) - 10) / 10) * 10)
        t0, t1 = xs[0], xs[-1]
        W, H = (1.05 if gauge else 1.25), 0.44
        x0, y0 = (-0.62 if gauge else -0.62), 0.13
        tilt = 0.0 if self.calm else 0.04 * math.sin(t * 0.4)
        base = hgl.rot_y(0.10 + tilt) @ hgl.rot_x(-0.12)
        draw_in = 1.0 if self.calm else ease((age - 0.2) / 1.4)

        def P(tt, v):
            return (x0 + W * (tt - t0) / max(t1 - t0, 1.0), y0 + H * (v - lo) / max(hi - lo, 1e-9), 0.0)

        g = []
        for k in range(5):
            y = y0 + H * k / 4
            g += [(x0, y, 0), (x0 + W, y, 0)]
        days = max(1, int(round((t1 - t0) / 86_400_000.0)))
        ticks = days if 2 <= days <= 10 else 6
        for d in range(ticks + 1):
            x = x0 + W * d / ticks
            g += [(x, y0, 0), (x, y0 + H, 0)]
        self.lines(g, rgba("dim", 0.55 * fade, 0.6 * fade), base, width=0.9)
        n = max(2, int(len(xs) * draw_in))
        level = spec.get("level", "ok")
        lc = level_colour(level)
        self.lines(polyline([P(a, b) for a, b in zip(xs[:n], ys[:n])]), rgba("phosphor", 1.5 * fade, fade), base, width=1.8)
        cur, cols = [], []
        step = max(1, len(xs) // 60)
        for a, b in list(zip(xs[:n], ys[:n]))[::step]:
            cur += [P(a, lo), P(a, b)]
            cols += [rgba("phosphor", 0.0, 0.0), rgba("phosphor", 0.32 * fade, 0.5 * fade)]
        self.lines(cur, cols, base, width=1.0)
        if thr:
            a0, a1 = np.array(P(t0, thr["value"])), np.array(P(t1, thr["value"]))
            dash = []
            for i in range(0, 28, 2):
                dash += [tuple(a0 + (a1 - a0) * i / 28), tuple(a0 + (a1 - a0) * (i + 1) / 28)]
            self.lines(dash, rgba("amber", 1.0 * fade, 0.9 * fade), base, width=1.1)
        end = P(xs[-1], ys[-1])
        if draw_in >= 1.0 and fc:
            fx = np.linspace(t1, t1 + (t1 - t0) * 0.1, 8)
            slope = (fc["value"] - ys[-1]) / max(fc["at"] - t1, 1.0)
            fy = ys[-1] + (fx - t1) * slope
            fd = []
            for i in range(0, len(fx) - 1, 2):
                fd += [P(fx[i], min(fy[i], hi)), P(fx[i + 1], min(fy[i + 1], hi))]
            self.lines(fd, rgba("danger", 1.3 * fade, fade), base, width=1.4)
        if draw_in >= 1.0:
            e3 = (base @ np.array([*end, 1.0], np.float32))[:3]
            pulse = 1.0 if self.calm else 1.0 + 0.5 * math.sin(t * 6)
            self.lines(ring(0.022 * pulse, 0, 24), rgba(lc, 2.0 * fade, fade), hgl.translate(*e3) @ hgl.rot_x(math.pi / 2), width=1.4)
        if gauge:
            cx, gr = 0.66, 0.08
            cyl = []
            for k in range(5):
                cyl += list(ring(gr, y0 + H * k / 4, 40))
            for k in range(8):
                a = 2 * math.pi * k / 8
                cyl += [(gr * math.cos(a), y0, gr * math.sin(a)), (gr * math.cos(a), y0 + H, gr * math.sin(a))]
            self.lines(cyl, rgba("dim", 0.6 * fade, 0.6 * fade), hgl.translate(cx, 0, 0), width=0.9)
            fill = max(0.0, min(1.0, (ys[-1] - lo) / max(hi - lo, 1e-9))) * draw_in
            fl = []
            for k in range(int(fill * 12) + 1):
                fl += list(ring(gr * 0.82, y0 + H * fill * k / 12, 32))
            self.lines(fl, rgba("phosphor", 0.5 * fade, 0.7 * fade), hgl.translate(cx, 0, 0), width=1.0)
            self.lines(ring(gr * 0.88, y0 + H * fill, 32), rgba(lc, 1.6 * fade, fade), hgl.translate(cx, 0, 0), width=1.4)
        ti = 1.0 if self.calm else ease((age - 0.4) / 0.5)
        unit = clip(spec.get("unit", ""), 4)
        self.label(clip(spec.get("title", ""), 34), 8, 4, "dim", size=11, a=ti, spacing=1)
        self.label(clip(spec.get("subtitle", ""), 34), 8, 19, "soft", size=11, a=ti, spacing=1)
        every = 1 if ticks <= 4 else 2
        for d in range(ticks + 1):
            if d != ticks and (ticks - d) % every:
                continue
            tt = t0 + (t1 - t0) * d / ticks
            px, py = self.at((base @ np.array([*P(tt, lo), 1.0], np.float32))[:3])
            word = "NOW" if d == ticks else (hours(tt) if days >= 2 else time.strftime("%H:%M", time.localtime(tt / 1000)))
            self.label(word, px, py + 4, "muted", size=11, a=ti * 0.85, anchor="ct")
        if draw_in >= 1.0:
            px, py = self.at((base @ np.array([*end, 1.0], np.float32))[:3])
            self.label(f"{ys[-1]:.0f}{unit}", px - 8, py - 8, lc, size=13, weight="bold", a=ti, anchor="rb")
            if fc:
                self.label(clip(fc.get("label", ""), 16), px - 8, py - 26, "danger_text", size=11, a=ti, anchor="rb", spacing=1)
            self.region("point", px - 10, py - 10, 20, 20)
        if thr:
            px, py = self.at((base @ np.array([*P(t0 + (t1 - t0) * 0.02, thr["value"]), 1.0], np.float32))[:3])
            self.label(clip(thr.get("label", ""), 14), px, py - 3, "amber", size=11, a=ti * 0.9, anchor="lb", spacing=1)
        corners = [(base @ np.array([*P(tt, v), 1.0], np.float32))[:3] for tt in (t0, t1) for v in (lo, hi)]
        self.region_around("chart", corners)

    def p_orbit(self, t, fade, age, spec):
        people = [p for p in spec.get("people", []) if isinstance(p, dict)][:6]
        self.camera(dist=2.1, pitch=0.32, aim_y=0.2)
        self.table(t, fade, radius=0.18, power=0.7, cone_h=0.06)
        self.lines(ring(1.0, 0.12, 128), rgba("phosphor", 0.45 * fade, 0.6 * fade), np.diag([0.78, 1, 0.40, 1]).astype(np.float32), width=1.0)
        self.lines(ring(0.2, 0.0, 64), rgba("dim", 0.5 * fade, 0.5 * fade), width=1.0)
        spin = 0.0 if self.calm else t * 0.10
        placed = []
        for i, p in enumerate(people):
            a = math.pi / 2 + spin + 2 * math.pi * i / max(1, len(people))
            x, z = 0.78 * math.cos(a), 0.40 * math.sin(a)
            arrive = 1.0 if self.calm else ease((age - 0.2 * i) / 0.8)
            s = 0.30 * (0.8 + 0.2 * (z + 0.40) / 0.8)
            mm = hgl.translate(x - s / 2, 0.12 + 0.04 * (1 - arrive), z) @ hgl.scale(s)
            placed.append((z, i, p, mm, x, s, arrive))
        for z, i, p, mm, x, s, arrive in sorted(placed, key=lambda q: q[0]):
            bright = float(p.get("bright", 0.6))
            k = bright * (0.65 + 0.35 * (z + 0.40) / 0.8)
            path = p.get("portrait")
            drawn = bool(path) and self.card.draw(self.holo, path, mm, "phosphor" if bright > 0.4 else "dim",
                                                 a=fade * arrive * k * 1.25, round_=True, reveal=arrive, lines=70,
                                                 crop="square", calm=self.calm)
            cx, cy = self.at((mm @ np.array([0.5, 0.5, 0, 1.0], np.float32))[:3])
            if not drawn:
                self.lines(ring(0.34, 0, 48), rgba("phosphor" if bright > 0.4 else "dim", 1.2 * k * fade, fade * arrive),
                           mm @ hgl.translate(0.5, 0.5, 0) @ hgl.rot_x(math.pi / 2), width=1.2)
                initial = (str(p.get("name") or "?").strip() or "?")[0].upper()
                self.label(initial, cx, cy, "primary" if bright > 0.9 else "soft", size=16, weight="bold",
                           a=fade * arrive * min(1, k * 1.4), anchor="cm")
            self.lines([(x * 0.25, 0.0, z * 0.25), (x, 0.12, z)], rgba("dim", 0.5 * fade * k, 0.5 * fade), width=0.8)
            nm = "primary" if bright > 0.9 else ("soft" if bright > 0.4 else "dim")
            label = f"{clip(p.get('name', ''), 10).upper()} · {clip(p.get('ago', ''), 8)}"
            rr = abs(self.at((mm @ np.array([0.5, 0.84 if not drawn else 1.0, 0, 1.0], np.float32))[:3])[1] - cy)
            if z < 0:                       # behind: the name over the ring, clear of those in front
                self.label(label, cx, cy - rr - 3, nm, size=11, a=fade * arrive * min(1, k * 1.4), anchor="cb", spacing=1)
            else:
                self.label(label, cx, cy + rr + 3, nm, size=11, a=fade * arrive * min(1, k * 1.4), anchor="ct", spacing=1)
            r = s * 0.5 * self.h * 1.1
            self.region(f"person:{i}", cx - r, cy - r, 2 * r, 2 * r + 18)

    def p_media(self, t, fade, age, spec):
        self.camera(dist=2.1, pitch=0.42, aim_y=0.12, aim_x=0.3)
        self.table(t, fade, radius=0.42, power=0.6, cone_h=0.08)
        tilt = hgl.translate(0, 0.2, 0) @ hgl.rot_x(-1.05)
        paused = bool(spec.get("paused"))
        spin = hgl.rot_y(0.0 if (self.calm or paused) else t * 1.4)
        reel = list(ring(0.34, 0, 120)) + list(ring(0.30, 0, 120)) + list(ring(0.09, 0, 48)) + list(ring(0.05, 0, 32))
        for k in range(6):
            a = 2 * math.pi * k / 6
            reel += [(0.09 * math.cos(a), 0, 0.09 * math.sin(a)), (0.30 * math.cos(a + 0.12), 0, 0.30 * math.sin(a + 0.12))]
            reel += [(0.09 * math.cos(a + 0.4), 0, 0.09 * math.sin(a + 0.4)), (0.30 * math.cos(a + 0.5), 0, 0.30 * math.sin(a + 0.5))]
        self.lines(reel, rgba("phosphor", 0.85 * fade, fade), tilt @ spin, width=1.1)
        frac = max(0.0, min(1.0, float(spec.get("progress") or 0.0)))
        if frac > 0:
            self.lines(ring(0.40, 0, 96, -math.pi / 2, -math.pi / 2 + 2 * math.pi * frac), rgba("primary", 1.8 * fade, fade), tilt, width=2.2)
        self.lines(ring(0.40, 0, 96), rgba("dim", 0.5 * fade, 0.5 * fade), tilt, width=1.0)
        if not paused:
            eq = []
            for k in range(48):
                a = 2 * math.pi * k / 48
                v = 0.04 if self.calm else 0.02 + 0.06 * abs(math.sin(t * 7 + k * 0.9) * math.sin(t * 3.1 + k * 0.3))
                eq += [(0.44 * math.cos(a), 0, 0.44 * math.sin(a)), ((0.44 + v) * math.cos(a), 0, (0.44 + v) * math.sin(a))]
            self.lines(eq, rgba("cyan", 1.0 * fade, fade), tilt, width=1.2)
        self.region_around("reel", [(tilt @ np.array([0.46 * sx, 0, 0.46 * sz, 1.0], np.float32))[:3]
                                    for sx in (-1, 1) for sz in (-1, 1)])
        ti = 1.0 if self.calm else ease((age - 0.5) / 0.5)
        x = 262
        self.label(clip(spec.get("eyebrow", ""), 20).upper(), x, 34, "dim", size=11, a=ti, spacing=2)
        self.label(clip(spec.get("title", ""), 15).upper(), x, 50, "primary", size=16, weight="bold", a=ti, spacing=2)
        self.label(clip(spec.get("subtitle", ""), 20), x, 76, "white", size=12, a=ti)
        self.label(clip(spec.get("meta", ""), 24), x, 96, "muted", size=11, a=ti)
        self.region("words", x - 4, 30, self.w - x, 84)

    def p_weather(self, t, fade, age, spec):
        night = bool(spec.get("night"))
        self.camera(dist=2.1, pitch=0.42, aim_y=0.12, aim_x=0.3)
        self.table(t, fade, radius=0.48, power=0.45, cone_h=0.06)
        grid = []
        N = 22

        def hgt(x, z):
            return 0.06 * math.exp(-((x + 0.25) ** 2 + (z + 0.3) ** 2) * 6) + 0.10 * math.exp(-((x - 0.3) ** 2 + (z + 0.35) ** 2) * 9) + \
                0.012 * math.sin(x * 9) * math.cos(z * 7)
        reveal = 1.0 if self.calm else ease((age - 0.2) / 1.0)
        for i in range(N + 1):
            for j in range(N):
                for (a, b) in [((i, j), (i, j + 1)), ((j, i), (j + 1, i))]:
                    xa, za = -0.45 + 0.9 * a[0] / N, -0.45 + 0.9 * a[1] / N
                    xb, zb = -0.45 + 0.9 * b[0] / N, -0.45 + 0.9 * b[1] / N
                    if xa * xa + za * za > 0.2 or xb * xb + zb * zb > 0.2:
                        continue
                    if (za + 0.45) / 0.9 > reveal:
                        continue
                    grid += [(xa, hgt(xa, za) * reveal, za), (xb, hgt(xb, zb) * reveal, zb)]
        self.lines(grid, rgba("phosphor", 0.55 * fade, 0.8 * fade), width=0.9)
        track = []
        for k in range(48):
            a0, a1 = math.pi * k / 48, math.pi * (k + 1) / 48
            track += [(-0.5 * math.cos(a0), 0.05 + 0.42 * math.sin(a0), -0.15), (-0.5 * math.cos(a1), 0.05 + 0.42 * math.sin(a1), -0.15)]
        body = "soft" if night else "gold"
        self.lines(track, rgba(body, 0.5 * fade, 0.6 * fade), width=0.9)
        sun = max(0.0, min(1.0, float(spec.get("sun", 0.5))))
        sa = math.pi * sun
        sp = (-0.5 * math.cos(sa), 0.05 + 0.42 * math.sin(sa), -0.15)
        for r in (0.035, 0.05):
            pulse = 1.0 if self.calm else 1 + 0.08 * math.sin(t * 3)
            self.lines(ring(r * pulse, 0, 32), rgba(body, 1.6 * fade, fade), hgl.translate(*sp) @ hgl.rot_x(math.pi / 2), width=1.3)
        self.region_around("ground", [(sx * 0.45, 0.0, sz * 0.45) for sx in (-1, 1) for sz in (-1, 1)] + [sp])
        ti = 1.0 if self.calm else ease((age - 0.5) / 0.5)
        x = 262
        self.label(clip(spec.get("eyebrow", ""), 22).upper(), x, 32, "dim", size=11, a=ti, spacing=2)
        self.label(clip(spec.get("temp", ""), 8), x, 46, "primary", size=24, weight="bold", a=ti, spacing=2)
        self.label(clip(spec.get("words", ""), 24), x, 82, "white", size=12, a=ti)
        self.label(clip(spec.get("meta", ""), 24), x, 102, "muted", size=11, a=ti)
        self.region("words", x - 4, 30, self.w - x, 90)

    def p_list(self, t, fade, age, spec):
        rows = [r for r in spec.get("rows", []) if isinstance(r, dict)][:5]
        self.camera(dist=2.1, pitch=0.36, aim_y=0.18, aim_x=0.0)
        self.table(t, fade, radius=0.5, power=0.45, cone_h=0.08)
        ti = 1.0 if self.calm else ease((age - 0.2) / 0.5)
        self.label(clip(spec.get("title", ""), 34).upper(), 12, 6, "dim", size=11, a=ti, spacing=2)
        y = 28
        for i, r in enumerate(rows):
            ri = 1.0 if self.calm else ease((age - 0.25 - 0.12 * i) / 0.4)
            lc = level_colour(r.get("level", "ok"))
            self.label(clip(r.get("label", ""), 26), 18, y, "text", size=12, a=ri)
            self.label(clip(r.get("detail", ""), 18), self.w - 14, y, "muted", size=11, a=ri, anchor="rt")
            prog = r.get("progress")
            s = self.holo.scale
            x0, x1, yb = 18, self.w - 14, y + 19
            self.holo.line2d(x0 * s, yb * s, x1 * s, yb * s, col("dim", 1.0, 0.6 * ri), width=1.0)
            if isinstance(prog, (int, float)):
                xe = x0 + (x1 - x0) * max(0.0, min(1.0, prog))
                self.holo.line2d(x0 * s, yb * s, xe * s, yb * s, col(lc, 1.0, ri), width=2.0)
            elif not self.calm:            # working, no measure: a bead running along
                f = (t * 0.5 + i * 0.3) % 1.0
                xa = x0 + (x1 - x0) * f
                self.holo.line2d(xa * s, yb * s, min(x1, xa + 24) * s, yb * s, col(lc, 1.0, ri), width=2.0)
            self.region(f"row:{i}", 12, y - 2, self.w - 24, 26)
            y += 32

    def release(self):
        for _, gm in self.models.values():
            gm.delete()
        self.models.clear()
        if self.lab:
            self.lab[1].delete()
            self.lab = None
        self.card.release()
        if hasattr(self.holo, "release"):
            self.holo.release()
