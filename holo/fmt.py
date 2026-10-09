"""Reader for the compact hologram format, `bromigos-holo/1` (`*.holo.npz`).

A .holo.npz is a NumPy zip archive (numpy alone reads it) with:

    meta  uint8   UTF-8 JSON header (see below)
    pos   f32[V,3] vertex positions; y up, front +z, base on y = 0, tallest extent 1
    nrm   f16[V,3] vertex normals
    part  u8[V]    part index of each vertex (vertices are split per part, so a part
                   moves as a unit with no tearing at its borders)
    tri   u32[T,3] surface triangles (for the faint fill, depth and picking)
    edge  u32[E,2] feature edges (sharp creases, part borders, open boundaries):
                   the wireframe a hologram draws

meta = {format, name, title, subtitle, source, source_sha1, up, front, height, radius,
        counts, edge_deg, baked,
        parts: [{id, label, bind, hint, centroid[3], extent[3], explode[3], anchor[3],
                 tris, edges}]}

`explode` is the full offset of a part when the model is fully exploded; `anchor` is
the point a callout's leader line attaches to (move it with the part); `bind` names
the live reading the part shows (see holo/bind.py).
"""
import json
import os

import numpy as np

from .paths import CONFIG, HOMELAB, ROOT

# Hologram collections (tools/holo/collection.py), first match winning per model name.
# VECTOR's own (MODEL_DIRS, the gallery's VECTOR group):
#   the user's own (~/.config/bromigos/vector/holo: their models, e.g. one for a plugin's feed)
#   the old dotfiles location, read until it moves
#   bromigos-homelab's (the rack), when it is installed
#   this VECTOR's own (packaged: /usr/share/bromigos/vector/holo; a checkout: assets/holo)
# then the worlds' (holo/worlds.py: bromigOS's themes' holo/ folders, the Drift's shared
# models, and installed theme extensions' holo/ folders), each its own group. All are read
# only; the packaged ones and the extensions' can't be written by VECTOR's terminal.
_PKG = "/usr/share/bromigos/vector/holo" if ROOT.startswith("/usr/") else os.path.join(ROOT, "assets", "holo")
_HOMELAB = next((d for d in ([os.path.join(os.path.dirname(HOMELAB), "homelab", "holo")] if HOMELAB and not HOMELAB.startswith("/usr/") else [])
                 + ["/usr/share/bromigos/homelab/holo"] if os.path.isdir(d)), None)
MODEL_DIRS = [os.path.join(CONFIG, "vector", "holo"), os.path.join(CONFIG, "brand", "3d", "holo")] + \
    ([_HOMELAB] if _HOMELAB else []) + [_PKG]
if os.environ.get("BROMIGOS_HOLO_MODELS"):          # a build worktree's models first (offscreen checks)
    MODEL_DIRS.insert(0, os.environ["BROMIGOS_HOLO_MODELS"])
ORDER = ["workstation", "wick", "rack", "monolith", "emblem"]


_manifests = {}     # path -> (mtime, models)


def _manifest(d):
    """A collection's manifest (its models' specs), or {} (the old location's lives one up)."""
    for p in (os.path.join(d, "manifest.json"), os.path.join(os.path.dirname(d), "manifest.json")):
        try:
            m = os.path.getmtime(p)
            if p not in _manifests or _manifests[p][0] != m:
                with open(p) as f:
                    models = json.load(f).get("models", {})
                _manifests[p] = (m, models if isinstance(models, dict) else {})
            return _manifests[p][1]
        except (OSError, ValueError, AttributeError):
            continue
    return {}


def _have(capability):
    """A model's `requires`: "homelab" needs bromigos-homelab with a source configured."""
    if capability == "homelab":
        try:
            import bromigos_homelab
            return bromigos_homelab.configured()
        except Exception:
            return False
    return True


def _shown(d, name):
    spec = _manifest(d).get(name, {})
    req = spec.get("requires") if isinstance(spec, dict) else None
    return all(_have(r) for r in ([req] if isinstance(req, str) else req or []))


