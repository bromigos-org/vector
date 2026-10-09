"""VECTOR's lab tools: bromigOS's homelab console (`bromigos lab`, bromigOS docs/homelab.md "The
lab console"). He reads the lab and makes things on its GPUs through its job queue, the same
queue Sir's `bromigos lab run` uses, so his work waits its turn, never crowds the GPU and
never starves the voice service he speaks through.

  lab_console      the console's status cards: nodes, GPUs, services, apps, storage, alerts, jobs
  lab_jobs         the queue: recent jobs, or one job with its log and results
  lab_job_submit   queue an image, edit, cutout, 3D model, hologram, voice line, transcription or
                   LLM batch on the lab; it waits up to `wait` seconds and reports what it made
  lab_job_cancel   cancel one of his own jobs

Limits, here in code (and his terminal refuses the same through `bromigos lab`, guard.py):
  * never the paid cloud: no job of his asks for --cloud (Sir's flag alone);
  * his jobs are recorded as his (by "vector"); he cancels only his own;
  * inputs come from the same folders as create's (no key files, no secrets);
  * no lab changes: deploying, restarting, loading or unloading models, power, provisioning
    and where AI runs are Sir's (Phase 2 asks him first).
Every call goes through tools.call (the audit log, the event feed, stop-after-a-block).
"""
import json
import os
import shutil
import subprocess
import time

from .create import INPUT_ROOTS

