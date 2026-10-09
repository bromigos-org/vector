#!/usr/bin/env python3
"""Image -> GLB through bromigOS's creation providers (`bromigos art make model3d.generate`).

    gen3d.py [--root DIR] <concept.png> <out.glb> [--provider ID] [--model M] [--texture] [--max-credits N]

Which provider makes it is ~/.config/bromigos/create.toml's [model3d.generate] list
(bromigOS docs/art.md, "Creation providers"): a ComfyUI (Pixal3D, else Hunyuan3D 2.1;
shape only, no credits) and/or nolgia, the first that is configured and answers. --provider forces
one (comfyui:<name> or nolgia); --model names its workflow or model. The older
`--model hunyuan3d-v3|trellis` still means nolgia. --texture asks nolgia for a
textured mesh (holograms keep geometry only, so it's off by default).

Best on a clean subject: cut the concept out first
(`bromigos art make image.cutout --input concept.jpg --out concept-cut.png`).
Appends a line to the collection's generations.jsonl (--root): provider, model,
licence, credits, job, seconds; prints it as JSON.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import collection  # noqa: E402

_root_arg, _rest = collection.pop_root(sys.argv[1:])
sys.argv[1:] = _rest
LEDGER = os.path.join(collection.resolve(_root_arg), "generations.jsonl")   # the collection's (--root)
NOLGIA_MODELS = ("hunyuan3d-v3", "trellis")


def bromigos():
    """The bromigos CLI: $BROMIGOS_BIN, else the installed one."""
    b = os.environ.get("BROMIGOS_BIN") or shutil.which("bromigos") or "/usr/bin/bromigos"
    if not os.path.exists(b) and not shutil.which(b):
        sys.exit("gen3d: needs the bromigos CLI (package bromigos-core)")
    return b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("out")
    ap.add_argument("--provider", help="comfyui:<name> or nolgia (default: create.toml's list)")
    ap.add_argument("--model", help="the workflow (ComfyUI) or model (nolgia)")
    ap.add_argument("--texture", action="store_true", help="a textured nolgia mesh (more credits)")
    ap.add_argument("--max-credits", type=int)
    a = ap.parse_args()
    provider = a.provider
    if a.model and not provider:
        if a.model in NOLGIA_MODELS:
            provider = "nolgia"
        else:
            sys.exit("gen3d: --model needs --provider (comfyui:<name> or nolgia)")
    cmd = [bromigos(), "art", "make", "model3d.generate", "--input", a.image, "--out", a.out, "--json"]
    if provider:
        cmd += ["--provider", provider]
    if a.model:
        cmd += ["--model", a.model]
    if a.texture:
        cmd += ["--extra", "texture=true"]
    if a.max_credits is not None:
        cmd += ["--max-credits", str(a.max_credits)]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        msg = (p.stderr.strip().splitlines() or ["failed"])[-1]
        if "unrecognized subcommand" in p.stderr or "make" in msg and "invalid" in msg:
            msg += " (bromigos-core is older than `art make`: run bromigos update)"
        sys.exit(f"gen3d: {msg}")
    made = json.loads(p.stdout.strip().splitlines()[-1])
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "out": os.path.basename(a.out),
           "input": os.path.basename(a.image), "provider": made.get("provider"),
           "model": made.get("model"), "licence": made.get("licence"), "job": made.get("job"),
           "texture": bool(a.texture), "credits": made.get("credits"),
           "seconds": made.get("seconds", round(time.time() - t0))}
    with open(LEDGER, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec))


if __name__ == "__main__":
    main()
