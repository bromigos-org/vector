"""Procedural hologram models from primitives: no credits, exact, editable. A manifest
model with "builder": "prims" lists "shapes"; each shape belongs to a part (by id) and
bake.py does the rest (feature edges, per-part vertices, explode offsets, the .holo.npz).

Units are free: the model is normalised afterwards (base on y = 0, tallest extent 1,
centred on x/z, y up, front +z). Angles are degrees.

Shapes (all take "part", optional "at": [x, y, z] and "rotate": [rx, ry, rz], applied
rotate-then-move, and "segments"):
  {"type": "box", "size": [x, y, z]}
  {"type": "cylinder", "r": 1, "h": 2, "axis": "y"}           # centred on "at"
  {"type": "cone", "r": 1, "h": 2, "axis": "y"}               # base at "at", tip up the axis
  {"type": "frustum", "r0": 1, "r1": 0.5, "h": 2, "axis": "y"} # base r0 at "at", top r1
  {"type": "sphere", "r": 1}
  {"type": "torus", "R": 1, "r": 0.1, "axis": "y"}            # ring around the axis
  {"type": "dish", "r": 1, "depth": 0.3, "thickness": 0.03}    # paraboloid bowl opening up +y
  {"type": "lathe", "profile": [[r, y], ...]}                  # revolved about +y
  {"type": "tube", "a": [x, y, z], "b": [x, y, z], "r": 0.05}  # a strut between two points
  {"type": "lattice", "a": [...], "b": [...], "w": 0.3, "r": 0.02, "bays": 6}  # a truss mast
"""
import math

import numpy as np
import trimesh
from trimesh import creation as C
from trimesh import transformations as T

AXIS = {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}


def _orient(m, axis):
    """creation.* builds along +z; turn that onto the requested axis."""
    if axis == "z":
        return m
    if axis == "y":
        m.apply_transform(T.rotation_matrix(-math.pi / 2, [1, 0, 0]))
    elif axis == "x":
        m.apply_transform(T.rotation_matrix(math.pi / 2, [0, 1, 0]))
    return m


def _tube(a, b, r, seg=12):
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = b - a
    L = float(np.linalg.norm(d))
    if L < 1e-9:
        return None
    m = C.cylinder(radius=r, height=L, sections=seg)
    m.apply_transform(trimesh.geometry.align_vectors([0, 0, 1], d / L))
    m.apply_translation((a + b) / 2)
    return m


def shape(s):
    t = s["type"]
    seg = int(s.get("segments", 32))
    if t == "box":
        m = C.box(extents=s["size"])
    elif t == "cylinder":
        m = _orient(C.cylinder(radius=s["r"], height=s["h"], sections=seg), s.get("axis", "y"))
    elif t == "cone":
        m = C.cone(radius=s["r"], height=s["h"], sections=seg)
        m = _orient(m, s.get("axis", "y"))
    elif t == "frustum":
        prof = [[0, 0], [s["r0"], 0], [s["r1"], s["h"]], [0, s["h"]]]
        m = C.revolve(np.asarray(prof, float), sections=seg)
        m = _orient(m, s.get("axis", "y"))
    elif t == "sphere":
        m = C.icosphere(subdivisions=int(s.get("subdivisions", 2)), radius=s["r"])
    elif t == "torus":
        m = _orient(C.torus(major_radius=s["R"], minor_radius=s["r"], major_sections=seg,
                            minor_sections=int(s.get("minor_segments", 12))), s.get("axis", "y"))
    elif t == "dish":
        r, dep, th = float(s["r"]), float(s["depth"]), float(s.get("thickness", 0.03 * s["r"]))
        n = int(s.get("rings", 10))
        outer = [[r * i / n, dep * (i / n) ** 2] for i in range(n + 1)]
        inner = [[max(0.0, r * i / n - th), dep * (i / n) ** 2 + th] for i in range(n, -1, -1)]
        m = C.revolve(np.asarray(outer + inner, float), sections=seg)
        m = _orient(m, "y")
    elif t == "lathe":
        m = _orient(C.revolve(np.asarray(s["profile"], float), sections=seg), "y")
    elif t == "tube":
        m = _tube(s["a"], s["b"], s["r"], int(s.get("segments", 12)))
    elif t == "lattice":
        a, b = np.asarray(s["a"], float), np.asarray(s["b"], float)
        w, r, bays = float(s["w"]), float(s["r"]), int(s.get("bays", 6))
        d = (b - a) / max(np.linalg.norm(b - a), 1e-9)
        up = np.array([0, 0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0, 0])
        u = np.cross(d, up)
        u /= np.linalg.norm(u)
        v = np.cross(d, u)
        corners = [(u + v) * w / 2, (u - v) * w / 2, (-u - v) * w / 2, (-u + v) * w / 2]
        parts = []
        for c in corners:                                      # four legs
            parts.append(_tube(a + c, b + c, r))
        for k in range(bays + 1):                              # rungs and diagonals
            p0 = a + (b - a) * k / bays
            for i in range(4):
                parts.append(_tube(p0 + corners[i], p0 + corners[(i + 1) % 4], r * 0.7))
                if k < bays:
                    p1 = a + (b - a) * (k + 1) / bays
                    parts.append(_tube(p0 + corners[i], p1 + corners[(i + 1) % 4], r * 0.6))
        m = trimesh.util.concatenate([p for p in parts if p is not None])
    else:
        raise ValueError(f"unknown shape type {t!r}")
    if m is None:
        raise ValueError(f"degenerate {t}")
    rx, ry, rz = (math.radians(x) for x in s.get("rotate", [0, 0, 0]))
    if rx or ry or rz:
        m.apply_transform(T.euler_matrix(rx, ry, rz, "sxyz"))
    if s.get("at"):
        m.apply_translation(s["at"])
    return m


def build(spec):
    pid = {p["id"]: i for i, p in enumerate(spec["parts"])}
    meshes, labels = [], []
    for s in spec["shapes"]:
        if s.get("part") not in pid:
            raise ValueError(f"shape {s.get('type')} names part {s.get('part')!r}, not in parts {list(pid)}")
        m = shape(s)
        meshes.append(m)
        labels.append(np.full(len(m.faces), pid[s["part"]], np.int32))
    m = trimesh.util.concatenate(meshes)
    face_part = np.concatenate(labels)
    m = trimesh.Trimesh(vertices=m.vertices, faces=m.faces, process=False)
    lo, hi = m.bounds
    c = (lo + hi) / 2
    m.apply_translation([-c[0], -lo[1], -c[2]])
    m.apply_scale(1.0 / float(max(m.extents)))
    return m, face_part
