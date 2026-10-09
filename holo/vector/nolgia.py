"""VECTOR and the nolgia CLI (~/.cargo/bin/nolgia, the host's own product): images, video,
audio, 3D and characters, always with --json, always through a credit budget.

  nolgia_catalog   models of a modality with their credit prices (the live catalog)
  nolgia_credits   balance (billing credits), today's spend, the daily cap
  nolgia_read      read-only CLI commands (models, assets, characters, projects, jobs)
  nolgia_generate  image | video | audio: estimate first, refuse over the daily cap unless
                   the host approved it in a turn after VECTOR asked, run, log the spend,
                   and review the output (a vision look for images and a video frame)
  nolgia_review    look at an image (or a video's middle frame) with the vision model

The daily cap is "daily_credits" in holo/nolgia.json (start: 20). Spend is logged per job
to ~/.local/state/bromigos/nolgia-spend.jsonl. Generation from his raw terminal is refused
(shell.py), so the budget can't be skipped.
"""
import base64
import datetime as dt
import hashlib
import json
import math
import os
import re
import ssl
import subprocess
import time
import urllib.request

HOME = os.path.expanduser("~")
NOLGIA = os.path.join(HOME, ".cargo/bin/nolgia")
from ..paths import conf  # noqa: E402
CONF = conf("nolgia.json")   # LOCKED: /etc or the package only, never a user-writable folder
LEDGER = os.path.join(HOME, ".local/state/bromigos/nolgia-spend.jsonl")
OUTDIR = os.path.join(HOME, ".local/share/bromigos/nolgia")
VISION_MODEL = __import__("holo.vector.llm", fromlist=["model"]).model("vision")   # multimodal (llm.py)
LAST_USER = {"t": 0.0}                         # set by the brain at each host turn
_pending = {}                                  # request hash -> time VECTOR asked
READ_CMDS = {("models", "list"), ("models", "get"), ("assets", "list"), ("assets", "get"), ("characters", "list"),
             ("characters", "get"), ("projects", "list"), ("projects", "get"), ("status",), ("wait",),
             ("account", "me"), ("account", "usage"), ("billing", "credits"), ("skills", "list"), ("skills", "show")}
SAFE_ARG = re.compile(r"[\w .,:/@#+=%-]{1,200}$")


def _conf():
    try:
        with open(CONF) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _cli(args, timeout=60):
    r = subprocess.run([NOLGIA, "--json", *args], capture_output=True, text=True, timeout=timeout,
                       stdin=subprocess.DEVNULL)
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        raise RuntimeError((r.stderr or out).strip()[-300:])
    try:
        return json.loads(out)
    except ValueError:
        return out


def _catalog():
    return _cli(["models", "list"])


def _model(mid):
    for m in _catalog():
        if m["id"] == mid:
            return m
    raise ValueError(f"no nolgia model {mid!r} (nolgia_catalog lists them)")


def balance():
    d = _cli(["billing", "credits"])
    return d.get("available_for_api", d.get("total"))


def spent_today():
    today = dt.date.today().isoformat()
    total = 0.0
    try:
        with open(LEDGER) as f:
            for line in f:
                r = json.loads(line)
                if r.get("t", "").startswith(today):
                    total += float(r.get("credits") or r.get("estimate") or 0)
    except (OSError, ValueError):
        pass
    return total


def estimate(model, quality=None, count=1, chars=0, duration_seconds=None):
    m = _model(model)
    c = m.get("cost") or {}
    per = float(c.get("credits") or 0)
    if quality:
        opt = next((o for o in (m.get("quality") or {}).get("options", []) if o.get("id") == quality), None)
        if not opt:
            raise ValueError(f"{model} quality tiers: {[o['id'] for o in (m.get('quality') or {}).get('options', [])]}")
        per = float(opt["credits"])
    unit = c.get("unit")
    if unit == "per_character":
        cpc = float(c.get("characters_per_credit") or 1000)
        return max(float(c.get("minimum_credits") or 1), math.ceil(chars / cpc) * per), unit
    if unit == "per_second" and duration_seconds:
        return per * float(duration_seconds), unit
    return per * max(1, int(count or 1)), unit


