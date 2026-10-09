"""VECTOR makes things on the homelab: images, edits, cutouts, 3D models and holograms,
through bromigOS's creation providers (`bromigos art make`, bromigOS docs/art.md), and only
on the homelab. He never reaches a paid provider on his own (the operator, 2026-10-08: "I
want him to be using the home lab, not the provider"): every job names the homelab's
ComfyUI explicitly (--provider) with --max-credits 0, so a homelab that is down or can't
make a thing is a refusal he reports, never a fallback that costs credits. nolgia stays a
separate tool (nolgia.py) for when the host asks for it by name.

  create         one image, edit, cutout or 3D model; the file lands in ~/Pictures/vector
  make_hologram  concept image (or a given one) -> cutout -> 3D model, on the homelab; the
                 bake into his hologram collection is the build loop's (build_start), which
                 is the only path to the desktop's models (guard.py)

Where he makes things: bromigOS's targets (`bromigos target resolve <capability>`, docs/
targets.md) when the user has a targets.toml: the first ComfyUI in their order on a free
target, the homelab's or this machine's, never the cloud. Without one, create.toml's ComfyUI
named "homelab" (else the only non-local one). Every job is logged to
~/.local/state/bromigos/vector-create.jsonl.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import time

HOME = os.path.expanduser("~")
CAPS = ("image.generate", "image.edit", "image.cutout", "model3d.generate", "video.loop")
OUT_ROOT = os.path.join(HOME, "Pictures/vector")
HOLO_RAW = os.path.join(HOME, ".local/share/bromigos/3d-raw")        # his terminal may read it (shell.py)
LEDGER = os.path.join(HOME, ".local/state/bromigos/vector-create.jsonl")
DEFAULT_CONF = "/usr/share/bromigos/default/create.toml"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
INPUT_ROOTS = ("Pictures", "Downloads", "Videos", ".local/share/bromigos/3d-raw", ".local/share/bromigos/nolgia",
               ".local/share/bromigos/builds", "github.com", ".dotfiles")
TIMEOUT_S = 900
# The look a 3D model wants from its concept: one subject, whole, isolated, no text.
CONCEPT = ("{subject}. A single object, whole and centred, seen from a three-quarter angle slightly above, on a plain "
           "white background, soft even studio light, clear silhouette, no text, no logo, no other objects, no shadow "
           "clutter.")


def _bin():
    return os.environ.get("BROMIGOS_BIN") or shutil.which("bromigos") or "/usr/bin/bromigos"


def _load(path):
    import tomllib
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, ValueError):
        return {}


def _merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def config():
    """create.toml as bromigos reads it: the packaged default, then the user's, key by key
    ($BROMIGOS_CREATE_CONFIG replaces the user's)."""
    user = os.environ.get("BROMIGOS_CREATE_CONFIG") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "bromigos/create.toml")
    return _merge(_load(DEFAULT_CONF), _load(user))


def homelab(cfg=None, cap="image.generate"):
    """The provider id VECTOR makes `cap` on ("comfyui:homelab", or "comfyui:local" when the
    user put this machine first), or None. The one place that chooses where he makes
    things: bromigOS's targets (bromigos target) when the user has a targets.toml: the first
    ComfyUI on a free target in their order, never the cloud; else create.toml's homelab."""
    from .. import targets
    es = targets.resolve(cap)
    if es is not None:
        free = [e for e in es
                if e.get("target") in ("homelab", "local") and str(e.get("engine", "")).startswith("comfyui:")]
        up = next((e for e in free if e.get("endpoint") and targets.answers(e["endpoint"])), None)
        return (up or (free[0] if free else {})).get("engine")
    inst = (cfg if cfg is not None else config()).get("comfyui") or {}
    if "homelab" in inst:
        return "comfyui:homelab"
    others = [n for n in inst if n != "local"]
    return f"comfyui:{others[0]}" if len(others) == 1 else None


