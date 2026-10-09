#!/usr/bin/env python3
"""Sync the operator's software documentation into Gnosis as a knowledge base.

    tools/kb-sync.py [--space kb-nolgia] [--dry-run]

Spaces (tenant bromigos; user_id = space; agent kb-ingest; through the gnosis-gate with
the kb_ingest_token, which can write nothing else):
  kb-bromigos  ~/github.com/bromigos-org/* (except homelab) + platform agents/LORE.md (canon lore)
  kb-nolgia    ~/github.com/nolgiainc/*   (Nolgia, the operator's other company)
  kb-personal  ~/github.com/blackflame007/*
  kb-desktop   ~/.dotfiles: AGENTS.md, every tracked .md under bromigos/ and bromigos-live/ (the desktop
               map, the shared skills, component READMEs, docs), the keybind table, and the
               docstrings of the desktop's own Python (widgets, holo, waybar scripts)
  kb-homelab   ~/github.com/bromigos-org/homelab
New clones under those directories are picked up on the next run.

Sources per repo: README*, AGENTS.md, CLAUDE.md and docs/**/*.md that git tracks (so
gitignored files never go), minus vendored/generated dirs and anything secret-looking.
Chunks follow headings (about 350 tokens each), written verbatim (infer=false) with
metadata {repo, path, heading, sha, url}. A chunk's key is sha1(space, repo, path,
heading, n); the state file maps keys to Gnosis memory ids and content hashes, so a run
adds new or changed chunks, deletes changed and vanished ones, and leaves the rest alone.
Embeddings come from Gnosis's own model on the lab.
"""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")
sys.path.insert(0, os.path.join(HOME, ".config/bromigos/lib"))
import bromigos_private as PRIV  # noqa: E402  the operator's private values (empty on a fresh clone)

GATE = PRIV.url("gnosis_gate")                     # private: endpoints.gnosis_gate
CA = os.path.join(HOME, ".config/homelab/homelab-ca.crt")
TOKEN = os.path.join(HOME, ".local/share/bromigos/gnosis-kb-ingest-token")
STATE = os.path.join(HOME, ".local/state/bromigos/kb-sync.json")
LOG = os.path.join(HOME, ".local/state/bromigos/kb-sync.log")
GH = os.path.join(HOME, "github.com")
DOC = re.compile(r"(^|/)(README[^/]*\.md|AGENTS\.md|CLAUDE\.md)$|^docs/.*\.md$", re.I)
SKIP = re.compile(r"(^|/)(node_modules|dist|build|\.venv|venv|vendor|third_party|\.next|target|__pycache__|"
                  r"site-packages|\.git|coverage|out)/", re.I)
SECRETISH = re.compile(r"(secret|token|credential|password|passwd|\.env|private|\.key$|\.pem$|kubeconfig|vault)", re.I)
MAX_FILE = 300_000
CHUNK = 1500          # characters (~350 tokens)
WORKERS = 6
CTX = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()


def log(*a):
    line = time.strftime("%Y-%m-%dT%H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


# ------------------------------------------------------------------ sources
def repos(org_dir, exclude=()):
    if not os.path.isdir(org_dir):
        return []
    return [os.path.join(org_dir, d) for d in sorted(os.listdir(org_dir))
            if os.path.isdir(os.path.join(org_dir, d, ".git")) and d not in exclude]


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=30).stdout


def repo_meta(repo, cache={}):
    if repo in cache:
        return cache[repo]
    sha = git(repo, "rev-parse", "HEAD").strip()[:12]
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() or "main"
    url = None
    remote = git(repo, "remote", "get-url", "origin").strip()
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", remote)
    if m:
        slug = m.group(1)
        try:      # only link public repos
            vis = subprocess.run(["gh", "repo", "view", slug, "--json", "visibility", "-q", ".visibility"],
                                 capture_output=True, text=True, timeout=20).stdout.strip()
            if vis == "PUBLIC":
                url = f"https://github.com/{slug}/blob/{branch}/"
        except Exception:
            pass
    cache[repo] = (sha, url)
    return cache[repo]


def tracked_docs(repo):
    out = []
    for p in git(repo, "ls-files").splitlines():
        if DOC.search(p) and not SKIP.search(p) and not SECRETISH.search(p):
            f = os.path.join(repo, p)
            if os.path.isfile(f) and 0 < os.path.getsize(f) <= MAX_FILE:
                out.append(p)
    return out


