"""The shared hologram renderer. One `Holo` per GL context draws:

  * the projection table: emitter bed with rotating rings, a light cone rising to the model;
  * part-indexed models (baked .holo or VECTOR's procedural construct): a depth-only
    prepass, a faint fresnel shell with topographic slices and the scan band, then fat
    anti-aliased feature edges (bright in front, ghosted behind);
  * free 3D lines (rings, leader stubs) and a 2D overlay (leader lines, label boxes, text);
  * bloom (quarter-res separable blur) and one tone-mapped composite onto the window.

Per-part state is three uniform arrays: a 4x4 matrix (explode offsets, the construct's
moving pieces), an rgba colour (status colour x brightness, alpha = isolation) and a
line-width factor. Geometry never changes after upload; all motion is uniforms.
"""
import contextlib
import math
import os
import sys

import numpy as np
from OpenGL import GL

from . import gl, shaders
from .text import TextCache

from . import paths  # noqa: E402,F401  (the shared libs on sys.path)
import bromigos_theme as T  # noqa: E402

# The Wick's names for the colours; col() and lin() take them from the theme in force
# (bromigos_theme: phosphor is the theme's primary, amber its warn, …).
PAL = {
    "void": (0x00, 0x05, 0x00), "panel": (0x00, 0x13, 0x00), "guard": (0x00, 0x3b, 0x00),
    "phosphor": (0x39, 0xff, 0x14), "soft": (0x9c, 0xff, 0x8a), "dim": (0x15, 0x9b, 0x09),
    "amber": (0xd4, 0xaf, 0x37), "danger": (0xff, 0x76, 0x6f), "static": (0x7e, 0x92, 0x7e),
    "white": (0xe8, 0xff, 0xe0), "text": (0xc4, 0xf5, 0xbb), "cyan": (0x3f, 0xe0, 0xc5),
    "gold": (0xf0, 0xd3, 0x6a), "rust": (0x4a, 0x0e, 0x0e),
}


# A world's colours (holo/worlds.py Palette) the gallery draws one frame in, instead of
# the theme in force: set only inside `with tinted(palette):`.
_world = None


@contextlib.contextmanager
def tinted(palette):
    """col(), lin() and wick() answer in `palette` (a world's colours) inside the block;
    None changes nothing (the theme in force)."""
    global _world
    prev, _world = _world, palette
    try:
        yield
    finally:
        _world = prev


def _pal():
    return T if _world is None else _world


def col(name, k=1.0, a=1.0):
    r, g, b = _pal().rgb255(name)
    return (r / 255 * k, g / 255 * k, b / 255 * k, a)


def lin(name, k=1.0):
    """Palette colour in linear light (the scene is linear HDR)."""
    return tuple(((c / 255.0) ** 2.2) * k for c in _pal().rgb255(name))


def wick(rgba):
    """A colour written in the Wick's values (a backing, a clear colour), in the theme in force
    (or the world tinted() names)."""
    return _pal().retint_rgb(rgba)


LEVEL_COL = {"ok": "phosphor", "warn": "amber", "crit": "danger", "off": "static", "info": "soft"}


class GpuModel:
    """A part-indexed mesh on the GPU: pos, nrm, part + tri and edge index buffers."""

    def __init__(self, pos, nrm, part, tri, edge, parts, meta=None):
        self.mesh = gl.Mesh([(0, pos), (1, nrm), (2, part.astype(np.float32))],
                            {"tri": tri, "edge": edge})
        self.pos, self.part, self.tri = pos, part, tri
        self.parts = parts
        self.n = len(parts)
        self.meta = meta or {}

    @classmethod
    def from_holo(cls, m):
        return cls(m.pos, m.nrm, m.part, m.tri, m.edge, m.parts, m.meta)

    def delete(self):
        self.mesh.delete()


