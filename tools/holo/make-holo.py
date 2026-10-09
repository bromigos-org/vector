#!/usr/bin/env python3
"""Add a hologram model and bake it, in one step (run with the build venv):

    ~/.local/share/bromigos/venv/bin/python make-holo.py [--root DIR] add NAME spec.json
        spec: {"title", "subtitle", "builder": "prims", "parts": [...], "shapes": [...]}
        (procedural: no credits; see prims.py for the shapes)
    ~/.local/share/bromigos/venv/bin/python make-holo.py [--root DIR] glb NAME model.glb parts.json
        a generated mesh (nolgia image-to-3D): parts are bbox-fraction boxes, as in
        manifest.json; the GLB is copied into the collection's src/

Every part needs an id, a LABEL, a "hint" (the hover text: what it is and what it shows)
and a "bind": one of the live readings in VECTOR's holo/bind.py KEYS (bromigos-vector), or
"" for a part that only shapes the model. Parts get "explode": [x, y, z] (direction) and
"distance". The model lands in DIR/manifest.json and DIR/NAME.holo.npz, with
DIR/previews/NAME-parts.png (DIR: a hologram collection, collection.py: the user's own
~/.config/bromigos/vector/holo by default); the gallery (SUPER+O) and the holo deck pick it up by
themselves. Prints a JSON summary (counts, size, parts, preview path, warnings).
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import collection  # noqa: E402

_root_arg, _argv = collection.pop_root(sys.argv[1:])
ROOT = collection.resolve(_root_arg)
BIND_KEYS = None


def bind_keys():
    """The reading keys the holo renderer answers (the handlers in holo/holo/bind.py), read
    from its source without importing it."""
    import re
    p = next((q for q in (os.path.join(os.path.dirname(os.path.dirname(HERE)), "holo", "bind.py"),   # this VECTOR tree
                          "/usr/lib/bromigos/vector/holo/bind.py",                                      # bromigos-vector
                          os.path.expanduser("~/github.com/bromigos-org/vector/holo/bind.py"))
              if os.path.exists(q)), "/usr/lib/bromigos/vector/holo/bind.py")
    src = open(p).read()
    keys = set()
    for m in re.finditer(r'key\s*(?:==|in)\s*(\([^)]*\)|"[a-z]+\.[a-z0-9_]+")', src):
        keys.update(re.findall(r'"([a-z]+\.[a-z0-9_]+)"', m.group(1)))
    return keys


def _entry_text(name, spec):
    """One manifest entry in the file's own compact style: one part per line."""
    head = {k: v for k, v in spec.items() if k not in ("parts", "shapes")}
    lines = [f'  {json.dumps(name)}: {{']
    for k, v in head.items():
        lines.append(f'   {json.dumps(k)}: {json.dumps(v)},')
    lines.append('   "parts": [')
    lines += [f'    {json.dumps(p)}' + ("," if i < len(spec["parts"]) - 1 else "") for i, p in enumerate(spec["parts"])]
    if "shapes" in spec:
        lines.append('   ],')
        lines.append('   "shapes": [')
        lines += [f'    {json.dumps(x)}' + ("," if i < len(spec["shapes"]) - 1 else "") for i, x in enumerate(spec["shapes"])]
    lines.append('   ]')
    lines.append('  }')
    return "\n".join(lines)


def write_manifest(man_path, name, spec):
    """Add or replace one model without reformatting the rest of the file (its comment and
    layout stay as they are); falls back to a full rewrite only if the text can't be spliced."""
    text = open(man_path).read()
    man = json.loads(text)
    entry = _entry_text(name, spec)
    if name not in man["models"]:
        i = text.index('"models"')
        j = text.index("{", i) + 1
        new = text[:j] + "\n" + entry + ("," if man["models"] else "") + text[j:]
    else:
        man["models"][name] = spec
        new = json.dumps(man, indent=1) + "\n"
    if json.loads(new)["models"][name] != json.loads(json.dumps(spec)):
        raise ValueError("manifest splice didn't round-trip")
    with open(man_path, "w") as f:
        f.write(new)