def sources():
    """-> {space: [(repo_name, repo_path, rel_path, text)]}"""
    spaces = {s: [] for s in ("kb-bromigos", "kb-nolgia", "kb-personal", "kb-desktop", "kb-homelab")}

    def add(space, repo, rel, name=None):
        try:
            with open(os.path.join(repo, rel), errors="replace") as f:
                spaces[space].append((name or os.path.basename(repo), repo, rel, f.read()))
        except OSError:
            pass
    for space, org, excl in (("kb-bromigos", "bromigos-org", ("homelab",)), ("kb-nolgia", "nolgiainc", ()),
                             ("kb-personal", "blackflame007", ())):
        for r in repos(os.path.join(GH, org), excl):
            for rel in tracked_docs(r):
                add(space, r, rel)
    plat = os.path.join(GH, "bromigos-org/platform")
    if os.path.exists(os.path.join(plat, "agents/LORE.md")):
        add("kb-bromigos", plat, "agents/LORE.md")
    hl = os.path.join(GH, "bromigos-org/homelab")
    if os.path.isdir(hl):
        for rel in tracked_docs(hl):
            add("kb-homelab", hl, rel)
    dot = os.path.join(HOME, ".dotfiles")
    for rel in ("AGENTS.md", "README.md", "linux/.config/bromigos/holo/README.md", "linux/.config/bromigos/brand/README.md",
                "linux/.config/bromigos/vector/holo/README.md", "linux/.config/bromigos/holo/voices/README.md"):
        if os.path.exists(os.path.join(dot, rel)):
            add("kb-desktop", dot, rel, "dotfiles")
    seen = {r for _, _, r, _ in spaces["kb-desktop"]}
    # every tracked markdown file of the desktop: the desktop map (bromigos/README.md), the
    # shared skills, each component's README, the live layer's docs; new ones join by themselves
    for rel in git(dot, "ls-files", "linux/.config/bromigos", "linux/.config/bromigos-live").splitlines():
        if rel.endswith(".md") and rel not in seen and not SKIP.search(rel) and not SECRETISH.search(rel):
            add("kb-desktop", dot, rel, "dotfiles")
            seen.add(rel)
    for rel in git(dot, "ls-files", "linux/.config/bromigos", "linux/.config/waybar/scripts").splitlines():
        if rel.endswith(".py") and not SKIP.search(rel) and not SECRETISH.search(rel):
            doc = py_docs(os.path.join(dot, rel), rel)
            if doc:
                spaces["kb-desktop"].append(("dotfiles", dot, rel, doc))
    try:      # the live keybind table, as the desktop sees it
        kbpy = next((p for p in ("/usr/lib/bromigos/widgets/keybinds.py",            # packaged: bromigos-widgets
                                 os.path.join(dot, "linux/.config/bromigos/widgets/keybinds.py")) if os.path.isfile(p)), "")
        kb = subprocess.run([sys.executable, kbpy],
                            capture_output=True, text=True, timeout=20).stdout
        if kb.strip():
            spaces["kb-desktop"].append(("dotfiles", dot, "keybinds (live, from the Hyprland config)", "# Keybinds\n\n" + kb))
    except Exception:
        pass
    return spaces


def py_docs(path, rel):
    """A desktop module's docstrings as markdown: the module's, then each class's and
    public function's (what the code says it does, not the code). Panels are headed by
    their on-screen title, e.g. "WORKBENCH panel"."""
    import ast
    try:
        with open(path, errors="replace") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):
        return ""
    out = [f"# {rel}"]
    mod = ast.get_docstring(tree)
    if mod:
        out.append(mod)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            title = None
            for st in node.body:      # name, title = "workbench", "WORKBENCH" / title = "LAB"
                if isinstance(st, ast.Assign) and isinstance(st.value, (ast.Tuple, ast.Constant)):
                    names = [t.id for t in (st.targets[0].elts if isinstance(st.targets[0], ast.Tuple) else st.targets)
                             if isinstance(t, ast.Name)]
                    vals = st.value.elts if isinstance(st.value, ast.Tuple) else [st.value]
                    for nm, v in zip(names, vals):
                        if nm == "title" and isinstance(v, ast.Constant) and isinstance(v.value, str):
                            title = v.value
            doc = ast.get_docstring(node)
            methods = [(m.name, ast.get_docstring(m)) for m in node.body
                       if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and not m.name.startswith("_")
                       and ast.get_docstring(m)]
            if doc or methods:
                head = f"{title} panel ({node.name})" if title and node.name.endswith("Panel") else node.name
                out.append(f"## {head}")
                if doc:
                    out.append(doc)
                out += [f"- {n}: {d.splitlines()[0]}" for n, d in methods]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            doc = ast.get_docstring(node)
            if doc and len(doc) >= 40:
                out.append(f"## {node.name}()\n{doc}")
    return "\n\n".join(out) if len(out) > 1 else ""


# ------------------------------------------------------------------ chunking
HEAD = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$", re.M)