# ------------------------------------------------------------------ tools
def nolgia_catalog(modality="image", limit=30):
    if modality not in ("image", "video", "audio", "3d"):
        raise ValueError("modality: image, video, audio or 3d")
    rows = [m for m in _catalog() if m.get("modality") == modality]
    rows.sort(key=lambda m: (m.get("cost") or {}).get("credits") or 999)
    return [{"model": m["id"], "credits": (m.get("cost") or {}).get("credits"), "unit": (m.get("cost") or {}).get("unit"),
             "recommended": m.get("recommended"),
             "refs": (m.get("image") or {}).get("reference_images_max")} for m in rows[:max(1, min(int(limit), 80))]]


def nolgia_credits():
    cap = float(_conf().get("daily_credits", 20))
    today = spent_today()
    return {"balance": balance(), "spent_today": today, "daily_cap": cap, "left_today": max(0.0, cap - today)}


def nolgia_read(command):
    """Read-only CLI commands: 'models get flux-pro', 'characters list', 'assets get <id>', 'status <job>'."""
    words = (command or "").split()
    if not words or not ((tuple(words[:2]) in READ_CMDS) or (tuple(words[:1]) in READ_CMDS)):
        raise ValueError(f"read commands only: {sorted(' '.join(c) for c in READ_CMDS)}")
    if not all(SAFE_ARG.match(w) for w in words):
        raise ValueError("plain arguments only")
    out = _cli(words, timeout=320 if words[0] == "wait" else 60)
    s = json.dumps(out) if not isinstance(out, str) else out
    return out if len(s) < 8000 else s[:8000] + "…"


def _key(kind, model, prompt, quality, count, duration):
    return hashlib.sha1(json.dumps([kind, model, prompt, quality, count, duration]).encode()).hexdigest()[:16]