HOME = os.path.expanduser("~")
KINDS = ("image", "image-edit", "cutout", "3d", "hologram", "voice", "transcribe", "upscale", "batch-llm", "video")
INPUT_EXT = {"image-edit": (".png", ".jpg", ".jpeg", ".webp"), "cutout": (".png", ".jpg", ".jpeg", ".webp"),
             "3d": (".png", ".jpg", ".jpeg", ".webp"), "hologram": (".png", ".jpg", ".jpeg", ".webp"),
             "image": (".png", ".jpg", ".jpeg", ".webp"),
             "upscale": (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov", ".mkv", ".m4v"),
             "transcribe": (".wav", ".mp3", ".m4a", ".ogg", ".opus", ".flac", ".webm"),
             "batch-llm": (".txt", ".jsonl")}
WAIT_MAX = 600


def _bin():
    """`bromigos-lab` itself (the router's target), or None without bromigos-homelab."""
    return next((p for p in (os.environ.get("BROMIGOS_LAB_BIN"), "/usr/lib/bromigos/bin/bromigos-lab",
                             shutil.which("bromigos-lab")) if p and os.path.exists(p)), None)


def available():
    return bool(_bin())


def _env():
    env = dict(os.environ, BROMIGOS_LAB_BY="vector", NO_COLOR="1")
    env.pop("BROMIGOS_LAB_ALLOW_CLOUD", None)
    return env


def _lab(args, timeout=60):
    exe = _bin()
    if not exe:
        raise RuntimeError("bromigos lab isn't installed (bromigos-homelab)")
    if any(a == "--cloud" or a.startswith("--cloud=") for a in args):
        raise PermissionError("never the paid cloud from VECTOR")
    r = subprocess.run([exe] + list(args), capture_output=True, text=True, timeout=timeout, env=_env(),
                       stdin=subprocess.DEVNULL)
    out = (r.stdout or "").strip()
    try:
        doc = json.loads(out) if out else None
    except ValueError:
        doc = None
    return r.returncode, doc, (r.stderr or "").strip()


def _input(path, kind):
    p = os.path.realpath(os.path.expanduser(str(path)))
    if not os.path.isfile(p):
        raise ValueError(f"no such file: {path}")
    ext = INPUT_EXT.get(kind)
    if ext and not p.lower().endswith(ext):
        raise ValueError(f"{kind} takes {', '.join(ext)} files")
    roots = [os.path.realpath(os.path.join(HOME, r)) + os.sep for r in INPUT_ROOTS] + ["/tmp/"]
    if not p.startswith(tuple(roots)):
        raise ValueError("inputs come from ~/Pictures, ~/Downloads, ~/Videos, ~/github.com, ~/.dotfiles, /tmp or "
                         "VECTOR's own media folders")
    return p


def _brief(j):
    keep = ("id", "kind", "preset", "state", "why", "progress", "progress_text", "outputs", "by", "prompt", "text",
            "note", "created", "finished")
    out = {k: j.get(k) for k in keep if j.get(k) not in (None, "", [], {})}
    if j.get("created"):
        out["age_s"] = int(time.time() - j["created"])
    return out


# ------------------------------------------------------------------ the tools
def lab_console(card=None):
    """The lab console's cards (bromigos lab status --json), trimmed for the brain."""
    args = ["status", "--json"] + (["--card", card] if card else [])
    code, doc, err = _lab(args, timeout=90)
    if doc is None:
        raise RuntimeError(err or "bromigos lab status didn't answer")
    if not doc.get("configured", True):
        return {"configured": False, "say": "No homelab is connected (Sir runs bromigos lab connect)."}
    cards = []
    for c in doc.get("cards", []):
        e = {"id": c["id"], "title": c["title"], "state": c["state"], "summary": c.get("summary")}
        bad = [i for i in c.get("items", []) if i.get("state") in ("warn", "crit")]
        if card or bad:
            cols = [k for k in (c.get("columns") or []) if k != "state"][:7]
            e["items"] = [{k: i.get(k) for k in ["name", "state"] + cols} for i in (c.get("items") if card else bad)[:25]]
        if c.get("errors"):
            e["unreachable"] = c["errors"]
        cards.append(e)
    return {"state": doc.get("state"), "cards": cards, "read_in_s": doc.get("seconds")}


def lab_jobs(job=None, limit=10):
    """Recent lab jobs, or one job with its log (bromigos lab jobs --json)."""
    if job:
        code, doc, err = _lab(["jobs", "logs", str(job), "--json"])
        if doc is None:
            raise ValueError(err or f"no job {job}")
        return {"job": _brief(doc["job"]), "log": (doc.get("log") or "")[-2000:]}
    code, doc, err = _lab(["jobs", "list", "--json"])
    if doc is None:
        raise RuntimeError(err or "bromigos lab jobs didn't answer")
    return {"service": doc.get("service"), "jobs": [_brief(j) for j in doc.get("jobs", [])[:max(1, min(int(limit), 40))]]}


def lab_job_submit(kind, preset=None, prompt=None, text=None, inputs=None, voice=None, name=None, aspect=None,
                   wait=0):
    """Queue a job on the lab (homelab only, recorded as his) and wait up to `wait` seconds."""
    if kind not in KINDS and not preset:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    args = ["run"] + ([kind] if kind else []) + ([preset] if preset else []) + ["--json"]
    if prompt:
        args += ["--prompt", str(prompt)[:2000]]
    if text:
        args += ["--text", str(text)[:2000]]
    for i in (inputs or [])[:4]:
        args += ["--input", _input(i, kind or "")]
    for flag, v in (("--voice", voice), ("--name", name), ("--aspect", aspect)):
        if v:
            args += [flag, str(v)[:80]]
    code, doc, err = _lab(args, timeout=60)
    if doc is None:
        return {"refused": err or "the lab didn't take the job",
                "say": "Tell Sir plainly why; don't switch to nolgia or any paid provider on your own."}
    j = doc
    deadline = time.time() + max(0, min(int(wait or 0), WAIT_MAX))
    while j.get("state") not in ("succeeded", "failed", "cancelled") and time.time() < deadline:
        time.sleep(2)
        code, d, err = _lab(["jobs", "logs", j["id"], "--json"])
        if d:
            j = d["job"]
    out = _brief(j)
    if j.get("state") == "succeeded":
        out["say"] = "Tell Sir what you made and where (outputs); it ran on the lab, for no credits."
    elif j.get("state") in ("queued", "waiting", "running"):
        out["say"] = "It's on the lab's queue; check it later with lab_jobs (job id)."
    return out


def lab_job_cancel(job):
    """Cancel one of his own lab jobs."""
    code, doc, err = _lab(["jobs", "logs", str(job), "--json"])
    if doc is None:
        raise ValueError(err or f"no job {job}")
    j = doc["job"]
    if j.get("by") != "vector":
        return {"refused": f"{j['id']} is Sir's job, not mine: he cancels it (bromigos lab jobs cancel {j['id']})"}
    code, d, err = _lab(["jobs", "cancel", j["id"], "--json"])
    return _brief(d) if d else {"error": err or "cancel didn't answer"}
