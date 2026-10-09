"""VECTOR's build loop: how he changes the desktop safely (widgets, live-layer layers and
decks, holograms, existing visualizations). Protected safety code (guard.py) is out of
reach at every step.

  begin(slug, title)     a worktree of ~/.dotfiles on branch vector/<slug>, under
                         ~/.local/share/bromigos/builds/<slug>; every write goes there
  read/write/edit/list   files inside that worktree only
  run(command)           his terminal (all its limits), with the worktree as cwd
  validate()             the diff against the base: guard.check_diff, py_compile, JSON,
                         GLSL compiled on the GPU, and an offscreen render of each widget,
                         layer and model (PNG + measurements + a vision-model look)
  apply(message, say)    TRIAL: copies the changed files into the live dotfiles (originals
                         kept), reloads what needs it, and starts the trial watcher
                         (python -m holo.vector.build watch), a separate process that rolls
                         the trial back if a host crashes, the change errors or blows its
                         frame budget, or nobody keeps it within 10 minutes
  keep()                 the host said keep: commit on the branch (message in the dotfiles
                         style), restore the live files, fast-forward master to the branch,
                         push, remove the worktree. Only after a host turn later than the trial
  revert()               restore the originals, reload, keep the patch, remove the worktree
State: ~/.local/state/bromigos/vector-build.json (one build at a time).
"""
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
DOT = os.environ.get("VECTOR_BUILD_DOT") or os.path.join(HOME, ".dotfiles")       # overridable for tests
BUILDS = os.environ.get("VECTOR_BUILD_DIR") or os.path.join(HOME, ".local/share/bromigos/builds")
STATE = os.environ.get("VECTOR_BUILD_STATE") or os.path.join(HOME, ".local/state/bromigos/vector-build.json")
LOCK = STATE + ".lock"
LOG = os.path.join(HOME, ".local/state/bromigos/vector-build.log")
TRIAL_S = 600
WIDGETS = "linux/.config/bromigos/widgets/"
LIVE = "linux/.config/bromigos-live/"
HOLO = "linux/.config/bromigos/holo/"
HYPR = "linux/.config/hypr/"
MODELS = "linux/.config/bromigos/vector/holo/"          # his hologram collection (tools/holo/collection.py)
RUN = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
LAST_USER = {"t": 0.0}                    # the brain sets it at each host turn


class BuildError(Exception):
    pass


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}\n")


