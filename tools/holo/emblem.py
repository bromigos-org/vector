"""The burn-in as a 3D object, extruded from the canonical vectors (never redrawn).

Sources: the Wick theme's emblem kit (themes/wick/emblem: bromigos-core installs it in
/usr/share/bromigos/themes/wick/emblem): emblem-ring.svg (two ring lines, the motto
glyphs as outlines, the eleven-tick tuning gap) and emblem-flame.svg (the flame
outline and the four mast rectangles cut through it). The flame is extruded as a solid slab with the
mast cut clean through it; the mast itself is rebuilt as a thin recessed frame
inside the cut, so it can slide out on its own in an exploded view.

Units: SVG px / 1000, so the ring's outer diameter D = 0.99; y up, front +z,
the ring's lowest point sits at y = 0 so the emblem stands on the table.
"""
import math
import os
import re
import xml.etree.ElementTree as ET

import numpy as np
import trimesh
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union
from svgpathtools import parse_path

HERE = os.path.dirname(os.path.abspath(__file__))
BRAND = next((d for d in (os.environ.get("BROMIGOS_EMBLEM_DIR"),
                          "/usr/share/bromigos/themes/wick/emblem",
                          os.path.expanduser("~/github.com/bromigos-org/bromigOS/themes/wick/emblem"))
              if d and os.path.isfile(os.path.join(d, "emblem-ring.svg"))), "/usr/share/bromigos/themes/wick/emblem")
NS = "{http://www.w3.org/2000/svg}"


def _tf(s):
    """SVG transform list -> 3x3 matrix (only translate/scale/rotate are used here)."""
    M = np.eye(3)
    for fn, args in re.findall(r"(\w+)\(([^)]*)\)", s or ""):
        a = [float(v) for v in re.split(r"[ ,]+", args.strip()) if v]
        if fn == "translate":
            T = np.array([[1, 0, a[0]], [0, 1, a[1] if len(a) > 1 else 0], [0, 0, 1]])
        elif fn == "scale":
            sy = a[1] if len(a) > 1 else a[0]
            T = np.diag([a[0], sy, 1])
        elif fn == "rotate":
            r = math.radians(a[0])
            c, s_ = math.cos(r), math.sin(r)
            R = np.array([[c, -s_, 0], [s_, c, 0], [0, 0, 1]])
            if len(a) == 3:
                T = np.array([[1, 0, a[1]], [0, 1, a[2]], [0, 0, 1]]) @ R @ np.array([[1, 0, -a[1]], [0, 1, -a[2]], [0, 0, 1]])
            else:
                T = R
        else:
            continue
        M = M @ T
    return M


def _path_polys(d, M, per_seg=10):
    """Subpaths of an SVG path as point rings, transformed by M."""
    rings = []
    for sub in parse_path(d).continuous_subpaths():
        pts = []
        for seg in sub:
            for t in np.linspace(0, 1, per_seg, endpoint=False):
                z = seg.point(t)
                pts.append((z.real, z.imag))
        if len(pts) < 3:
            continue
        P = np.c_[np.array(pts), np.ones(len(pts))] @ M.T
        rings.append(P[:, :2])
    return rings


def _evenodd(rings):
    shape = None
    for r in rings:
        p = Polygon(r).buffer(0)
        if p.is_empty:
            continue
        shape = p if shape is None else shape.symmetric_difference(p)
    return shape


def _extrude(geom, z0, z1):
    out = []
    geoms = getattr(geom, "geoms", [geom])
    for g in geoms:
        if g.is_empty or g.area < 1e-3:
            continue
        m = trimesh.creation.extrude_polygon(g, z1 - z0)
        m.apply_translation([0, 0, z0])
        out.append(m)
    return out


def build(spec):
    ring = ET.parse(os.path.join(BRAND, "emblem-ring.svg")).getroot()
    flame = ET.parse(os.path.join(BRAND, "emblem-flame.svg")).getroot()
    pieces = []          # (trimesh, part index)
    pid = {p["id"]: i for i, p in enumerate(spec["parts"])}

    # ring lines: annuli from the circles
    for c in ring.iter(NS + "circle"):
        if c.get("stroke"):
            cx, cy, r, w = (float(c.get(k)) for k in ("cx", "cy", "r", "stroke-width"))
            ann = Polygon(LineString([(cx + (r + w / 2) * math.cos(t), cy + (r + w / 2) * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 241)]).coords).difference(
                Polygon([(cx + (r - w / 2) * math.cos(t), cy + (r - w / 2) * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 241)]))
            pieces += [(m, pid["ring"]) for m in _extrude(ann, -14, 14)]
    # motto glyphs (outlined text, each with its own rotation about the centre)
    for p in ring.iter(NS + "path"):
        if not (p.get("id") or "").startswith("text"):
            continue
        g = _evenodd(_path_polys(p.get("d"), _tf(p.get("transform")), per_seg=6))
        if g is not None:
            pieces += [(m, pid["ring"]) for m in _extrude(g, -8, 8)]
    # tuning gap ticks
    for ln in ring.iter(NS + "line"):
        x1, y1, x2, y2 = (float(ln.get(k)) for k in ("x1", "y1", "x2", "y2"))
        w = float(ln.get("stroke-width") or 6)
        g = LineString([(x1, y1), (x2, y2)]).buffer(w / 2, cap_style=2)
        pieces += [(m, pid["ticks"]) for m in _extrude(g, -10, 10)]
    # the flame, with the mast cut through it
    fp = next(p for p in flame.iter(NS + "path") if p.get("fill") == "#000000")
    fl = _evenodd(_path_polys(fp.get("d"), _tf(fp.get("transform")), per_seg=8))
    mask = flame.find(f".//{NS}mask")
    cut = unary_union([box(float(r.get("x")), float(r.get("y")), float(r.get("x")) + float(r.get("width")),
                           float(r.get("y")) + float(r.get("height")))
                       for r in mask.iter(NS + "rect") if r.get("fill") == "black"])
    pieces += [(m, pid["flame"]) for m in _extrude(fl.difference(cut), -26, 26)]
    # the first relay: a thin frame inside the cut, recessed
    mast = cut.intersection(fl).buffer(-3, join_style=2)
    pieces += [(m, pid["mast"]) for m in _extrude(mast, -6, 6)]

    meshes, parts = [], []
    for m, i in pieces:
        meshes.append(m)
        parts.append(np.full(len(m.faces), i, np.int32))
    m = trimesh.util.concatenate(meshes)
    face_part = np.concatenate(parts)
    # SVG (y down, px) -> world (y up, D ~ 1), ring bottom on the table
    v = m.vertices.copy()
    v[:, 0] = (v[:, 0] - 500) / 1000
    v[:, 1] = (500 - v[:, 1]) / 1000 + 0.5
    v[:, 2] = v[:, 2] / 1000
    m = trimesh.Trimesh(vertices=v, faces=m.faces, process=False)
    return m, face_part