def workflow(cap, cfg=None):
    """The homelab's workflow for a capability, or None (video.loop has none today)."""
    a, b = cap.split(".")
    return (((cfg if cfg is not None else config()).get(a) or {}).get(b) or {}).get("comfyui")


def capabilities():
    cfg = config()
    on = {c: homelab(cfg, c) for c in CAPS}
    return {"provider": next((p for p in on.values() if p), None),
            "serves": [c for c in CAPS if on[c] and workflow(c, cfg)],
            "not_on_the_homelab": [c for c in CAPS if not (on[c] and workflow(c, cfg))]}


def _input(path):
    p = os.path.realpath(os.path.expanduser(str(path)))
    if not os.path.isfile(p):
        raise ValueError(f"no such image: {path}")
    if not p.lower().endswith(IMAGE_EXT):
        raise ValueError("inputs are images (.png, .jpg, .jpeg, .webp)")
    roots = [os.path.realpath(os.path.join(HOME, r)) + os.sep for r in INPUT_ROOTS] + ["/tmp/"]
    if not p.startswith(tuple(roots)):
        raise ValueError("inputs come from ~/Pictures, ~/Downloads, ~/Videos, ~/github.com, ~/.dotfiles, /tmp or "
                         "VECTOR's own media folders")
    return p


def _slug(text, fallback):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower())[:40].strip("-") or fallback


def _refuse(why):
    return {"refused": why, "say": "Say it plainly to Sir. Don't use nolgia or any paid provider on your own; "
                                   "nolgia only if he asks for it by name."}


def _log(rec):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps(rec) + "\n")