def _git(repo, *args, timeout=60, check=True):
    env = dict(os.environ, GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=15", GIT_TERMINAL_PROMPT="0")
    env.pop("SSH_AUTH_SOCK", None) if "/gcr/" in env.get("SSH_AUTH_SOCK", "") else None
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=timeout, env=env,
                       stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise BuildError(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout


def state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE)


class _Lock:
    def __enter__(self):
        os.makedirs(os.path.dirname(LOCK), exist_ok=True)
        self.f = open(LOCK, "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def _wt():
    st = state()
    if not st.get("worktree") or st.get("phase") not in ("building", "validated"):
        raise BuildError("no build in progress (begin first)")
    return st, st["worktree"]


def _inside(wt, path):
    rel = path.lstrip("/") if not path.startswith(("~", "/")) else os.path.relpath(os.path.expanduser(path), DOT)
    p = os.path.realpath(os.path.join(wt, rel))
    if not p.startswith(os.path.realpath(wt) + os.sep):
        raise BuildError(f"{path}: outside the build's worktree")
    from .guard import protected
    if protected(os.path.relpath(p, wt)):
        raise BuildError(f"{path} is protected safety code; VECTOR never edits it")
    return p, os.path.relpath(p, wt)


# ------------------------------------------------------------------ begin, files, run
def begin(slug, title, kind="desktop"):
    slug = re.sub(r"[^a-z0-9-]+", "-", (slug or "").lower()).strip("-")[:40]
    if not slug:
        raise BuildError("slug: a short name like gpu-gauge")
    with _Lock():
        st = state()
        if st.get("phase") in ("building", "validated", "trial"):
            raise BuildError(f"build {st.get('slug')} is still {st['phase']}; finish or revert it first")
        wt = os.path.join(BUILDS, slug)
        if os.path.exists(wt):
            _git(DOT, "worktree", "remove", "--force", wt, check=False)
            shutil.rmtree(wt, ignore_errors=True)
        _git(DOT, "branch", "-D", f"vector/{slug}", check=False)
        base = _git(DOT, "rev-parse", "HEAD").strip()
        os.makedirs(BUILDS, exist_ok=True)
        _git(DOT, "worktree", "add", "-q", "-b", f"vector/{slug}", wt, base)
        _save({"slug": slug, "title": title, "kind": kind, "phase": "building", "worktree": wt, "base": base,
               "started": time.time()})
    log(f"begin {slug}: {title}")
    from . import events
    events.emit("task.progress", task=slug, title=title, step="begin", note="worktree ready", state="running", pct=5)
    return {"ok": True, "slug": slug, "worktree": wt, "branch": f"vector/{slug}",
            "note": "paths are relative to the dotfiles root, e.g. linux/.config/bromigos/widgets/plugins/gpu_gauge.py"}


def read(path, start=1, lines=300):
    _, wt = _wt()
    rel = os.path.relpath(os.path.expanduser(path), DOT) if path.startswith(("~", "/")) else path.lstrip("/")
    p = os.path.realpath(os.path.join(wt, rel))
    if not p.startswith(os.path.realpath(wt) + os.sep):
        raise BuildError(f"{path}: outside the build's worktree")
    with open(p, errors="replace") as f:
        all_ = f.readlines()
    s = max(1, int(start))
    return {"path": rel, "total_lines": len(all_), "from": s, "text": "".join(all_[s - 1:s - 1 + min(int(lines), 600)])}


def write(path, content):
    st, wt = _wt()
    p, rel = _inside(wt, path)
    if len(content) > 200_000:
        raise BuildError("file too large (200 KB max)")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(content)
    if rel.endswith((".py",)) and "/bin/" in rel:
        os.chmod(p, 0o755)
    st["phase"] = "building"
    _save(st)
    return {"ok": True, "path": rel, "bytes": len(content.encode())}


def edit(path, old, new, count=1):
    st, wt = _wt()
    p, rel = _inside(wt, path)
    with open(p) as f:
        text = f.read()
    n = text.count(old)
    if n == 0:
        raise BuildError("old text not found (read the file first; match exactly)")
    if n > 1 and count == 1:
        raise BuildError(f"old text occurs {n} times; include more context")
    with open(p, "w") as f:
        f.write(text.replace(old, new, count if count > 0 else n))
    st["phase"] = "building"
    _save(st)
    return {"ok": True, "path": rel, "replaced": min(n, count) if count > 0 else n}


def listdir(path="linux/.config/bromigos"):
    _, wt = _wt()
    p = os.path.realpath(os.path.join(wt, path.lstrip("/")))
    if not p.startswith(os.path.realpath(wt)):
        raise BuildError("outside the worktree")
    out = []
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]
        for f in files:
            out.append(os.path.relpath(os.path.join(root, f), wt))
        if len(out) > 400:
            break
    return {"path": path, "files": sorted(out)[:400]}


def run(command, timeout_s=120):
    _, wt = _wt()
    from .shell import RUNNER
    # bubblewrap: the live dotfiles are read-only here, only this build's worktree is writable
    return RUNNER.run(command, cwd=wt, timeout_s=timeout_s, writable_only=os.path.realpath(wt))


# ------------------------------------------------------------------ diff and validation
def _diff(wt, base):
    _git(wt, "add", "-A", "-N")                               # untracked files show in the diff
    names = [l for l in _git(wt, "diff", "--name-only", base).splitlines() if l.strip()]
    added = []
    for f in names:
        d = _git(wt, "diff", "-U0", base, "--", f, check=False)
        added += [(f, l[1:]) for l in d.splitlines() if l.startswith("+") and not l.startswith("+++")]
    return names, added


def _fingerprint(wt, names):
    h = hashlib.sha256()
    for f in sorted(names):
        p = os.path.join(wt, f)
        h.update(f.encode())
        try:
            with open(p, "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"<deleted>")
    return h.hexdigest()[:16]


def _review(png, question):
    try:
        from .nolgia import nolgia_review
        return nolgia_review(png, question)["review"]
    except Exception as e:
        return f"(no vision review: {type(e).__name__}: {str(e)[:80]})"


def validate(look=True):
    st, wt = _wt()
    names, added = _diff(wt, st["base"])
    if not names:
        raise BuildError("nothing changed yet")
    from .guard import check_diff
    problems = check_diff(names, added)
    checks, renders = [], []
    out_dir = os.path.join(BUILDS, f"{st['slug']}-renders")
    os.makedirs(out_dir, exist_ok=True)
    for f in names:
        p = os.path.join(wt, f)
        if not os.path.exists(p):
            checks.append({"file": f, "check": "deleted"})
            continue
        if f.endswith(".py") or (f.startswith(WIDGETS) and "." not in os.path.basename(f)):
            r = subprocess.run([sys.executable if f.startswith(HOLO) else "/usr/bin/python3", "-m", "py_compile", p],
                               capture_output=True, text=True, timeout=30)
            ok = r.returncode == 0
            checks.append({"file": f, "check": "py_compile", "ok": ok, **({} if ok else {"error": r.stderr.strip()[-400:]})})
            if not ok:
                problems.append(f"{f}: does not compile")
        if f.endswith(".json"):
            try:
                json.load(open(p))
                checks.append({"file": f, "check": "json", "ok": True})
            except ValueError as e:
                problems.append(f"{f}: invalid JSON: {e}")
        # renders
        if f.startswith(WIDGETS + "plugins/") and f.endswith(".py"):
            out = os.path.join(out_dir, os.path.basename(f)[:-3] + ".png")
            r = subprocess.run(["/usr/bin/python3", os.path.join(wt, WIDGETS, "bromigos-widgets"), "render", p, out,
                                "--ticks", "3"], capture_output=True, text=True, timeout=90)
            lines = (r.stdout or "").strip().splitlines()
            if r.returncode != 0 or not lines or not lines[-1].startswith("{"):
                problems.append(f"{f}: offscreen render failed: {(r.stderr or r.stdout or 'no output').strip()[-400:]}")
            else:
                m = json.loads(lines[-1])
                if m.get("regions", 0) == 0:
                    problems.append(f"{f}: no hover regions (every element explains itself: self.region(...))")
                if m.get("regions_without_hint"):
                    problems.append(f"{f}: {m['regions_without_hint']} hover regions without text")
                if m.get("regions_out_of_bounds"):
                    problems.append(f"{f}: {m['regions_out_of_bounds']} hover regions reach outside the panel")
                problems += [f"{f}: {x}" for x in lint_panel(p)]
                if m.get("draw_ms", 0) > 40:
                    problems.append(f"{f}: draw takes {m['draw_ms']} ms (budget 40 ms)")
                renders.append({"file": f, "png": out, **m})
        if f.startswith(LIVE + "layers/") and f.endswith(".frag"):
            out = os.path.join(out_dir, os.path.basename(f)[:-5] + ".png")
            r = subprocess.run(["/usr/bin/python3", "-m", "live.plugins", "test-layer", p, out], capture_output=True,
                               text=True, timeout=120, cwd=os.path.join(wt, LIVE))
            lines = (r.stdout or "").strip().splitlines()
            if r.returncode != 0 or not lines or not lines[-1].startswith("{"):
                err = [l for l in (r.stderr or "").splitlines() if "error" in l.lower()][-3:]
                problems.append(f"{f}: GLSL: {' | '.join(err) or (r.stderr or 'no output').strip()[-300:]}")
            else:
                m = json.loads(lines[-1])
                if not m.get("within_budget"):
                    problems.append(f"{f}: {m.get('gpu_ms')} ms GPU a frame, over its budget {m.get('budget_ms')} ms")
                renders.append({"file": f, "png": out, **m})
        if f.startswith(MODELS) and f.endswith(".holo.npz"):
            name = os.path.basename(f)[:-9]
            out = os.path.join(out_dir, name + ".png")
            env = dict(os.environ, BROMIGOS_HOLO_MODELS=os.path.join(wt, MODELS))
            from ..paths import ROOT as _VEC                # this VECTOR tree renders it (packaged or a checkout)
            r = subprocess.run([sys.executable, os.path.join(_VEC, "tools/offscreen.py"), "gallery", out, name, "0.35"],
                               capture_output=True, text=True, timeout=180, env=env, cwd=_VEC)
            if r.returncode != 0 or not os.path.exists(out):
                problems.append(f"{f}: offscreen gallery render failed: {(r.stderr or '').strip()[-300:]}")
            else:
                renders.append({"file": f, "png": out, "model": name})
    if look:
        for r in renders:
            q = ("This is an offscreen render of a new element for a retro sci-fi desktop (phosphor green on near-black, "
                 f"thin vector lines). The goal: {st['title']}. In two or three sentences: what does it show, does it "
                 "match the goal, is anything broken (clipped or overlapping text, empty areas, unreadable labels, "
                 "garbled shapes)?")
            r["review"] = _review(r["png"], q)
    fp = _fingerprint(wt, names)
    st.update(phase="validated" if not problems else "building", validated=fp if not problems else None,
              files=names, renders=[r["png"] for r in renders])
    _save(st)
    from . import events
    events.emit("task.progress", task=st["slug"], title=st["title"], step="validate",
                note="passed" if not problems else f"{len(problems)} problems", state="running", pct=70)
    return {"ok": not problems, "files": names, "problems": problems, "checks": checks, "renders": renders,
            "next": "apply (trial)" if not problems else "fix the problems, then validate again"}


DRAW_IO = ("CDLL", "nvmlInit", "nvmlDeviceGetHandle", "open", "urlopen", "Popen", "run", "check_output", "sleep",
           "get_json", "connect", "read_text", "System", "GPU", "Net", "storage", "cpu_percent", "virtual_memory")


def lint_panel(path):
    """House rules a plugin's source can be checked for without running it."""
    import ast
    probs = []
    try:
        tree = ast.parse(open(path).read())
    except SyntaxError as e:
        return [f"syntax error: {e}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("draw", "render"):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                    if name in DRAW_IO or name.startswith("nvml"):
                        probs.append(f"draw() calls {name}(): sample in tick() (or __init__), draw only what tick() read")
        if isinstance(node, ast.FunctionDef) and node.name not in ("__init__",):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                    if name in ("CDLL", "nvmlInit_v2", "nvmlInit") and node.name != "__init__":
                        probs.append(f"{node.name}() loads/initialises NVML every call; do it once in __init__ (or use S.GPU())")
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            probs.append(f"line {node.lineno}: bare except: hides failures; catch the specific error")
    return sorted(set(probs))


# ------------------------------------------------------------------ trial
def _hosts(names):
    """What must be reloaded for these files, and how."""
    h = set()
    for f in names:
        if f.startswith(WIDGETS):
            if not f.startswith(WIDGETS + "plugins/"):
                h.add("widgets")
        elif f.startswith(LIVE):
            if not f.startswith((LIVE + "layers/", LIVE + "plugins/")):
                h.add("live")
        elif f.startswith(HOLO + "holo/"):
            h.add("holo")
        elif f.startswith("linux/.config/waybar/"):
            h.add("waybar")
    return sorted(h)


def _reload(hosts):
    for h in hosts:
        try:
            if h == "widgets":
                subprocess.run([os.path.join(HOME, ".config/bromigos/widgets/bromigos-widgets"), "reload"], timeout=10)
            elif h == "live":
                subprocess.run([os.path.join(HOME, ".config/bromigos-live/bin/bromigos-live"), "restart"], timeout=30,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif h == "holo":
                subprocess.Popen([os.path.join(HOME, ".config/bromigos/holo/bin/bromigos-holo"), "restart"],
                                 start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif h == "waybar":
                subprocess.run(["pkill", "-SIGUSR2", "-x", "waybar"], timeout=5)
        except Exception as e:
            log(f"reload {h} failed: {e}")


def _hyprctl(*args):
    try:
        return subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception as e:
        return f"hyprctl failed: {e}"


@contextlib.contextmanager
def _hypr_still(names):
    """Hyprland reloads by itself whenever a file it loads changes, so files written one by
    one can reload it half-written and trip its emergency mode. For a change under hypr/:
    auto-reload off first, write, verify the config, one reload (which also brings
    auto-reload back from the config), then check configerrors and the bind count."""
    if not any(f.startswith(HYPR) for f in names or ()):
        yield None
        return
    out = _hyprctl("keyword", "misc:disable_autoreload", "true")
    if out.strip().lower() != "ok":
        _hyprctl("eval", "hl.config({ misc = { disable_autoreload = true } })")
    report = {}
    try:
        yield report
    finally:
        hl = shutil.which("Hyprland") or shutil.which("hyprland") or "Hyprland"
        try:
            v = subprocess.run([hl, "--verify-config", "-c", os.path.join(HOME, ".config/hypr/hyprland.lua")],
                               capture_output=True, text=True, timeout=60)
            report["verify"] = (v.stdout + v.stderr).strip()[-300:]
        except Exception as e:
            report["verify"] = f"verify failed to run: {e}"
        _hyprctl("reload")
        time.sleep(1.0)
        report["configerrors"] = _hyprctl("configerrors")[:300]
        try:
            report["binds"] = len(json.loads(_hyprctl("binds", "-j") or "[]"))
        except ValueError:
            report["binds"] = None
        log(f"hyprland: verify={report['verify'][-80:]!r} configerrors={report['configerrors'][:80]!r} binds={report['binds']}")


def apply(message, say):
    """Trial: put the validated change live; the watcher takes it from here."""
    if not re.match(r"(Added|Updated|Fixed): \S", message or ""):
        raise BuildError("message: the dotfiles style, e.g. 'Added: a GPU temperature and fan gauge panel'")
    with _Lock():
        st, wt = _wt()
        names, _ = _diff(wt, st["base"])
        if st.get("phase") != "validated" or st.get("validated") != _fingerprint(wt, names):
            raise BuildError("validate the current change first (it changed since, or failed)")
        dirty = set(l[3:] for l in _git(DOT, "status", "--porcelain=v1").splitlines())
        clash = [f for f in names if f in dirty]
        if clash:
            raise BuildError(f"Sir has uncommitted changes in {clash}; not touching them")
        moved = [f for f in names if _git(DOT, "diff", "--name-only", st["base"], "HEAD", "--", f).strip()]
        if moved:
            raise BuildError(f"{moved} changed on master since the build began; begin again from the new master")
        originals = {}
        for f in names:
            live, new = os.path.join(DOT, f), os.path.join(wt, f)
            if os.path.exists(live):
                with open(live, "rb") as fh:
                    originals[f] = fh.read().hex()
            else:
                originals[f] = None
        with _hypr_still(names) as hypr:
            for f in names:
                live, new = os.path.join(DOT, f), os.path.join(wt, f)
                if os.path.exists(new):
                    os.makedirs(os.path.dirname(live), exist_ok=True)
                    shutil.copy2(new, live)
                elif os.path.exists(live):
                    os.unlink(live)
        hosts = _hosts(names)
        st.update(phase="trial", message=message, say=say, originals=originals, hosts=hosts, trial_at=time.time(),
                  deadline=time.time() + TRIAL_S, files=names)
        _save(st)
    log(f"trial {st['slug']}: {names} hosts={hosts}")
    _reload(hosts)
    subprocess.Popen([sys.executable, "-m", "holo.vector.build", "watch"],
                     cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=open(LOG, "a"))
    from . import events
    events.emit("task.progress", task=st["slug"], title=st["title"], step="trial", note=say, state="running", pct=90)
    return {"ok": True, "trial": True, "files": names, "reloaded": hosts, "deadline_s": TRIAL_S,
            **({"hyprland": hypr} if hypr is not None else {}), "say": say + " Keep it?"}


def _restore(st):
    for f, hexdata in (st.get("originals") or {}).items():
        live = os.path.join(DOT, f)
        if hexdata is None:
            if os.path.exists(live):
                os.unlink(live)
        else:
            os.makedirs(os.path.dirname(live), exist_ok=True)
            with open(live, "wb") as fh:
                fh.write(bytes.fromhex(hexdata))


def _cleanup(st, keep_branch=False):
    wt = st.get("worktree")
    if wt:
        _git(DOT, "worktree", "remove", "--force", wt, check=False)
        shutil.rmtree(wt, ignore_errors=True)
    if not keep_branch:
        _git(DOT, "branch", "-D", f"vector/{st['slug']}", check=False)


def finish(decision, why=""):
    """keep | revert, for the tools and the watcher alike (one at a time, under the lock)."""
    from . import events
    with _Lock():
        st = state()
        if st.get("phase") != "trial":
            return {"ok": False, "detail": f"no trial running (phase {st.get('phase')})"}
        slug, wt = st["slug"], st["worktree"]
        if decision == "keep":
            try:
                _git(wt, "add", "-A")
                _git(wt, "-c", "user.useConfigOnly=true", "commit", "-q", "-m",
                     st["message"] + f"\n\nBuilt by VECTOR in trial mode and kept by Sir ({st['title']}).")
                with _hypr_still(st.get("files")):
                    _restore(st)                                 # back to the base, then fast-forward
                    if _git(DOT, "rev-parse", "HEAD").strip() != st["base"]:
                        _git(wt, "rebase", "-q", _git(DOT, "rev-parse", "HEAD").strip())   # master moved on meanwhile
                    _git(DOT, "merge", "--ff-only", "-q", f"vector/{slug}")
                sha = _git(DOT, "rev-parse", "--short=8", "HEAD").strip()
                push = subprocess.run(["git", "-C", DOT, "push", "-q"], capture_output=True, text=True, timeout=90,
                                      env=dict(os.environ, GIT_SSH_COMMAND="ssh -o BatchMode=yes", GIT_TERMINAL_PROMPT="0"))
                pushed = push.returncode == 0
                _cleanup(st)
                st.update(phase="kept", sha=sha, pushed=pushed, ended=time.time(), originals=None)
                _save(st)
                log(f"kept {slug} as {sha} pushed={pushed}")
                events.emit("git.push", repo="blackflame007/dotfiles", branch="master", sha=sha, ok=pushed)
                events.emit("task.progress", task=slug, title=st["title"], step="kept", note=sha, state="done", pct=100)
                return {"ok": True, "kept": slug, "commit": sha, "pushed": pushed,
                        **({} if pushed else {"push_error": push.stderr.strip()[-200:]})}
            except BuildError as e:
                log(f"keep {slug} failed: {e}; reverting")
                decision, why = "revert", f"keep failed: {e}"
        # revert
        with _hypr_still(st.get("files")):
            _restore(st)
        patch = os.path.join(BUILDS, f"{slug}.patch")
        try:
            with open(patch, "w") as f:
                f.write(_git(wt, "diff", st["base"], check=False))
        except Exception:
            pass
        _reload(st.get("hosts") or [])
        _cleanup(st)
        st.update(phase="reverted", why=why, ended=time.time(), originals=None, patch=patch)
        _save(st)
        log(f"reverted {slug}: {why}")
        events.emit("task.progress", task=slug, title=st["title"], step="reverted", note=why[:120], state="failed", pct=100)
        return {"ok": True, "reverted": slug, "why": why, "patch": patch}


def keep():
    st = state()
    if st.get("phase") != "trial":
        raise BuildError("no trial to keep")
    if LAST_USER["t"] <= st.get("trial_at", 0):
        raise BuildError("Sir hasn't answered since the trial went up; ask him first")
    return finish("keep")


def revert(why="Sir said revert"):
    return finish("revert", why)


def stop(why="stopped"):
    """Abandon a build that hasn't gone live (no trial): remove the worktree."""
    with _Lock():
        st = state()
        if st.get("phase") == "trial":
            pass
        elif st.get("phase") in ("building", "validated"):
            _cleanup(st)
            st.update(phase="stopped", why=why, ended=time.time())
            _save(st)
            return {"ok": True, "stopped": st["slug"]}
        else:
            return {"ok": False, "detail": "no build running"}
    return finish("revert", why)


def status():
    st = state()
    out = {k: st.get(k) for k in ("slug", "title", "phase", "files", "hosts", "message", "sha", "pushed", "why")}
    if st.get("phase") == "trial":
        out["seconds_left"] = max(0, int(st["deadline"] - time.time()))
    return out


# ------------------------------------------------------------------ the trial watcher (its own process)
def _health_problem(st, started):
    names, hosts = st.get("files") or [], st.get("hosts") or []
    now = time.time()
    plugs = [os.path.basename(f)[:-3] for f in names if f.startswith(WIDGETS + "plugins/") and f.endswith(".py")]
    if plugs or "widgets" in hosts:
        try:
            h = json.load(open(os.path.join(RUN, "bromigos-widgets-health.json")))
            if now - h["t"] > 20 and now - started > 20:
                return "the widgets stopped answering (no heartbeat for 20 s)"
            for p in plugs:
                ph = (h.get("plugins") or {}).get(p)
                if ph and ph.get("status") in ("off", "load_failed"):
                    return f"widget {p}: {ph.get('disabled') or ph.get('last_error')}"
                if ph and ph.get("errors_last_minute", 0) >= 3:
                    return f"widget {p} keeps failing: {ph.get('last_error')}"
        except (OSError, ValueError, KeyError):
            if now - started > 30:
                return "the widgets aren't running"
    layers = [os.path.basename(f)[:-5] for f in names if f.startswith(LIVE + "layers/") and f.endswith(".frag")]
    if layers or "live" in hosts:
        try:
            h = json.load(open(os.path.join(RUN, "bromigos-live-plugins.json")))
            for name in layers:
                lh = (h.get("layers") or {}).get(name)
                if lh and lh.get("status") == "off":
                    return f"layer {name}: {lh.get('reason')}"
            if "live" in hosts and now - h["t"] > 30 and now - started > 40:
                return "the live layer stopped answering"
        except (OSError, ValueError, KeyError):
            if "live" in hosts and now - started > 40:
                return "the live layer isn't running"
    if "holo" in hosts and now - started > 40:
        r = subprocess.run(["pgrep", "-f", "holo.app"], capture_output=True)
        if r.returncode != 0:
            return "VECTOR's own daemon isn't running after the change"
    return None


def watch():
    st = state()
    if st.get("phase") != "trial":
        return
    started = time.time()
    slug = st["slug"]
    log(f"watcher up for {slug}")
    while True:
        time.sleep(2)
        st = state()
        if st.get("phase") != "trial" or st.get("slug") != slug:
            log(f"watcher for {slug}: trial ended ({st.get('phase')})")
            return
        bad = _health_problem(st, started)
        if bad:
            log(f"auto-rollback {slug}: {bad}")
            finish("revert", f"auto-rollback: {bad}")
            _notify(f"Rolled back the {st['title']} trial: {bad}.")
            return
        if time.time() > st["deadline"]:
            finish("revert", "no answer within 10 minutes")
            _notify(f"Nobody kept the {st['title']} trial, so I rolled it back.")
            return


def _notify(text):
    """Spoken in the notify voice when the daemon is up; a desktop notification always."""
    subprocess.run(["notify-send", "-a", "VECTOR", "VECTOR · build", text], timeout=5, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    try:
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(os.path.join(RUN, "bromigos-holo.sock"))
        s.sendall(("say " + text).encode())
        s.recv(1024)
        s.close()
    except OSError:
        pass


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "watch":
        watch()
    elif len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(status(), indent=1))
