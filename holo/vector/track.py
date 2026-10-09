"""VECTOR always tracks his changes. This module keeps him honest:

  * touch(path): every terminal command reports the repos it worked in (its cwd, `git -C`
    dirs, and existing paths it names). The first touch of a repo records a baseline: the
    files already dirty there and the commits already unpushed, which are the host's own
    work and never his to commit.
  * changes_check(): before he reports a task done. For every repo he touched since the
    daemon started: files he changed and left uncommitted, and commits he made and didn't
    push (beyond the baseline). Plus changes made outside any repo (Vault writes, an
    imperative scale) since the last entry in ~/.dotfiles/VECTOR-CHANGELOG.md. He must
    clear it: commit and push, or record it.
  * github_repo_create(): a new repo under the right owner, private by default, cloned
    under ~/github.com/<owner>/<name>, seeded (README, AGENTS.md, .gitignore), pushed.
"""
import hashlib
import json
import os
import re
import subprocess
import threading
import time

HOME = os.path.expanduser("~")
CHANGELOG = os.path.join(HOME, ".dotfiles/VECTOR-CHANGELOG.md")
OWNERS = {"bromigos-org": "Bromigos (the org, its products and the lore)", "nolgiainc": "Nolgia, the company",
          "blackflame007": "Sir's personal projects"}
OUTSIDE = ("vault_put", "vault_copy", "k8s_scale")      # changes that live outside any repo
_lock = threading.Lock()
_repos = {}            # toplevel -> {"dirty": {path: hash}, "unpushed": set(shas), "t": first touch}
_outside = []          # (time, tool, summary)


def _git(repo, *args, timeout=15):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=timeout,
                       stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:200])
    return r.stdout


def toplevel(path):
    p = os.path.realpath(os.path.expanduser(path))
    d = p if os.path.isdir(p) else os.path.dirname(p)
    try:
        top = _git(d, "rev-parse", "--show-toplevel", timeout=5).strip()
        return top or None
    except Exception:
        return None


def _dirty(repo):
    out = {}
    for line in _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0"):
        if len(line) < 4:
            continue
        path = line[3:]
        f = os.path.join(repo, path)
        try:
            with open(f, "rb") as fh:
                h = hashlib.sha1(fh.read()).hexdigest()
        except OSError:
            h = "gone"
        out[path] = (line[:2], h)
    return out


def _unpushed(repo):
    try:
        return set(_git(repo, "log", "--format=%H", "--branches", "--not", "--remotes").split())
    except Exception:
        return set()


def touch(path):
    top = toplevel(path)
    if not top or top in _repos:
        return top
    try:
        base = {"dirty": {p: h for p, (_, h) in _dirty(top).items()}, "unpushed": _unpushed(top), "t": time.time()}
    except Exception:
        return None
    with _lock:
        _repos.setdefault(top, base)
    return top


def touch_from_command(words, cwd):
    """Repos a terminal command worked in: its cwd, git -C dirs, and existing paths it names."""
    cands = {cwd}
    for i, w in enumerate(words):
        if w == "-C" and i + 1 < len(words):
            cands.add(os.path.join(cwd, os.path.expanduser(words[i + 1])))
        elif ("/" in w or w.startswith("~")) and len(w) < 300:
            p = os.path.join(cwd, os.path.expanduser(w))
            if os.path.exists(p) or os.path.exists(os.path.dirname(p)):
                cands.add(p)
    for c in cands:
        try:
            touch(c)
        except Exception:
            pass


def note_outside(tool, args):
    if tool in OUTSIDE:
        with _lock:
            _outside.append((time.time(), tool, json.dumps(args)[:120]))