def nolgia_generate(kind, prompt, model=None, out=None, input=None, aspect_ratio=None, quality=None,
                    character_id=None, voice=None, duration_seconds=None, project_id=None, approved=False):
    """Generate one image, video or audio clip under the daily credit cap."""
    if kind not in ("image", "video", "audio"):
        raise ValueError("kind: image, video or audio (3D: the make-hologram pipeline)")
    prompt = " ".join((prompt or "").split())
    if not prompt or len(prompt) > 4000:
        raise ValueError("a prompt (up to 4000 chars) is required")
    model = model or {"image": "flux-2-klein", "video": "kling-v2-5-turbo", "audio": "elevenlabs-sound-effects-v2"}[kind]
    m = _model(model)
    if m.get("modality") != kind:
        raise ValueError(f"{model} is a {m.get('modality')} model, not {kind}")
    est, unit = estimate(model, quality, 1, len(prompt), duration_seconds)
    if kind == "video":                           # the API prices video exactly
        args = ["gen", "video", "--model", model, "--prompt", prompt, "--cost-only"]
        if duration_seconds:
            args += ["--duration-seconds", str(int(duration_seconds))]
        if quality:
            args += ["--quality", quality]
        try:
            c = _cli(args)
            est = float((c.get("credits") if isinstance(c, dict) else None) or est)
        except Exception:
            pass
    cap = float(_conf().get("daily_credits", 20))
    today = spent_today()
    key = _key(kind, model, prompt, quality, 1, duration_seconds)
    if today + est > cap:
        asked = _pending.get(key)
        if not (approved and asked and LAST_USER["t"] > asked):
            _pending[key] = time.time()
            return {"needs_approval": True, "estimate_credits": est, "spent_today": today, "daily_cap": cap,
                    "say": f"This would cost about {est:g} credits and take today's spend to {today + est:g}, over the "
                           f"{cap:g}-credit daily cap. Ask Sir; if he says yes, call again with approved=true."}
    day = dt.date.today().isoformat()
    ext = {"image": "png", "video": "mp4", "audio": "mp3"}[kind]
    if out:
        dest = os.path.realpath(os.path.expanduser(out))
        allowed = (os.path.join(HOME, ".dotfiles"), os.path.join(HOME, "github.com"), OUTDIR,
                   os.path.join(HOME, ".config/bromigos"), os.path.join(HOME, "Pictures"), os.path.join(HOME, "Music"),
                   os.path.join(HOME, "Videos"), "/tmp")
        if not dest.startswith(tuple(os.path.realpath(a) + os.sep for a in allowed)):
            raise ValueError("out must be under ~/.dotfiles, ~/github.com, ~/.config/bromigos, ~/Pictures, ~/Music, "
                             "~/Videos or the default ~/.local/share/bromigos/nolgia")
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower())[:40].strip("-") or kind
        dest = os.path.join(OUTDIR, day, f"{time.strftime('%H%M%S')}-{slug}.{ext}")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    args = ["gen", kind, "--model", model, "--prompt", prompt, "--out", dest]
    for flag, v in (("--input", input), ("--aspect-ratio", aspect_ratio), ("--quality", quality),
                    ("--character-id", character_id), ("--voice", voice), ("--project-id", project_id),
                    ("--duration-seconds", duration_seconds)):
        if v not in (None, ""):
            args += [flag, str(v)]
    if kind != "video":
        args.append("--wait")
    else:
        args += ["--wait", "true"]
    before = None
    try:
        before = balance()
    except Exception:
        pass
    t0 = time.monotonic()
    res = _cli(args, timeout=1500)
    after = None
    try:
        after = balance()
    except Exception:
        pass
    spent = (before - after) if (before is not None and after is not None and before >= after) else None
    job = None
    if isinstance(res, dict):
        job = res.get("id") or res.get("job_id") or (res.get("job") or {}).get("id")
    rec = {"t": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "kind": kind, "model": model,
           "job": job, "estimate": est, "credits": spent if spent is not None else est, "out": dest,
           "seconds": round(time.monotonic() - t0)}
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(json.dumps(rec) + "\n")
    _pending.pop(key, None)
    from . import events
    events.emit("tool.end", id=f"nolgia-{job}", name="nolgia_generate", ok=os.path.exists(dest), ms=None,
                outcome=f"{kind} {model} {rec['credits']:g} credits")
    out_d = {"ok": os.path.exists(dest), "file": dest, "model": model, "job": job, "credits_spent": rec["credits"],
             "credits_left": after, "spent_today": spent_today(), "daily_cap": cap, "seconds": rec["seconds"]}
    if os.path.exists(dest) and kind in ("image", "video"):
        try:
            out_d["review"] = nolgia_review(dest, f"Describe what this shows in two sentences. The prompt was: {prompt[:300]}. "
                                                  "Say plainly whether it matches, and any flaw (garbled text, wrong subject, "
                                                  "artifacts, logos).")["review"]
        except Exception as e:
            out_d["review"] = f"(no review: {type(e).__name__}: {str(e)[:80]}; look at it before presenting it)"
    return out_d


def nolgia_review(path, question="Describe this image in two sentences and note any flaws."):
    """Look at an image (or a video's middle frame) with the vision model."""
    p = os.path.realpath(os.path.expanduser(path))
    if not os.path.isfile(p):
        raise ValueError(f"no such file: {path}")
    img = p
    if p.lower().endswith((".mp4", ".mov", ".webm", ".mkv")):
        img = p + ".frame.jpg"
        dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
                             capture_output=True, text=True, timeout=20).stdout.strip() or "1"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", str(float(dur) / 2), "-i", p, "-frames:v", "1",
                        "-vf", "scale=768:-2", img], capture_output=True, timeout=60)
    from PIL import Image
    im = Image.open(img).convert("RGB")
    im.thumbnail((1024, 1024))
    import io
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    b64 = base64.b64encode(buf.getvalue()).decode()
    from . import llm
    key = llm.key()
    ca = os.path.join(HOME, ".config/homelab/homelab-ca.crt")
    ctx = ssl.create_default_context(cafile=ca) if os.path.exists(ca) else ssl.create_default_context()
    body = {"model": VISION_MODEL, "max_tokens": 220, "temperature": 0.2,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": [{"type": "text", "text": question[:1000]},
                                                       {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]}]}
    from ..private import PRIV
    req = urllib.request.Request(llm.url("/chat/completions"), data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90, context=ctx) as r:
        txt = json.load(r)["choices"][0]["message"]["content"]
    if img != p:
        os.unlink(img)
    return {"file": p, "size": f"{Image.open(p).size if img == p else 'video'}", "review": txt.strip()}
