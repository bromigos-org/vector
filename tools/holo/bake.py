#!/usr/bin/env python3
"""Bake GLB models into the compact hologram format (.holo.npz) the renderers read.

    ~/.local/share/bromigos/venv/bin/python bake.py [--root DIR] [name ...]   # names from DIR/manifest.json
    ... bake.py --preview name                                    # also write previews/<name>-parts.png

DIR is a hologram collection (collection.py): the user's own by default.

Pipeline per model (all offline; the desktop only ever reads the .holo.npz):
  1. load the GLB, merge every mesh, weld vertices;
  2. quadric-decimate to `faces` triangles (fast-simplification);
  3. normalise: centre on X/Z, base at y = 0, tallest extent = 1 (y up, front = +z);
  4. segment into named parts: each face goes to the first manifest box (fractions of
     the bounding box) that holds its centroid, else to the part marked "rest";
     generated meshes come out as one fused shell, so parts are regions, not objects;
  5. split vertices per part so parts can explode apart without tearing;
  6. feature edges: boundary edges and edges whose dihedral angle exceeds `edge_deg`
     (raised automatically until the count fits `max_edges`), plus every part border;
  7. write arrays + a JSON header (README.md in a collection, "The .holo format").

Built models are procedural too (`"builder": "emblem"`): see emblem.py.
"""
import argparse
import hashlib
import json
import os
import sys
import time

import numpy as np
import trimesh

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import collection  # noqa: E402

ROOT = OUT = collection.resolve()       # the collection: manifest.json, src/, and the baked files
FORMAT = "bromigos-holo/1"


def use(root):
    """Work on another collection (--root; make-holo.py passes its own)."""
    global ROOT, OUT
    ROOT = OUT = collection.resolve(root)


def load_mesh(path):
    s = trimesh.load(path, force="scene")
    m = s.to_geometry() if hasattr(s, "to_geometry") else s.dump(concatenate=True)
    m = trimesh.Trimesh(vertices=m.vertices, faces=m.faces, process=True)
    m.merge_vertices()
    return m


def decimate(m, faces):
    if len(m.faces) <= faces:
        return m
    import fast_simplification
    v, f = fast_simplification.simplify(np.asarray(m.vertices, np.float32), np.asarray(m.faces, np.int32),
                                        target_reduction=1.0 - faces / len(m.faces), agg=6)
    out = trimesh.Trimesh(vertices=v, faces=f, process=True)
    out.remove_unreferenced_vertices()
    return out


def normalise(m, rot_y_deg=0.0):
    if rot_y_deg:
        m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(rot_y_deg), [0, 1, 0]))
    lo, hi = m.bounds
    c = (lo + hi) / 2
    m.apply_translation([-c[0], -lo[1], -c[2]])
    m.apply_scale(1.0 / float(max(m.extents)))
    return m


def segment(m, parts):
    lo, hi = m.bounds
    span = np.maximum(hi - lo, 1e-9)
    cen = (m.triangles_center - lo) / span          # face centroid in bbox fractions
    lab = np.full(len(m.faces), -1, np.int32)
    rest = next((i for i, p in enumerate(parts) if p.get("rest")), len(parts) - 1)
    for i, p in enumerate(parts):
        for box in p.get("boxes", []):
            x0, x1, y0, y1, z0, z1 = box
            hit = (lab < 0) & (cen[:, 0] >= x0) & (cen[:, 0] <= x1) & (cen[:, 1] >= y0) & \
                  (cen[:, 1] <= y1) & (cen[:, 2] >= z0) & (cen[:, 2] <= z1)
            lab[hit] = i
    lab[lab < 0] = rest
    return lab