def check(name, spec):
    errs, warns = [], []
    if not name.replace("-", "").replace("_", "").isalnum() or len(name) > 40:
        errs.append("name: letters, digits, - and _ (up to 40)")
    for k in ("title", "parts"):
        if not spec.get(k):
            errs.append(f"spec needs {k}")
    keys = bind_keys()
    ids = set()
    for p in spec.get("parts", []):
        pid = p.get("id")
        if not pid or pid in ids:
            errs.append(f"part id missing or repeated: {pid!r}")
        ids.add(pid)
        if not p.get("label"):
            errs.append(f"part {pid}: needs a label")
        if not (p.get("hint") or "").strip():
            errs.append(f"part {pid}: needs a hint (hover text: what it is, what it shows)")
        b = p.get("bind", "")
        if b and b not in keys:
            errs.append(f"part {pid}: bind {b!r} is not a known reading ({sorted(keys)})")
        if not b:
            warns.append(f"part {pid}: no live reading (shape only)")
        p.setdefault("explode", [0, 1, 0])
        p.setdefault("distance", 0.3)
    if len(spec.get("parts", [])) > 16:
        errs.append("16 parts at most (part ids are one byte, callouts get crowded)")
    if spec.get("builder") == "prims":
        for s in spec.get("shapes", []):
            if s.get("part") not in ids:
                errs.append(f"shape {s.get('type')} names unknown part {s.get('part')!r}")
        if not spec.get("shapes"):
            errs.append("a prims model needs shapes")
    return errs, warns


def main():
    argv = _argv
    if len(argv) < 3 or argv[0] not in ("add", "glb"):
        sys.exit(__doc__)
    import numpy as np  # noqa: F401
    import bake
    bake.use(ROOT)
    mode, name = argv[0], argv[1]
    if mode == "add":
        spec = json.load(open(argv[2]))
        spec["builder"] = spec.get("builder") or "prims"
        spec.setdefault("edge_deg", 1)          # clean primitives: every ring and meridian is wireframe; flat diagonals are not
        spec.setdefault("max_edges", 12000)
    else:
        glb, parts = argv[2], json.load(open(argv[3]))
        spec = parts if isinstance(parts, dict) else {"parts": parts}
        spec.setdefault("title", name.upper())
        os.makedirs(os.path.join(ROOT, "src"), exist_ok=True)
        dest = os.path.join(ROOT, "src", f"{name}.glb")
        if os.path.realpath(glb) != os.path.realpath(dest):
            shutil.copy(glb, dest)
        spec["source"] = f"src/{name}.glb"
        spec.setdefault("faces", 40000)
        spec.setdefault("max_edges", 16000)
    errs, warns = check(name, spec)
    if errs:
        print(json.dumps({"ok": False, "errors": errs}))
        sys.exit(1)
    spec.setdefault("subtitle", "")
    man_path = os.path.join(ROOT, "manifest.json")
    if not os.path.exists(man_path):
        with open(man_path, "w") as f:
            f.write('{\n  "_comment": "Hologram models (see VECTOR\'s tools/holo/collection.py).",\n  "models": {\n  }\n}\n')
    man = json.load(open(man_path))
    man["models"][name] = spec
    try:
        bake.bake(name, spec, True)
    except Exception as e:
        print(json.dumps({"ok": False, "errors": [f"bake failed: {type(e).__name__}: {e}"]}))
        sys.exit(1)
    write_manifest(man_path, name, spec)
    import numpy
    d = numpy.load(os.path.join(ROOT, f"{name}.holo.npz"))
    meta = json.loads(bytes(d["meta"]).decode())
    empty = [p["id"] for p in meta["parts"] if not p.get("tris")]
    if empty:
        warns.append(f"parts with no geometry: {empty}")
    print(json.dumps({"ok": True, "model": name, "file": os.path.join(ROOT, f"{name}.holo.npz"),
                      "kb": os.path.getsize(os.path.join(ROOT, f"{name}.holo.npz")) // 1024,
                      "counts": meta["counts"], "parts": [(p["id"], p["tris"], p["edges"]) for p in meta["parts"]],
                      "preview": os.path.join(ROOT, "previews", f"{name}-parts.png"), "warnings": warns}))


if __name__ == "__main__":
    main()