def chunks(text):
    """Split by headings into ~CHUNK-character pieces: (heading path, body)."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    parts, last, stack = [], 0, []
    marks = [(m.start(), len(m.group(1)), m.group(2).strip()) for m in HEAD.finditer(text)]
    sections = []
    pos_head = (0, 0, "")
    for start, level, title in marks + [(len(text), 0, None)]:
        body = text[pos_head[0]:start].strip()
        if body:
            sections.append((list(stack), body))
        if title is None:
            break
        stack = [h for h in stack if h[0] < level] + [(level, title)]
        pos_head = (start, level, title)
    for st, body in sections:
        heading = " › ".join(t for _, t in st) or "(top)"
        if len(body) <= CHUNK * 1.3:
            parts.append((heading, body))
            continue
        buf = ""
        for para in re.split(r"\n\s*\n", body):
            if buf and len(buf) + len(para) > CHUNK:
                parts.append((heading, buf.strip()))
                buf = ""
            while len(para) > CHUNK * 1.5:
                parts.append((heading, para[:CHUNK]))
                para = para[CHUNK:]
            buf += para + "\n\n"
        if buf.strip():
            parts.append((heading, buf.strip()))
    # merge tiny neighbours under the same heading
    merged = []
    for h, b in parts:
        if merged and merged[-1][0] == h and len(merged[-1][1]) + len(b) < CHUNK * 0.6:
            merged[-1] = (h, merged[-1][1] + "\n\n" + b)
        else:
            merged.append((h, b))
    return [(h, b) for h, b in merged if len(re.sub(r"\W", "", b)) >= 40]


# ------------------------------------------------------------------ gnosis
class Gnosis:
    def __init__(self):
        with open(TOKEN) as f:
            self.tok = f.read().strip()

    def call(self, method, path, body, timeout=60):
        for attempt in range(3):
            req = urllib.request.Request(GATE + path, data=json.dumps(body).encode(), method=method,
                                         headers={"Authorization": "Bearer " + self.tok, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 404 and method == "DELETE":
                    return {}
                if e.code < 500 or attempt == 2:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise
            time.sleep(2 * (attempt + 1))


def scope(space):
    return {"tenant_id": "bromigos", "space_id": space, "agent_id": "kb-ingest", "session_id": "kb-sync",
            "user_id": space, "visibility": "agent_shared"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--space")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    try:
        with open(STATE) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    g = None if a.dry_run else Gnosis()
    srcs = sources()
    report = {}
    for space, files in srcs.items():
        if a.space and space != a.space:
            continue
        want = {}
        for name, repo, rel, text in files:
            sha, url = repo_meta(repo) if os.path.isdir(os.path.join(repo, ".git")) else ("", None)
            for n, (heading, body) in enumerate(chunks(text)):
                key = hashlib.sha1(f"{space}|{name}|{rel}|{heading}|{n}".encode()).hexdigest()[:20]
                content = f"[{name} / {rel} — {heading}]\n{body}"
                want[key] = {"content": content, "hash": hashlib.sha1(content.encode()).hexdigest()[:16],
                             "meta": {"repo": name, "path": rel, "heading": heading[:200], "sha": sha,
                                      "url": (url + rel) if url and not rel.startswith("keybinds") else None,
                                      "source": "kb-sync"}}
        have = state.get(space, {})
        add = [k for k in want if k not in have or have[k]["hash"] != want[k]["hash"]]
        drop = [k for k in have if k not in want or have[k]["hash"] != want[k]["hash"]]
        report[space] = {"files": len(files), "chunks": len(want), "add": len(add), "drop": len(drop)}
        log(space, report[space])
        if a.dry_run:
            continue

        def delete(k):
            g.call("DELETE", f"/v1/memories/{have[k]['id']}", {"scope": scope(space)})
            return k

        def write(k):
            w = want[k]
            d = g.call("POST", "/v1/memories", {"scope": scope(space), "content": w["content"], "infer": False,
                                                 "metadata": {k2: v for k2, v in w["meta"].items() if v}})
            mid = (d.get("results") or [{}])[0].get("memory_id")
            return k, mid
        with cf.ThreadPoolExecutor(WORKERS) as ex:
            for k in ex.map(delete, drop):
                have.pop(k, None)
            done = 0
            for k, mid in ex.map(write, add):
                if mid:
                    have[k] = {"id": mid, "hash": want[k]["hash"]}
                done += 1
                if done % 200 == 0:
                    log(space, f"{done}/{len(add)} written")
                    state[space] = have
                    with open(STATE, "w") as f:
                        json.dump(state, f)
        state[space] = have
        with open(STATE, "w") as f:
            json.dump(state, f)
    log("done", json.dumps(report), f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