def _show(path, title):
    """A desktop notice with the picture as its icon (the notification daemon scales it)."""
    if os.environ.get("VECTOR_CREATE_QUIET") or not path.lower().endswith(IMAGE_EXT):
        return
    try:
        subprocess.run(["notify-send", "-a", "VECTOR", "-i", path, "VECTOR · made", title], timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


def _make(cap, out, prompt=None, inputs=(), mask=None, aspect=None):
    """One `bromigos art make` on the homelab only. Returns its JSON, or a refusal."""
    cfg = config()
    prov = homelab(cfg, cap)
    if not prov:
        return _refuse("there's no homelab ComfyUI (targets.toml's homelab, or create.toml's [comfyui.homelab]), "
                       "so I can't make it on the homelab")
    if not workflow(cap, cfg):
        return _refuse(f"the homelab has no model for {cap} yet")
    cmd = [_bin(), "art", "make", cap, "--provider", prov, "--max-credits", "0", "--out", out, "--json"]
    if prompt:
        cmd += ["--prompt", prompt]
    for p in inputs:
        cmd += ["--input", p]
    if mask:
        cmd += ["--mask", mask]
    if aspect:
        cmd += ["--aspect", aspect]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return _refuse(f"the homelab didn't finish {cap} in {TIMEOUT_S // 60} minutes")
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().splitlines()
        return _refuse(f"the homelab couldn't make it ({(err[-1] if err else 'no answer')[:240]})")
    try:
        res = json.loads(r.stdout)
    except ValueError:
        return _refuse("the homelab's answer wasn't readable")
    credits = float(res.get("credits") or 0)
    if res.get("provider") != prov or credits > 0:      # can't happen with --provider and --max-credits 0
        return _refuse(f"that ran on {res.get('provider')} for {credits:g} credits, not on the homelab; tell Sir")
    res["seconds"] = res.get("seconds") or round(time.monotonic() - t0, 1)
    _log({"t": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "capability": cap,
          "provider": res.get("provider"), "model": res.get("model"), "credits": 0, "licence": res.get("licence"),
          "out": res.get("out") or out, "seconds": res["seconds"]})
    return res


def create(capability, prompt=None, inputs=None, mask=None, aspect=None, name=None):
    """Make one image (image.generate), edit (image.edit), cutout (image.cutout) or 3D model
    (model3d.generate) on the homelab, for no credits."""
    if capability not in CAPS:
        raise ValueError(f"capability: one of {', '.join(CAPS)}")
    prompt = " ".join((prompt or "").split()) or None
    if prompt and len(prompt) > 4000:
        raise ValueError("a prompt is up to 4000 characters")
    if capability in ("image.generate", "image.edit") and not prompt:
        raise ValueError(f"{capability} needs a prompt")
    ins = [_input(p) for p in (inputs or [])][:4]
    if capability in ("image.edit", "image.cutout", "model3d.generate") and not ins:
        raise ValueError(f"{capability} needs an input image")
    if aspect and not re.fullmatch(r"\d{1,2}:\d{1,2}", aspect):
        raise ValueError("aspect is W:H, e.g. 16:9")
    ext = {"model3d.generate": "glb", "video.loop": "mp4"}.get(capability, "png")
    base = _slug(name or prompt or (os.path.splitext(os.path.basename(ins[0]))[0] if ins else ""), capability.split(".")[1])
    out = os.path.join(OUT_ROOT, dt.date.today().isoformat(), f"{time.strftime('%H%M%S')}-{base}.{ext}")
    res = _make(capability, out, prompt, ins, _input(mask) if mask else None, aspect)
    if "refused" in res:
        return res
    f = res.get("out") or out
    _show(f, f"{capability} · {res.get('model')} · homelab")
    return {"ok": os.path.exists(f), "file": f, "capability": capability, "provider": res.get("provider"),
            "model": res.get("model"), "credits": 0, "licence": res.get("licence"), "seconds": res.get("seconds")}


def make_hologram(name, subject=None, image=None):
    """Concept image (from subject, or a given image) -> cutout -> 3D model, all on the homelab.
    Returns the GLB and the build goal that bakes it into his collection."""
    name = _slug(name, "")
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,30}", name or ""):
        raise ValueError("name: a short model name, letters first (e.g. coffee-mug)")
    if not (subject or image):
        raise ValueError("give a subject (what to model) or an image")
    d = os.path.join(HOLO_RAW, name)
    steps = []
    if image:
        concept = _input(image)
    else:
        concept = os.path.join(d, "concept.png")
        r = _make("image.generate", concept, CONCEPT.format(subject=" ".join(str(subject).split())[:600]), aspect="1:1")
        if "refused" in r:
            return dict(r, steps=steps)
        steps.append({"step": "concept", "model": r.get("model"), "credits": 0, "seconds": r.get("seconds")})
    cut = os.path.join(d, "cutout.png")
    r = _make("image.cutout", cut, inputs=[concept])
    if "refused" in r:
        return dict(r, steps=steps)
    steps.append({"step": "cutout", "model": r.get("model"), "credits": 0, "seconds": r.get("seconds")})
    glb = os.path.join(d, f"{name}.glb")
    r = _make("model3d.generate", glb, inputs=[cut])
    if "refused" in r:
        return dict(r, steps=steps)
    steps.append({"step": "3d", "model": r.get("model"), "credits": 0, "seconds": r.get("seconds")})
    _show(cut, f"hologram {name}: model made on the homelab")
    goal = (f"Add a hologram model named '{name}' to my collection from the generated mesh {glb} (concept: {cut}). "
            f"Follow the make-hologram-model skill's generated path: look at the concept, choose 3-8 meaningful parts "
            f"as bbox-fraction boxes with labels, hover hints and live readings that fit, then "
            f"make-holo.py --root linux/.config/bromigos/vector/holo glb {name} {glb} parts.json, check the preview, "
            f"and put it up in trial mode.")
    return {"ok": os.path.exists(glb), "name": name, "glb": glb, "concept": concept, "cutout": cut,
            "provider": r.get("provider"), "credits": 0, "steps": steps,
            "next": "Call build_start with this goal to bake it into the collection, then show_hologram once the "
                    "trial is up.", "build_goal": goal}