class Holo:
    def __init__(self, scale=1.0):
        self.scale = scale
        self.p_line = gl.Program(shaders.PART_VS, shaders.LINE_FS, shaders.LINE_GS)
        self.p_free = gl.Program(shaders.FREE_VS, shaders.LINE_FS, shaders.LINE_GS)
        self.p_fill = gl.Program(shaders.FILL_VS, shaders.FILL_FS)
        self.p_disc = gl.Program(shaders.DISC_VS, shaders.DISC_FS)
        self.p_cone = gl.Program(shaders.CONE_VS, shaders.CONE_FS)
        self.p_quad = gl.Program(shaders.QUAD_VS, shaders.QUAD_FS)
        self.p_down = gl.Program(shaders.POST_VS, shaders.DOWN_FS)
        self.p_blur = gl.Program(shaders.POST_VS, shaders.BLUR_FS)
        self.p_comp = gl.Program(shaders.POST_VS, shaders.COMP_FS)
        self.empty_vao = GL.glGenVertexArrays(1)
        # table geometry
        q = np.array([[-1, 0, -1], [1, 0, -1], [1, 0, 1], [-1, 0, -1], [1, 0, 1], [-1, 0, 1]], np.float32)
        self.disc = gl.Mesh([(0, q)])
        n = 96
        a = np.linspace(0, 2 * np.pi, n + 1)
        ring = np.stack([np.cos(a), np.zeros_like(a), np.sin(a)], 1)
        top = ring.copy()
        top[:, 1] = 1
        strip = np.empty((2 * (n + 1), 3), np.float32)
        strip[0::2], strip[1::2] = ring, top
        self.cone = gl.Mesh([(0, strip)])
        self.free = gl.Mesh([(0, np.zeros((2, 3))), (1, np.zeros((2, 4)))], dynamic=True)
        self.quad = gl.Mesh([(0, np.zeros((6, 2))), (1, np.zeros((6, 2)))], dynamic=True)
        self.text = TextCache(scale)
        self.size = None
        self.ms = self.half = self.qa = self.qb = None
        self.vp = np.eye(4, dtype=np.float32)
        self.eye = np.zeros(3, np.float32)
        self.t = 0.0
        self.calm = False      # reduced motion (holo/motion.py): the table's light holds steady
        self._lines2d = []     # batched 2D segments: (x0, y0, x1, y1, rgba)
        self._quads = []       # deferred text/rect draws

    # ------------------------------------------------------------------ frame
    def begin(self, w, h, t, calm=False):
        """A frame. t: the scene's animation seconds (its motion clock); calm: anything but
        full motion (no flicker or sweeping beam on the table)."""
        self.t = t
        self.calm = calm
        if self.size != (w, h):
            for tgt in (self.ms, self.half, self.qa, self.qb):
                if tgt:
                    tgt.delete()
            self.ms = gl.MSTarget(w, h, 4)
            self.half = gl.Target(max(1, w // 2), max(1, h // 2))
            self.qa = gl.Target(max(1, w // 4), max(1, h // 4))
            self.qb = gl.Target(max(1, w // 4), max(1, h // 4))
            self.size = (w, h)
        self.ms.bind()
        GL.glClearColor(0, 0, 0, 0)
        GL.glClearDepth(1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE)
        GL.glDisable(GL.GL_CULL_FACE)

    def camera(self, eye, target, fov, near=0.05, far=20.0, viewport=None):
        """Set the camera. viewport (x, y, w, h) in pixels, top-left origin, restricts
        drawing to a sub-rectangle of the window (VECTOR's table pane)."""
        W, H = self.size
        vx, vy, vw, vh = viewport or (0, 0, W, H)
        self.view_rect = (vx, vy, vw, vh)
        GL.glViewport(int(vx), int(H - vy - vh), int(vw), int(vh))
        self.proj = gl.perspective(fov, vw / max(vh, 1), near, far)
        self.view = gl.look_at(eye, target)
        self.vp = self.proj @ self.view
        self.eye = np.asarray(eye, np.float32)

    def project(self, pts):
        """World points (N,3) -> window pixels (N,2) top-left origin, and depth (N)."""
        pts = np.asarray(pts, np.float32).reshape(-1, 3)
        h = np.c_[pts, np.ones(len(pts), np.float32)] @ self.vp.T
        w = np.where(np.abs(h[:, 3]) < 1e-6, 1e-6, h[:, 3])
        ndc = h[:, :3] / w[:, None]
        vx, vy, vw, vh = self.view_rect
        sx = vx + (ndc[:, 0] * 0.5 + 0.5) * vw
        sy = vy + (1 - (ndc[:, 1] * 0.5 + 0.5)) * vh
        return np.stack([sx, sy], 1), ndc[:, 2]

    # ------------------------------------------------------------------ table
    def draw_table(self, model_mat, radius=0.62, cone_top=0.5, cone_h=0.9, power=1.0, colour="phosphor"):
        GL.glDepthMask(False)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDepthFunc(GL.GL_LEQUAL)
        c = lin(colour) if isinstance(colour, str) else tuple(float(x) for x in colour[:3])
        p = self.p_disc.use()
        p.m4("u_vp", self.vp)
        p.m4("u_model", model_mat @ gl.scale(radius * 1.25))
        p.f("u_col", *c)
        p.f("u_time", self.t)
        p.f("u_r", 1 / 1.25)
        p.f("u_power", power * 1.6)
        p.f("u_calm", 1.0 if self.calm else 0.0)
        self.disc.draw(GL.GL_TRIANGLES)
        p = self.p_cone.use()
        p.m4("u_vp", self.vp)
        p.m4("u_model", model_mat)
        p.f("u_col", *c)
        p.f("u_time", self.t)
        p.f("u_power", power)
        p.f("u_r0", radius * 0.92)
        p.f("u_r1", cone_top)
        p.f("u_h", cone_h)
        p.f("u_calm", 1.0 if self.calm else 0.0)
        self.cone.draw(GL.GL_TRIANGLE_STRIP)
        GL.glDepthMask(True)

    # ------------------------------------------------------------------ models
    def draw_model(self, gm, model_mat, pmats, pcols, pws, scan=(0, 0.02, 0), fill=1.0,
                   width=1.3, slices=48.0, ghost=0.22):
        n = shaders.MAXP
        pm = np.tile(np.eye(4, dtype=np.float32), (n, 1, 1))
        pc = np.zeros((n, 4), np.float32)
        pw = np.ones(n, np.float32)
        k = min(len(pmats), n)
        pm[:k], pc[:k], pw[:k] = pmats[:k], pcols[:k], pws[:k]
        W, H = self.size
        vx, vy, vw, vh = self.view_rect
        GL.glEnable(GL.GL_DEPTH_TEST)
        # 1. depth prepass (pushed back so edges on the surface win)
        GL.glColorMask(False, False, False, False)
        GL.glDepthMask(True)
        GL.glDepthFunc(GL.GL_LESS)
        GL.glEnable(GL.GL_POLYGON_OFFSET_FILL)
        GL.glPolygonOffset(1.5, 2.0)
        p = self.p_fill.use()
        p.m4("u_vp", self.vp)
        p.m4("u_model", model_mat)
        p.m4v("u_pmat", pm)
        p.v4v("u_pcol", pc)
        gm.mesh.draw(GL.GL_TRIANGLES, "tri")
        GL.glColorMask(True, True, True, True)
        # 2. the shell, front-most surface only
        GL.glDepthMask(False)
        GL.glDepthFunc(GL.GL_LEQUAL)
        p.f("u_eye", *self.eye)
        p.f("u_scan", *scan, 0.0)
        p.f("u_fill", fill)
        p.f("u_slices", slices)
        gm.mesh.draw(GL.GL_TRIANGLES, "tri")
        GL.glDisable(GL.GL_POLYGON_OFFSET_FILL)
        # 3. edges: visible, then hidden ones ghosted
        p = self.p_line.use()
        p.m4("u_vp", self.vp)
        p.m4("u_model", model_mat)
        p.m4v("u_pmat", pm)
        p.v4v("u_pcol", pc)
        GL.glUniform1fv(p.loc("u_pw"), n, pw)
        p.f("u_px", float(vw), float(vh))
        p.f("u_width", width * self.scale)
        p.f("u_scan", *scan, 0.0)
        p.f("u_gain", 1.0)
        gm.mesh.draw(GL.GL_LINES, "edge")
        if ghost > 0:
            GL.glDepthFunc(GL.GL_GREATER)
            p.f("u_gain", ghost)
            gm.mesh.draw(GL.GL_LINES, "edge")
        GL.glDepthFunc(GL.GL_LEQUAL)
        GL.glDepthMask(True)

    def draw_lines3d(self, pts, cols, model_mat=None, width=1.2, depth=True):
        """Free 3D segments: pts (2N,3), cols (2N,4) linear rgb + alpha."""
        if len(pts) < 2:
            return
        self.free.update([pts, cols])
        p = self.p_free.use()
        p.m4("u_vp", self.vp)
        p.m4("u_model", np.eye(4, dtype=np.float32) if model_mat is None else model_mat)
        vx, vy, vw, vh = self.view_rect
        p.f("u_px", float(vw), float(vh))
        p.f("u_width", width * self.scale)
        p.f("u_scan", 0, 1, 0, 0)
        p.f("u_gain", 1.0)
        if depth:
            GL.glEnable(GL.GL_DEPTH_TEST)
        else:
            GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDepthMask(False)
        self.free.draw(GL.GL_LINES)
        GL.glDepthMask(True)

    # ------------------------------------------------------------------ 2D overlay (window pixels)
    def line2d(self, x0, y0, x1, y1, rgba, width=1.2):
        self._lines2d.append((x0, y0, x1, y1, rgba, width))

    def poly2d(self, pts, rgba, width=1.2, closed=False):
        pts = list(pts)
        if closed:
            pts.append(pts[0])
        for a, b in zip(pts, pts[1:]):
            self.line2d(a[0], a[1], b[0], b[1], rgba, width)

    def brackets(self, x, y, w, h, rgba, arm=10, width=1.2):
        for (cx, cy, dx, dy) in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
            self.line2d(cx, cy, cx + dx * arm, cy, rgba, width)
            self.line2d(cx, cy, cx, cy + dy * arm, rgba, width)

    def rect(self, x, y, w, h, rgba):
        self._quads.append(("rect", x, y, w, h, rgba, None))

    def label(self, text, x, y, rgba, size=14, weight="normal", width=None, markup=False,
              anchor="lt", spacing=0.0):
        """Queue text; returns (w, h) in pixels. anchor: l/c/r + t/m/b."""
        t = self.text.get(text, size, weight, width, markup, spacing)
        if anchor[0] == "c":
            x -= t.w / 2
        elif anchor[0] == "r":
            x -= t.w
        if anchor[1] == "m":
            y -= t.h / 2
        elif anchor[1] == "b":
            y -= t.h
        self._quads.append(("tex", x, y, t.w, t.h, rgba, t.tex))
        return t.w, t.h

    def measure(self, text, size=14, weight="normal", width=None, markup=False, spacing=0.0):
        t = self.text.get(text, size, weight, width, markup, spacing)
        return t.w, t.h

    def _flush_overlay(self, W, H):
        GL.glViewport(0, 0, W, H)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)
        vp = gl.ortho(0, W, H, 0)
        # rects first (label backgrounds), then lines, then text
        p = self.p_quad.use()
        p.m4("u_vp", vp)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        p.i("u_tex", 0)
        for kind, x, y, w, h, rgba, tex in [q for q in self._quads if q[0] == "rect"]:
            self._quad(p, x, y, w, h, rgba, None)
        if self._lines2d:
            by_w = {}
            for x0, y0, x1, y1, c, wd in self._lines2d:
                by_w.setdefault(wd, []).append((x0, y0, x1, y1, c))
            lp = self.p_free.use()
            lp.m4("u_vp", vp)
            lp.m4("u_model", np.eye(4, dtype=np.float32))
            lp.f("u_px", float(W), float(H))
            lp.f("u_scan", 0, 1, 0, 0)
            lp.f("u_gain", 1.0)
            for wd, segs in by_w.items():
                pts = np.zeros((2 * len(segs), 3), np.float32)
                cs = np.zeros((2 * len(segs), 4), np.float32)
                for i, (x0, y0, x1, y1, c) in enumerate(segs):
                    pts[2 * i] = (x0, y0, 0)
                    pts[2 * i + 1] = (x1, y1, 0)
                    cs[2 * i] = cs[2 * i + 1] = c
                lp.f("u_width", wd * self.scale)
                self.free.update([pts, cs])
                self.free.draw(GL.GL_LINES)
        p = self.p_quad.use()
        for kind, x, y, w, h, rgba, tex in [q for q in self._quads if q[0] == "tex"]:
            self._quad(p, x, y, w, h, rgba, tex)
        self._lines2d.clear()
        self._quads.clear()

    def _quad(self, p, x, y, w, h, rgba, tex):
        x, y = round(x), round(y)
        v = np.array([[x, y], [x + w, y], [x + w, y + h], [x, y], [x + w, y + h], [x, y + h]], np.float32)
        uv = np.array([[0, 0], [1, 0], [1, 1], [0, 0], [1, 1], [0, 1]], np.float32)
        self.quad.update([v, uv])
        p.f("u_col", *rgba)
        p.f("u_mode", 0.0 if tex else 1.0)
        if tex:
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        self.quad.draw(GL.GL_TRIANGLES)

    # ------------------------------------------------------------------ post
    def end(self, out_fbo, bg=None, bloom=1.0, fade=1.0):
        bg = wick((0.0, 0.02, 0.0, 0.0)) if bg is None else bg
        W, H = self.size
        src = self.ms.resolve()
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_BLEND)
        GL.glBindVertexArray(self.empty_vao)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        # downsample: full -> half -> quarter
        self.half.bind()
        p = self.p_down.use()
        p.i("u_tex", 0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, src.tex)
        p.f("u_texel", 1.0 / W, 1.0 / H)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        self.qa.bind()
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.half.tex)
        p.f("u_texel", 1.0 / self.half.w, 1.0 / self.half.h)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        p = self.p_blur.use()
        p.i("u_tex", 0)
        for _ in range(2):
            self.qb.bind()
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.qa.tex)
            p.f("u_dir", 1.0 / self.qa.w, 0.0)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
            self.qa.bind()
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.qb.tex)
            p.f("u_dir", 0.0, 1.0 / self.qa.h)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, out_fbo)
        GL.glViewport(0, 0, W, H)
        GL.glClearColor(0, 0, 0, 0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        p = self.p_comp.use()
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, src.tex)
        p.i("u_scene", 0)
        GL.glActiveTexture(GL.GL_TEXTURE1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.qa.tex)
        p.i("u_bloom", 1)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        p.f("u_bloom_k", bloom)
        p.f("u_bg", *bg)
        p.f("u_fade", fade)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        self._flush_overlay(W, H)
        self.text.gc()


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def approach(cur, target, dt, rate):
    """Exponential approach, frame-rate independent."""
    return target + (cur - target) * math.exp(-rate * dt)