def changes_check():
    """What he changed and hasn't committed, pushed or recorded."""
    problems, clean = [], []
    for repo, base in sorted(_repos.items()):
        if not os.path.isdir(repo):
            continue
        try:
            now = _dirty(repo)
            unpushed = _unpushed(repo)
            branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        except Exception as e:
            problems.append({"repo": repo, "error": str(e)[:120]})
            continue
        mine = sorted(p for p, (_, h) in now.items() if base["dirty"].get(p) != h)
        new_commits = unpushed - base["unpushed"]
        host_dirty = sorted(p for p in now if p not in mine)
        name = repo.replace(HOME, "~")
        if mine or new_commits:
            item = {"repo": name, "branch": branch}
            if mine:
                item["uncommitted"] = mine[:30]
            if new_commits:
                item["unpushed_commits"] = len(new_commits)
            if host_dirty:
                item["host_own_uncommitted"] = f"{len(host_dirty)} files (not yours: leave them)"
            problems.append(item)
        else:
            clean.append(name)
    try:
        logged = os.path.getmtime(CHANGELOG)
    except OSError:
        logged = 0.0
    unrecorded = [f"{tool} {args}" for t, tool, args in _outside if t > logged]
    if unrecorded:
        problems.append({"outside_repos": unrecorded[:10],
                         "do": "record each in ~/.dotfiles/VECTOR-CHANGELOG.md (what, where, why, how to undo), "
                               "or make it GitOps, then commit and push the dotfiles"})
    return {"clear": not problems, "problems": problems, "clean_repos": clean,
            "rule": "commit with a clear message in the repo's style and push; never commit Sir's own "
                    "uncommitted files, secrets, .env files or keys"}


# ------------------------------------------------------------------ new repos
GITIGNORE = """# OS and editors
.DS_Store
*.swp
.idea/
.vscode/

# secrets: never commit these
.env
.env.*
!.env.example
*.pem
*.key

# Python
__pycache__/
*.pyc
.venv/
venv/

# Node
node_modules/
.next/
dist/
build/

# Rust
target/
"""


def github_repo_create(owner, name, description, public=False):
    if owner not in OWNERS:
        raise ValueError("owner: bromigos-org (Bromigos), nolgiainc (Nolgia, the company) or blackflame007 "
                         "(personal); ask Sir when it isn't clear")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name or ""):
        raise ValueError("name: letters, digits, . _ -")
    desc = " ".join((description or "").split())[:300]
    if not desc:
        raise ValueError("a one-line description is required")
    dest = os.path.join(HOME, "github.com", owner, name)
    if os.path.exists(dest):
        raise ValueError(f"{dest} already exists")
    vis = "--public" if public is True else "--private"
    r = subprocess.run(["gh", "repo", "create", f"{owner}/{name}", vis, "--description", desc],
                       capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-300:])
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    env = dict(os.environ, GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=15", GIT_TERMINAL_PROMPT="0")
    env.pop("SSH_AUTH_SOCK", None)
    c = subprocess.run(["git", "clone", "-q", f"git@github.com:{owner}/{name}.git", dest], capture_output=True,
                       text=True, timeout=60, env=env, stdin=subprocess.DEVNULL)
    if c.returncode != 0:
        os.makedirs(dest, exist_ok=True)
        _git(dest, "init", "-q", "-b", "main")
        _git(dest, "remote", "add", "origin", f"git@github.com:{owner}/{name}.git")
    files = {
        "README.md": f"# {name}\n\n{desc}\n",
        "AGENTS.md": f"# {name}\n\n## Purpose\n\n{desc}\n\n## Layout\n\n- `README.md` — what this is\n\n"
                     "## Rules\n\n- Never commit secrets, `.env` files or keys; secrets live in the homelab Vault.\n"
                     "- Commit every change with a clear message and push it.\n",
        ".gitignore": GITIGNORE,
    }
    for f, text in files.items():
        with open(os.path.join(dest, f), "w") as fh:
            fh.write(text)
    _git(dest, "add", "README.md", "AGENTS.md", ".gitignore")
    _git(dest, "commit", "-q", "-m", "Initial commit: README, AGENTS.md, .gitignore")
    p = subprocess.run(["git", "-C", dest, "push", "-q", "-u", "origin", "HEAD:main"], capture_output=True, text=True,
                       timeout=60, env=env, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise RuntimeError("created and seeded, but the push failed: " + p.stderr.strip()[-200:])
    touch(dest)
    from . import events
    events.emit("git.push", repo=f"{owner}/{name}", branch="main", sha=_git(dest, "rev-parse", "--short=8", "HEAD").strip(),
                ok=True)
    return {"ok": True, "repo": f"{owner}/{name}", "visibility": "public" if public is True else "private",
            "path": dest.replace(HOME, "~"), "url": f"https://github.com/{owner}/{name}",
            "next": "add CI if it has code; file it to memory; it joins the knowledge-base sync by itself"}