def collections():
    """Every collection in lookup order: [(dir, group)] (worlds.VECTOR for VECTOR's own)."""
    from . import worlds
    return [(d, worlds.VECTOR) for d in MODEL_DIRS] + worlds.world_dirs()


class HoloModel:
    def __init__(self, path):
        with np.load(path) as d:
            self.meta = json.loads(bytes(d["meta"]).decode())
            self.pos = d["pos"].astype(np.float32)
            self.nrm = d["nrm"].astype(np.float32)
            self.part = d["part"].astype(np.uint8)
            self.tri = d["tri"].astype(np.uint32)
            self.edge = d["edge"].astype(np.uint32)
        self.path = path
        self.name = self.meta["name"]
        self.parts = self.meta["parts"]

    def __repr__(self):
        return f"<HoloModel {self.name} {self.meta['counts']}>"


def _found():
    """{name: (path, group)}: the first collection holding a name decides it."""
    seen = {}
    for d, g in collections():
        try:
            files = sorted(os.listdir(d))
        except OSError:
            continue
        for f in files:
            if f.endswith(".holo.npz") and f[:-9] not in seen:
                seen[f[:-9]] = (os.path.join(d, f), g)
    return seen


def path_of(name):
    for d, _ in collections():
        p = os.path.join(d, name + ".holo.npz")
        if os.path.exists(p):
            return p
    raise FileNotFoundError(name)


_bad = {}            # path -> mtime of a file that didn't load: left out until it changes


def _broken(p):
    try:
        return _bad.get(p) == os.path.getmtime(p)
    except OSError:
        return True


def _live_first(d, name):
    """Within a world: its post (a model with a part bound to a live reading) first."""
    spec = _manifest(d).get(name, {})
    parts = spec.get("parts") if isinstance(spec, dict) else None
    return 0 if any(isinstance(p, dict) and p.get("bind") for p in parts or []) else 1


def groups():
    """The gallery's shelves: [(group, [names])], the world in force first, then VECTOR's
    own, the Drift's, the other worlds (holo/worlds.py, rank). A model whose `requires` the
    machine lacks (the rack without a homelab), or whose file didn't load, is left out."""
    from . import worlds
    by = {}
    for n, (p, g) in _found().items():
        d = os.path.dirname(p)
        if _shown(d, n) and not _broken(p):
            by.setdefault(g, []).append((n, d))
    here = worlds.current()
    out = []
    for g in sorted(by, key=lambda g: worlds.rank(g, here)):
        if g == worlds.VECTOR:
            names = sorted((n for n, _ in by[g]), key=lambda n: (ORDER.index(n) if n in ORDER else 99, n))
        else:
            names = [n for n, d in sorted(by[g], key=lambda nd: (_live_first(nd[1], nd[0]), nd[0]))]
        out.append((g, names))
    return out


def group_of(name):
    """The group (a world, or worlds.VECTOR / worlds.DRIFT) a model belongs to."""
    return _found().get(name, (None, None))[1]


def available():
    """Every model on the table, shelf by shelf (groups())."""
    return [n for _, names in groups() for n in names]


def title(name):
    """A model's title for a list: its manifest's, else its own header's, else its name."""
    try:
        p = path_of(name)
        spec = _manifest(os.path.dirname(p)).get(name, {})
        if isinstance(spec, dict) and isinstance(spec.get("title"), str):
            return spec["title"]
        return load(name).meta.get("title") or name.upper()
    except Exception:
        return name.upper()


_cache = {}


def load(name):
    """Cached, but re-read when the file changes (a rebake shows at once). A file that
    doesn't load is remembered, and left out of the shelves until it changes."""
    p = path_of(name)
    m = os.path.getmtime(p)
    if name not in _cache or _cache[name][0] != m or _cache[name][2] != p:
        try:
            _cache[name] = (m, HoloModel(p), p)
        except Exception:
            _bad[p] = m
            raise
    return _cache[name][1]
