"""Text for the hologram: Pango lays it out (Geist Mono), cairo rasterises it once into
an alpha texture, GL tints and draws it. Textures are cached by content and evicted
when unused for a while, so steady labels cost nothing per frame."""
import time

import cairo
import gi
import numpy as np

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402
from OpenGL import GL  # noqa: E402

from . import gl  # noqa: E402

FONT = "Geist Mono"


class Tex:
    __slots__ = ("tex", "w", "h", "used", "lines")


class TextCache:
    def __init__(self, scale=1.0):
        self.scale = scale
        self.items = {}
        self._probe = cairo.ImageSurface(cairo.FORMAT_A8, 4, 4)

    def _layout(self, cr, text, size, weight, width, markup, spacing):
        lay = PangoCairo.create_layout(cr)
        fd = Pango.FontDescription(f"{FONT} {size * self.scale:.1f}px")
        fd.set_weight(Pango.Weight.BOLD if weight == "bold" else Pango.Weight.MEDIUM if weight == "medium" else Pango.Weight.NORMAL)
        lay.set_font_description(fd)
        if spacing:
            attrs = Pango.AttrList()
            attrs.insert(Pango.attr_letter_spacing_new(int(spacing * Pango.SCALE * self.scale)))
            lay.set_attributes(attrs)
        if width:
            lay.set_width(int(width * self.scale * Pango.SCALE))
            lay.set_wrap(Pango.WrapMode.WORD_CHAR)
        if markup:
            lay.set_markup(text, -1)
        else:
            lay.set_text(text, -1)
        return lay

    def get(self, text, size=14, weight="normal", width=None, markup=False, spacing=0.0):
        key = (text, size, weight, width, markup, spacing)
        t = self.items.get(key)
        if t is not None:
            t.used = time.monotonic()
            return t
        cr = cairo.Context(self._probe)
        lay = self._layout(cr, text, size, weight, width, markup, spacing)
        _, log = lay.get_pixel_extents()
        w, h = max(1, log.width + 4), max(1, log.height + 2)
        surf = cairo.ImageSurface(cairo.FORMAT_A8, w, h)
        cr = cairo.Context(surf)
        lay = self._layout(cr, text, size, weight, width, markup, spacing)
        cr.move_to(2 - log.x, 1 - log.y)
        PangoCairo.show_layout(cr, lay)
        surf.flush()
        stride = surf.get_stride()
        a = np.frombuffer(surf.get_data(), np.uint8).reshape(h, stride)[:, :w]
        rgba = np.zeros((h, w, 4), np.uint8)
        rgba[..., 3] = a
        rgba[..., :3] = 255
        t = Tex()
        t.tex = gl.texture(w, h, np.ascontiguousarray(rgba))
        t.w, t.h = w, h
        t.used = time.monotonic()
        t.lines = lay.get_line_count()
        self.items[key] = t
        return t

    def gc(self, idle=20.0):
        now = time.monotonic()
        dead = [k for k, t in self.items.items() if now - t.used > idle]
        for k in dead:
            GL.glDeleteTextures([self.items.pop(k).tex])

    def clear(self):
        for t in self.items.values():
            GL.glDeleteTextures([t.tex])
        self.items.clear()