def feature_edges(m, face_part, edge_deg, max_edges):
    adj = m.face_adjacency                    # (A, 2) face pairs
    adj_e = m.face_adjacency_edges            # (A, 2) vertex pairs
    ang = np.degrees(m.face_adjacency_angles)
    border = face_part[adj[:, 0]] != face_part[adj[:, 1]]
    deg = edge_deg
    while True:
        sel = (ang >= deg) | border
        if sel.sum() <= max_edges or deg >= 89:
            break
        deg += 2
    edges = adj_e[sel]
    # an edge belongs to the part of its first face; border edges show on both sides
    epart = face_part[adj[sel, 0]]
    b2 = border & sel
    extra = adj_e[b2]
    extra_part = face_part[adj[b2, 1]]
    # mesh boundary (holes / open shells)
    ue = m.edges_sorted
    uniq, inv, cnt = np.unique(ue, axis=0, return_inverse=True, return_counts=True)
    bnd_mask = cnt[inv.ravel()] == 1
    bnd = ue[bnd_mask]
    bnd_part = face_part[np.repeat(np.arange(len(m.faces)), 3)[bnd_mask]]
    E = np.concatenate([edges, extra, bnd])
    P = np.concatenate([epart, extra_part, bnd_part])
    return E, P, deg


def split_by_part(m, face_part, E, epart):
    """Duplicate vertices per part. Returns pos, nrm, vpart, tris, edges (re-indexed)."""
    vn = m.vertex_normals
    key_v = []
    remap = {}
    pos, nrm, vpart = [], [], []
    tris = np.empty_like(m.faces)
    # vectorised: unique (vertex, part) pairs over the face corners
    corner_v = m.faces.ravel()
    corner_p = np.repeat(face_part, 3)
    pairs = corner_v.astype(np.int64) * 256 + corner_p
    uniq, inv = np.unique(pairs, return_inverse=True)
    tris = inv.reshape(-1, 3).astype(np.uint32)
    uv = (uniq // 256).astype(np.int64)
    up = (uniq % 256).astype(np.uint8)
    pos = np.asarray(m.vertices, np.float32)[uv]
    nrm = np.asarray(vn, np.float32)[uv]
    lut = {int(k): i for i, k in enumerate(uniq)}
    ek = E.astype(np.int64) * 256 + epart[:, None]
    flat = ek.ravel()
    idx = np.searchsorted(uniq, flat)
    idx = np.clip(idx, 0, len(uniq) - 1)
    ok = (uniq[idx] == flat).reshape(-1, 2).all(1)
    edges = idx.reshape(-1, 2)[ok].astype(np.uint32)
    return pos, nrm, up, tris, edges


def part_meta(parts, pos, vpart, tris, edges):
    allc = pos.mean(0)
    meta = []
    for i, p in enumerate(parts):
        sel = vpart == i
        if not sel.any():
            c = allc.copy()
            ext = np.zeros(3)
        else:
            pp = pos[sel]
            c = (pp.min(0) + pp.max(0)) / 2
            ext = pp.max(0) - pp.min(0)
        d = np.array(p.get("explode", c - np.array([0, allc[1], 0])), float)
        if np.linalg.norm(d) < 1e-6:
            d = np.array([0, 1.0, 0])
        d = d / np.linalg.norm(d)
        anchor = p.get("anchor")
        if anchor is None and sel.any():
            # the vertex of this part that sits furthest along the explode direction,
            # pulled halfway back toward the centre: on the part, visible from outside
            pp = pos[sel]
            far = pp[np.argmax(pp @ d)]
            anchor = (c + far) / 2
        meta.append({
            "id": p["id"], "label": p["label"], "bind": p.get("bind"), "hint": p.get("hint", ""),
            "centroid": np.round(c, 4).tolist(), "extent": np.round(ext, 4).tolist(),
            "explode": np.round(d * float(p.get("distance", 0.35)), 4).tolist(),
            "anchor": np.round(np.asarray(anchor if anchor is not None else c, float), 4).tolist(),
            "tris": int((vpart[tris[:, 0]] == i).sum()), "edges": int((vpart[edges[:, 0]] == i).sum()),
        })
    return meta


def write(name, spec, pos, nrm, vpart, tris, edges, parts, extra):
    os.makedirs(OUT, exist_ok=True)
    meta = {
        "format": FORMAT, "name": name, "title": spec["title"], "subtitle": spec.get("subtitle", ""),
        "source": spec.get("source"), "source_sha1": extra.get("sha1"),
        "up": "y", "front": "+z", "height": round(float(pos[:, 1].max()), 4),
        "radius": round(float(np.linalg.norm(pos[:, [0, 2]], axis=1).max()), 4),
        "parts": parts, "counts": {"verts": len(pos), "tris": len(tris), "edges": len(edges)},
        "edge_deg": extra.get("edge_deg"), "baked": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    path = os.path.join(OUT, name + ".holo.npz")
    np.savez_compressed(path, meta=np.frombuffer(json.dumps(meta).encode(), np.uint8),
                        pos=pos.astype(np.float32), nrm=nrm.astype(np.float16), part=vpart.astype(np.uint8),
                        tri=tris.astype(np.uint32), edge=edges.astype(np.uint32))
    return path, meta


def preview(path, out_png, size=900):
    """Front, side and three-quarter wireframe views, part-coloured (for fitting boxes)."""
    from PIL import Image, ImageDraw
    d = np.load(path)
    meta = json.loads(bytes(d["meta"]))
    pos, edge, part = d["pos"], d["edge"], d["part"]
    cols = [(57, 255, 20), (212, 175, 55), (255, 118, 111), (90, 169, 230), (201, 123, 219),
            (63, 224, 197), (240, 211, 106), (156, 255, 138), (255, 157, 151), (140, 202, 245)]
    img = Image.new("RGB", (size * 3, size + 40), (0, 5, 0))
    dr = ImageDraw.Draw(img)
    for k, yaw in enumerate((0, 90, 35)):
        a = np.radians(yaw)
        R = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
        p = pos @ R.T
        sx = size / 2 + p[:, 0] * size * 0.85
        sy = size - 20 - p[:, 1] * size * 0.85
        for (i, j) in edge[:: max(1, len(edge) // 60000)]:
            dr.line([(sx[i] + k * size, sy[i]), (sx[j] + k * size, sy[j])], fill=cols[part[i] % len(cols)], width=1)
        # grid fractions for fitting boxes
        for f in np.linspace(0, 1, 11):
            y = size - 20 - f * meta["height"] * size * 0.85
            dr.text((k * size + 4, y - 6), f"{f:.1f}", fill=(80, 120, 80))
    x = 8
    for i, pm in enumerate(meta["parts"]):
        dr.text((x, size + 14), pm["id"], fill=cols[i % len(cols)])
        x += 8 * len(pm["id"]) + 24
    img.save(out_png)


def bake(name, spec, want_preview):
    t0 = time.time()
    if spec.get("builder"):
        sys.path.insert(0, HERE)
        mod = __import__(spec["builder"])
        m, face_part = mod.build(spec)
    else:
        src = os.path.join(ROOT, spec["source"])
        m = load_mesh(src)
        m = decimate(m, spec.get("faces", 40000))
        m = normalise(m, spec.get("rot_y", 0))
        face_part = segment(m, spec["parts"])
    E, P, deg = feature_edges(m, face_part, spec.get("edge_deg", 30), spec.get("max_edges", 30000))
    pos, nrm, vpart, tris, edges = split_by_part(m, face_part, E, P)
    parts = part_meta(spec["parts"], pos, vpart, tris, edges)
    sha = None
    if spec.get("source"):
        with open(os.path.join(ROOT, spec["source"]), "rb") as f:
            sha = hashlib.sha1(f.read()).hexdigest()
    path, meta = write(name, spec, pos, nrm, vpart, tris, edges, parts, {"sha1": sha, "edge_deg": deg})
    if want_preview:
        os.makedirs(os.path.join(ROOT, "previews"), exist_ok=True)
        preview(path, os.path.join(ROOT, "previews", name + "-parts.png"))
    print(f"{name}: {meta['counts']} edge_deg={deg} parts={[(p['id'], p['tris']) for p in parts]} "
          f"{os.path.getsize(path) // 1024} KB {time.time() - t0:.1f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--root", help="the hologram collection (default: ~/.config/bromigos/vector/holo)")
    a = ap.parse_args()
    use(a.root)
    with open(os.path.join(ROOT, "manifest.json")) as f:
        man = json.load(f)
    for name in a.names or list(man["models"]):
        bake(name, man["models"][name], a.preview)


if __name__ == "__main__":
    main()
