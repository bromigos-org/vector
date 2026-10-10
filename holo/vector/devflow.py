"""VECTOR's own working copies and pull requests: he proposes changes the way a developer does.

  * His clones live in ~/.local/share/bromigos/vector/work/<name> (WORK), one per repo in
    REPOS: his own code (vector), bromigOS, the homelab repo, the extensions catalog and the
    dotfiles. His sandbox makes the source checkouts under ~/github.com read-only to him;
    these clones are his to write.
  * He works there as his own GitHub account, bromigodev (ACCOUNT): its token is the field
    github_token_bromigodev of his own Vault credentials (vault.own_field), handed to his
    sandbox as GH_TOKEN_BROMIGODEV like his other github_token_* fields (shell.sandbox_env).
    git asks for it through a credential helper set in each clone, gh through his shim. His
    commits carry bromigodev's noreply address. The token is never printed or returned.
  * Every git command here runs through his terminal (shell.RUNNER): the same sandbox, the
    same refusals, the same audit log, the same emergency switch. Nothing here runs a
    clone's code or git config outside the sandbox.
  * The tools: work_clone (clone, or fetch and rebase his branches), work_status,
    work_branch, work_commit (explicit paths only), work_push (rebases first), pr_open,
    pr_watch (CI on the PR's head) and pr_merge. A repo in REQUIRED_CHECKS (homelab: its
    `validate` check) is merged only once those checks pass: missing is never green.
    GitHub's API is reached here with urllib and his token.
  * pr_merge refuses a PR that touches the code behind his limits (LIMITS, below: one list
    per repo, the only place these paths are named; the CODEOWNERS files are made from it).
    GitHub doesn't enforce CODEOWNERS on these private repos on the free plan, so this
    refusal is the enforcement: such a PR is left open for the operator, with a comment
    saying so. His terminal refuses the other ways to reach main (shell_refusal: gh pr merge,
    the API's merge and contents writes, a push to main from a clone, and a push to main
    from any checkout that carries a LIMITS path).
"""
import json
import os
import re
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request

from . import money

HOME = os.path.expanduser("~")
WORK = os.path.join(HOME, ".local/share/bromigos/vector/work")
ACCOUNT = "bromigodev"
OPERATOR = "blackflame007"
TOKEN_FIELD = "github_token_bromigodev"
TOKEN_ENV = "GH_TOKEN_BROMIGODEV"
API = "https://api.github.com"
BRANCH_PREFIX = "vector/"

# his clones: the folder name in WORK -> the GitHub repo
REPOS = {
    "vector": "bromigos-org/vector",
    "bromigOS": "bromigos-org/bromigOS",
    "homelab": "bromigos-org/homelab",
    "extensions": "bromigos-org/extensions",
    "dotfiles": "blackflame007/.dotfiles",
}


def _vector_limits():
    from .guard import HOLO_FILES
    return list(HOLO_FILES) + [
        "holo/vector/devflow.py", "tools/test-devflow.py",   # this file: the merge refusal itself
        "lib/privacy_guard.py",                        # the privacy check shared with the dotfiles' hook
    ]


def _dotfiles_limits():
    from .guard import PROTECTED
    return list(PROTECTED) + [
        "linux/.config/bromigos/lib/privacy_guard.py", ".githooks/", ".sops.yaml", "private",
    ]


# The code behind his limits, per repo: a path, or a folder ending in "/". A PR that touches
# any of them (or renames one) is the operator's to merge. In every repo: the workflows (CI is
# the green light pr_merge waits for, and the publishing workflows hold the signing and bucket
# secrets) and CODEOWNERS.
LIMITS = {
    # his safety code (guard.HOLO_FILES: the terminal's sandbox, secret and real-money
    # refusals, switch.py, guard.py, the tool allowlists, the act/Vault/nolgia limits, the
    # browser's limits and their tests) and the packaging scripts that publish with secrets
    "bromigos-org/vector": _vector_limits() + ["packaging/ci/"],
    # the emergency switch's bar, key and Settings page; the plugin isolation of the live layer
    # and the widgets; his homelab tools and their data layer; the release privacy check
    "bromigos-org/bromigOS": [
        "desktop/waybar/scripts/vector-switch.py", "desktop/hypr/bromigos/vector.lua",
        "desktop/settings/pages/vector.py", "desktop/tests/test_vector_guard.py",
        "live/live/plugins.py", "widgets/plugins.py", "homelab/vector/", "homelab/lib/",
        "tools/release/privacy_guard.py", "packaging/ci/",
    ],
    # his Vault policy and Vault itself; his vector-operator RBAC and admission policies; the
    # read-only ServiceAccount (and the real-money workloads, from vector.toml, below)
    "bromigos-org/homelab": [
        "ansible/roles/vault/", "helm/vault/", "scripts/vault-unseal.sh",
        "ansible/inventory/group_vars/all/vault.yml",
        "helm/vector/", "gitops/argocd-apps/vector.yaml", "helm/pilot/templates/rbac.yaml",
    ],
    # the catalog's checks (permissions against parts)
    "bromigos-org/extensions": ["tools/check.py"],
    # the desktop's protected files (guard.PROTECTED), the public repo's privacy guard and
    # its hook, the sops rules and the private overlay
    "blackflame007/.dotfiles": _dotfiles_limits(),
}
# the user's real-money code (vector.toml [real_money.code], money.py) joins a repository's list;
# a repository without a list is all the operator's already, so it gets none (that would open it)
for _slug, _code in money.CODE.items():
    if _slug in LIMITS:
        LIMITS[_slug] += [p for p in _code if p not in LIMITS[_slug]]
for _paths in LIMITS.values():
    _paths += [".github/", "CODEOWNERS", "docs/CODEOWNERS"]


def touches_limits(slug, files):
    """-> the files (paths in the repo) that are code behind his limits."""
    rules = LIMITS.get(slug)
    if rules is None:
        return sorted(set(files))                  # a repo without a list: all of it is the operator's
    hit = set()
    for f in files:
        f = (f or "").lstrip("/")
        for p in rules:
            if f == p.rstrip("/") or (p.endswith("/") and f.startswith(p)) or ("*" in p and money.matches(f, p)):
                hit.add(f)
    return sorted(hit)


def codeowners(slug):
    """The CODEOWNERS text for a repo, made from LIMITS."""
    lines = ["# The code behind VECTOR's limits: the operator reviews and merges every change to it.",
             "# Made from LIMITS in bromigos-org/vector holo/vector/devflow.py: bromigos-holo work codeowners",
             f"# {slug.split('/')[1]}. VECTOR's merge tool refuses a pull request that touches these paths.", ""]
    for p in LIMITS[slug]:
        lines.append(f"/{p} @{OPERATOR}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ GitHub, as bromigodev
def _token():
    from .vault import own_field
    t = own_field(TOKEN_FIELD)
    if not t:
        raise RuntimeError(f"no GitHub credential for {ACCOUNT} yet: the field {TOKEN_FIELD} of my own Vault "
                           "credentials isn't there or isn't readable. The host adds it. Nothing ran.")
    return t


def _api(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + _token(), "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "bromigos-vector"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read() or b"{}").get("message", "")
        except ValueError:
            msg = ""
        raise RuntimeError(f"GitHub answered {e.code} to {method} {path.split('?')[0]}: {msg[:200]}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"couldn't reach GitHub ({e.reason})") from None


_ID = {}


def identity():
    """(name, email) for his commits: bromigodev and its noreply address, from the API."""
    if not _ID:
        u = _api("GET", "/user")
        if u.get("login") != ACCOUNT:
            raise RuntimeError(f"the token in {TOKEN_FIELD} isn't {ACCOUNT}'s")
        _ID.update(name=ACCOUNT, email=f"{u['id']}+{ACCOUNT}@users.noreply.github.com")
    return _ID["name"], _ID["email"]


# ------------------------------------------------------------------ his clones
def _name(repo):
    r = (repo or "").strip().rstrip("/")
    for name, slug in REPOS.items():
        if r.lower() in (name.lower(), slug.lower(), slug.split("/")[1].lower()):
            return name
    raise ValueError(f"repo must be one of {', '.join(REPOS)}")


def _dir(name):
    return os.path.join(WORK, name)


def _sh(name_or_dir, cmd, timeout=120, ok_codes=(0,)):
    """One command in his terminal (sandbox, refusals, audit, switch); cwd is the clone (or WORK)."""
    from .shell import RUNNER
    cwd = name_or_dir if name_or_dir.startswith("/") else _dir(name_or_dir)
    res = RUNNER.run(cmd, cwd=cwd, timeout_s=timeout)
    if res.get("refused"):
        raise PermissionError(res["refused"])
    if res.get("error"):
        raise RuntimeError(res["error"])
    if res.get("exit") not in ok_codes:
        raise RuntimeError(f"`{cmd[:120]}` failed (exit {res.get('exit')}): {(res.get('output') or '').strip()[-500:]}")
    return (res.get("output") or "").strip()


def _q(s):
    return shlex.quote(s)


def _default(name):
    out = _sh(name, "git rev-parse --abbrev-ref origin/HEAD 2>/dev/null", ok_codes=(0, 128))
    return out.split("/", 1)[1] if out.startswith("origin/") else "main"


def _branch(name):
    return _sh(name, "git rev-parse --abbrev-ref HEAD")


def _dirty(name):
    return [line for line in _sh(name, "git status --porcelain=v1 --untracked-files=all").splitlines() if line.strip()]


def credential(name):
    """git's credential helper for a clone: his account's token, or for the operator's dotfiles
    (another owner's repo, which his fine-grained token can't reach) his public_repo token."""
    env = TOKEN_ENV + ("_DOTFILES" if REPOS.get(name) == "blackflame007/.dotfiles" else "")
    return ('!f() { test "$1" = get || exit 0; test -n "$' + env + '" || exit 0; echo username=' + ACCOUNT +
            '; echo "password=$' + env + '"; }; f')


CREDENTIAL = credential("vector")


def _configure(name):
    """His identity and credentials in a clone (re-set every time, so they can't drift)."""
    who, email = identity()
    cmds = [f"git config user.name {_q(who)}", f"git config user.email {_q(email)}",
            "git config --replace-all credential.helper ''", f"git config --add credential.helper {_q(credential(name))}",
            "git config push.autoSetupRemote true", "git config pull.rebase true"]
    if REPOS[name] == "blackflame007/.dotfiles":
        cmds.append("git config core.hooksPath .githooks")   # the privacy guard before every commit
    _sh(name, " && ".join(cmds))


def _clone(name):
    os.makedirs(WORK, exist_ok=True)
    who, email = identity()
    slug = REPOS[name]
    cmd = ("git clone -q --filter=blob:none --no-recurse-submodules "
           f"-c user.name={_q(who)} -c user.email={_q(email)} -c credential.helper= "
           f"-c credential.helper={_q(credential(name))} https://github.com/{slug}.git {_q(name)}")
    _sh(WORK, cmd, timeout=600)
    _configure(name)
    return {"repo": slug, "cloned": True, "path": _dir(name).replace(HOME, "~"), "branch": _default(name)}


def _rebasing(name):
    g = os.path.join(_dir(name), ".git")
    return os.path.isdir(os.path.join(g, "rebase-merge")) or os.path.isdir(os.path.join(g, "rebase-apply"))


def _update(name):
    """Fetch, bring the default branch up to date, and rebase each of his branches on it."""
    _configure(name)
    _sh(name, "git fetch -q --prune origin && git remote set-head origin -a >/dev/null", timeout=300)
    base = _default(name)
    cur = _branch(name)
    dirty = _dirty(name)
    out = {"repo": REPOS[name], "path": _dir(name).replace(HOME, "~"), "default": base, "branch": cur,
           "rebased": [], "conflicts": [], "deleted": []}
    if cur == base:
        _sh(name, f"git merge -q --ff-only origin/{_q(base)}", ok_codes=(0, 1, 128))
    else:
        _sh(name, f"git branch -f {_q(base)} origin/{_q(base)}", ok_codes=(0, 128))
    if dirty:
        out["note"] = f"{len(dirty)} uncommitted file(s): his branches weren't rebased (commit or discard them first)"
        return out
    mine = [b for b in _sh(name, "git for-each-ref --format='%(refname:short) %(upstream:track)' refs/heads").splitlines()
            if b.strip() and b.split()[0] != base]
    for line in mine:
        b = line.split()[0]
        r = _sh(name, f"git rebase -q origin/{_q(base)} {_q(b)}", ok_codes=(0, 1, 128))
        if _rebasing(name) or "CONFLICT" in r:
            _sh(name, "git rebase --abort", ok_codes=(0, 1, 128))
            out["conflicts"].append(b)
            continue
        ahead = _sh(name, f"git rev-list --count origin/{_q(base)}..{_q(b)}")
        if "[gone]" in line and ahead == "0":           # its PR was merged and the branch deleted
            _sh(name, f"git checkout -q {_q(base)} && git branch -q -D {_q(b)}")
            out["deleted"].append(b)
            if b == cur:
                cur = base
        else:
            out["rebased"].append(b)
    _sh(name, f"git checkout -q {_q(cur)}", ok_codes=(0, 1))
    out["branch"] = _branch(name)
    return out


def work_clone(repo="all"):
    """Clone a repo into his work folder, or bring an existing clone up to date (fetch, then
    rebase his branches). repo: one of REPOS, or all."""
    names = list(REPOS) if (repo or "all") == "all" else [_name(repo)]
    out = []
    for n in names:
        try:
            out.append(_update(n) if os.path.isdir(os.path.join(_dir(n), ".git")) else _clone(n))
        except PermissionError:
            raise
        except Exception as e:
            out.append({"repo": REPOS[n], "error": str(e)[:400]})
    return out[0] if len(out) == 1 else {"repos": out}


def _need(name):
    if not os.path.isdir(os.path.join(_dir(name), ".git")):
        raise ValueError(f"no clone of {REPOS[name]} yet: work_clone it first")
    _configure(name)


def work_status(repo="all"):
    """His clones: branch, uncommitted files, commits ahead of and behind the default branch,
    and his open pull requests."""
    names = list(REPOS) if (repo or "all") == "all" else [_name(repo)]
    out = []
    for n in names:
        if not os.path.isdir(os.path.join(_dir(n), ".git")):
            out.append({"repo": REPOS[n], "cloned": False})
            continue
        base, cur = _default(n), _branch(n)
        counts = _sh(n, f"git rev-list --left-right --count origin/{_q(base)}...HEAD").split()
        rec = {"repo": REPOS[n], "path": _dir(n).replace(HOME, "~"), "branch": cur, "default": base,
               "uncommitted": _dirty(n)[:30], "behind": int(counts[0]), "ahead": int(counts[1]),
               "branches": _sh(n, "git for-each-ref --format='%(refname:short)' refs/heads").split()}
        try:
            prs = _api("GET", f"/repos/{REPOS[n]}/pulls?state=open&per_page=50")
            rec["my_open_prs"] = [{"number": p["number"], "title": p["title"], "branch": p["head"]["ref"],
                                   "url": p["html_url"]} for p in prs if (p.get("user") or {}).get("login") == ACCOUNT]
        except RuntimeError as e:
            rec["prs_error"] = str(e)[:200]
        out.append(rec)
    return out[0] if len(out) == 1 else {"repos": out}


def work_branch(repo, name):
    """A new branch vector/<name> from the latest default branch; uncommitted changes come along."""
    n = _name(repo)
    _need(n)
    b = (name or "").strip()
    b = b if b.startswith(BRANCH_PREFIX) else BRANCH_PREFIX + b
    if not re.fullmatch(r"vector/[A-Za-z0-9][A-Za-z0-9._/-]{0,80}", b) or ".." in b or b.endswith((".lock", "/")):
        raise ValueError("name: letters, digits, . _ - / (e.g. fix-safety-typo)")
    _sh(n, "git fetch -q origin", timeout=300)
    base = _default(n)
    _sh(n, f"git checkout -q -b {_q(b)} --no-track origin/{_q(base)}")
    return {"repo": REPOS[n], "branch": b, "from": f"origin/{base}"}


# ------------------------------------------------------------------ files he reads back safely
def _read_in_git(name, fname, limit=4 << 20):
    """A file a sandboxed command wrote into the clone's .git: opened without following links,
    and only when it really is inside his work folder."""
    gd = os.path.join(_dir(name), ".git")
    dfd = os.open(gd, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        real = os.path.realpath(f"/proc/self/fd/{dfd}")
        if not real.startswith(os.path.realpath(WORK) + os.sep):
            raise PermissionError("that clone's .git isn't in my work folder")
        fd = os.open(fname, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)
        with os.fdopen(fd, "rb") as f:
            return f.read(limit).decode("utf-8", "replace")
    finally:
        os.close(dfd)


def _write_in_git(name, fname, text):
    gd = os.path.join(_dir(name), ".git")
    dfd = os.open(gd, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        real = os.path.realpath(f"/proc/self/fd/{dfd}")
        if not real.startswith(os.path.realpath(WORK) + os.sep):
            raise PermissionError("that clone's .git isn't in my work folder")
        try:
            os.unlink(fname, dir_fd=dfd)
        except FileNotFoundError:
            pass
        fd = os.open(fname, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dfd)
        with os.fdopen(fd, "w") as f:
            f.write(text)
    finally:
        os.close(dfd)


def _secret_lines(name, diff):
    """Reasons a patch can't leave: secret-looking values; in the public dotfiles, private details."""
    from .guard import PRIVATE
    from .shell import redact
    why, path = [], "?"
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else line[4:]
            continue
        if not line.startswith("+"):
            continue
        if redact(line) != line:
            why.append(f"{path}: a line looks like it carries a secret value")
        if REPOS[name] == "blackflame007/.dotfiles" and PRIVATE.search(line) and "privacy: allow" not in line:
            why.append(f"{path}: a private detail in the public dotfiles (put it in the private overlay)")
    return sorted(set(why))


def work_commit(repo, paths, message):
    """Commit exactly these paths (relative to the clone) on his current branch, never the
    default branch. New files are added; nothing else is staged."""
    n = _name(repo)
    _need(n)
    from .shell import REPO_SECRET, SECRET_FILE
    if isinstance(paths, str):
        paths = [paths]
    paths = [p.strip() for p in (paths or []) if p and p.strip()]
    if not paths:
        raise ValueError("name the files to commit (paths relative to the clone)")
    for p in paths:
        if p.startswith(("/", "~", "-")) or ".." in p.split("/") or p == ".git" or p.startswith(".git/") or \
                any(c in p for c in "*?[]"):
            raise ValueError(f"{p}: a plain path inside the clone (no globs, no .., not .git)")
        if REPO_SECRET.match(os.path.basename(p)) or SECRET_FILE.search(p):
            raise PermissionError(f"{p} looks like a secret or credential file")
    msg = (message or "").strip()
    if not msg:
        raise ValueError("a commit message is required, in the repo's own style")
    base, cur = _default(n), _branch(n)
    if cur in (base, "HEAD"):
        raise PermissionError(f"I'm on {cur}: commits go on a branch (work_branch), and reach {base} through a PR")
    args = " ".join(_q(p) for p in paths)
    _sh(n, f"git add -- {args}")
    _sh(n, f"git diff --cached --no-ext-diff --no-textconv -U0 -- {args} > .git/VECTOR_STAGED.diff")
    staged = _read_in_git(n, "VECTOR_STAGED.diff")
    why = _secret_lines(n, staged)
    if not staged.strip():
        why = why or ["nothing changed in those paths"]
    if why:
        _sh(n, f"git reset -q -- {args}", ok_codes=(0, 1))
        return {"refused": "; ".join(why[:6]), "repo": REPOS[n]}
    _write_in_git(n, "VECTOR_COMMIT_MSG", msg + "\n")
    out = _sh(n, f"git commit -q -F .git/VECTOR_COMMIT_MSG -- {args}", timeout=180)
    sha = _sh(n, "git rev-parse --short=8 HEAD")
    files = _sh(n, "git show --name-only --format= HEAD").split()
    res = {"repo": REPOS[n], "branch": cur, "sha": sha, "files": files}
    if out:
        res["hook"] = out[-400:]
    hits = touches_limits(REPOS[n], files)
    if hits:
        res["note"] = (f"this touches the code behind my limits ({', '.join(hits[:4])}): I can push it and open the "
                       "PR, but the operator merges it")
    return res


def work_push(repo):
    """Rebase his branch on the latest default branch, then push it to origin (never the default branch)."""
    n = _name(repo)
    _need(n)
    base, cur = _default(n), _branch(n)
    if cur in (base, "HEAD"):
        raise PermissionError(f"I'm on {cur}: only my branches are pushed; {base} changes through a PR")
    _sh(n, "git fetch -q origin", timeout=300)
    r = _sh(n, f"git rebase -q --autostash origin/{_q(base)}", ok_codes=(0, 1, 128))
    if _rebasing(n) or "CONFLICT" in r:
        _sh(n, "git rebase --abort", ok_codes=(0, 1, 128))
        return {"refused": f"rebasing {cur} on {base} conflicts; nothing was pushed. Resolve it in the clone, then push.",
                "repo": REPOS[n], "detail": r[-300:]}
    _sh(n, f"git log -p --no-ext-diff --no-textconv --format= origin/{_q(base)}..HEAD > .git/VECTOR_OUTGOING.diff")
    why = _secret_lines(n, _read_in_git(n, "VECTOR_OUTGOING.diff"))
    if why:
        return {"refused": "; ".join(why[:6]) + ". Nothing was pushed: amend the commit first.", "repo": REPOS[n]}
    ahead = _sh(n, f"git rev-list --count origin/{_q(base)}..HEAD")
    if ahead == "0":
        return {"refused": f"{cur} has no commits beyond {base}; nothing to push", "repo": REPOS[n]}
    _sh(n, f"git push -q --force-with-lease -u origin HEAD:refs/heads/{_q(cur)}", timeout=300)
    return {"repo": REPOS[n], "branch": cur, "pushed": _sh(n, "git rev-parse --short=8 HEAD"), "commits": int(ahead),
            "rebased_on": f"origin/{base}"}


# ------------------------------------------------------------------ pull requests
def _pr(slug, number):
    try:
        number = int(number)
    except (TypeError, ValueError):
        raise ValueError("number: the PR's number") from None
    return number, _api("GET", f"/repos/{slug}/pulls/{number}")


def _files(slug, number, expected):
    files, page = [], 1
    while True:
        batch = _api("GET", f"/repos/{slug}/pulls/{number}/files?per_page=100&page={page}")
        for f in batch:
            files.append(f["filename"])
            if f.get("previous_filename"):
                files.append(f["previous_filename"])
        if len(batch) < 100 or page >= 30:
            break
        page += 1
    if len({f for f in files}) < int(expected or 0):
        raise PermissionError("GitHub didn't list every file of this PR, so I can't check it: the operator merges it")
    return sorted(set(files))


def pr_open(repo, title, body):
    """Open a pull request from his current (pushed) branch into the default branch."""
    n = _name(repo)
    _need(n)
    slug, base, cur = REPOS[n], _default(n), _branch(n)
    if cur in (base, "HEAD"):
        raise PermissionError(f"I'm on {cur}: a PR comes from one of my branches")
    title = " ".join((title or "").split())[:200]
    if not title:
        raise ValueError("a short title is required")
    local = _sh(n, "git rev-parse HEAD")
    remote = _sh(n, f"git rev-parse -q --verify origin/{_q(cur)} 2>/dev/null", ok_codes=(0, 1, 128))
    if remote != local:
        raise RuntimeError(f"{cur} isn't pushed as it is here: work_push first")
    body = (body or "").strip() + f"\n\n---\nOpened by VECTOR ({ACCOUNT}) from the bromigOS desktop."
    pr = _api("POST", f"/repos/{slug}/pulls", {"title": title, "head": cur, "base": base, "body": body})
    files = _files(slug, pr["number"], pr.get("changed_files"))
    hits = touches_limits(slug, files)
    out = {"repo": slug, "number": pr["number"], "url": pr["html_url"], "branch": cur, "base": base, "files": files[:40]}
    if hits:
        _leave_for_operator(slug, pr["number"], hits)
        out["note"] = (f"it touches the code behind my limits ({', '.join(hits[:4])}); it's left for the operator to "
                       "review and merge, and I've said so on the PR")
    return out


def _leave_for_operator(slug, number, hits):
    """A comment on the PR, once, and a review request for the operator (best effort)."""
    mark = "<!-- vector:left-for-operator -->"
    try:
        comments = _api("GET", f"/repos/{slug}/issues/{number}/comments?per_page=100")
        if not any(mark in (c.get("body") or "") for c in comments):
            files = "\n".join(f"- `{f}`" for f in hits[:20]) + ("\n- …" if len(hits) > 20 else "")
            _api("POST", f"/repos/{slug}/issues/{number}/comments",
                 {"body": f"{mark}\n@{OPERATOR}: this PR touches the code behind VECTOR's limits, so his merge tool "
                          f"won't merge it. It's yours to review and merge.\n\n{files}"})
    except RuntimeError:
        pass
    try:
        _api("POST", f"/repos/{slug}/pulls/{number}/requested_reviewers", {"reviewers": [OPERATOR]})
    except RuntimeError:
        pass


GOOD = {"success", "neutral", "skipped"}


def _ci(slug, sha):
    """The checks on a commit: (state, runs). state: green, red, pending or none."""
    runs = _api("GET", f"/repos/{slug}/commits/{sha}/check-runs?per_page=100").get("check_runs") or []
    statuses = (_api("GET", f"/repos/{slug}/commits/{sha}/status").get("statuses")) or []
    out = [{"name": r["name"], "status": r["status"], "conclusion": r.get("conclusion"), "url": r.get("html_url")}
           for r in runs]
    out += [{"name": s.get("context"), "status": "completed" if s.get("state") != "pending" else "in_progress",
             "conclusion": {"success": "success", "pending": None}.get(s.get("state"), "failure"),
             "url": s.get("target_url")} for s in statuses]
    if not out:
        return "none", out
    if any(r["status"] != "completed" for r in out):
        return "pending", out
    return ("green" if all(r["conclusion"] in GOOD for r in out) else "red"), out


def _age(pr):
    try:
        t = time.strptime(pr.get("updated_at") or pr.get("created_at"), "%Y-%m-%dT%H:%M:%SZ")
        return time.time() - __import__("calendar").timegm(t)
    except (TypeError, ValueError):
        return 0


NO_CI_GRACE = 150     # seconds: a PR's checks usually appear within a minute
POLL_S = 15

# Checks a repo's PRs must pass, by check-run name, before pr_merge merges one. GitHub's free
# plan can't require a check on a private repo, so this is the enforcement. Without them, "no
# checks within NO_CI_GRACE" counts as nothing to wait for; with them, a missing check is
# waited for (pending) and never merged past. homelab: Argo CD deploys whatever lands on
# master, and its workflow .github/workflows/validate.yml (job `validate`) is the gate.
REQUIRED_CHECKS = {
    "bromigos-org/homelab": ["validate"],
}


def _required(slug, state, runs):
    """-> (state, missing): the state with the repo's required checks applied, and the
    required checks that haven't passed yet."""
    need = REQUIRED_CHECKS.get(slug) or []
    missing = []
    for name in need:
        mine = [r for r in runs if r["name"] == name]
        if not mine:
            missing.append(name)
            if state != "red":
                state = "pending"
        elif any(r["status"] != "completed" for r in mine):
            missing.append(name)
            if state != "red":
                state = "pending"
        elif not all(r["conclusion"] in ("success",) for r in mine):
            missing.append(name)
            state = "red"
    return state, missing


def pr_watch(repo, number, timeout_s=900):
    """Wait for CI on a PR's head commit: green, red, or none (the repo runs no checks for it).
    A repo with REQUIRED_CHECKS is never none: its checks are waited for until they finish."""
    slug = REPOS[_name(repo)]
    t0 = time.monotonic()
    while True:
        number, pr = _pr(slug, number)
        sha = pr["head"]["sha"]
        state, runs = _ci(slug, sha)
        state, missing = _required(slug, state, runs)
        waited = time.monotonic() - t0
        if state in ("green", "red") or (state == "none" and max(waited, _age(pr)) >= NO_CI_GRACE) or \
                waited >= min(int(timeout_s or 900), 1800):
            break
        time.sleep(POLL_S)
    try:
        from . import events
        events.emit("ci.result", repo=slug, sha=sha[:8], workflow=f"PR #{number}", status=state,
                    conclusion=state, url=pr["html_url"])
    except Exception:
        pass
    return {"repo": slug, "number": number, "sha": sha[:8], "ci": state, "runs": runs[:20], "url": pr["html_url"],
            "waited_s": round(time.monotonic() - t0),
            **({"note": "no checks ran for this PR: the repo's workflows don't cover these files"} if state == "none" else {}),
            **({"required": REQUIRED_CHECKS[slug], "not_passed": missing} if slug in REQUIRED_CHECKS else {})}


ARRIVES = {
    "bromigos-org/vector": "It's on main now. The packages workflow publishes bromigos-vector to the edge channel; "
                           "a machine on edge gets it with its next `bromigos update` (rc and stable with the next release).",
    "bromigos-org/bromigOS": "It's on main now. Its packages publish to the edge channel when the change is in a packaged "
                             "folder; a machine on edge gets it with its next `bromigos update` (rc and stable with "
                             "the next release).",
    "bromigos-org/homelab": "It's on master now. Argo CD applies it to the lab (argocd_wait to confirm); a change "
                            "under ansible/ applies when a playbook runs.",
    "bromigos-org/extensions": "It's in the catalog on main now. Machines see it when they fetch the catalog again "
                               "(at most six hours, or at once with `bromigos ext search`).",
    "blackflame007/.dotfiles": "It's on master now. This desktop's ~/.dotfiles gets it with a `git pull` there; "
                               "`bromigos update` doesn't pull the dotfiles.",
}


def pr_merge(repo, number, timeout_s=900):
    """Merge one of his own PRs once CI is green. Refused for a PR that touches the code behind
    his limits (LIMITS): that one is left open for the operator."""
    n = _name(repo)
    slug = REPOS[n]
    number, pr = _pr(slug, number)
    if pr.get("state") != "open" or pr.get("merged"):
        raise ValueError(f"PR #{number} isn't open")
    if (pr.get("user") or {}).get("login") != ACCOUNT:
        raise PermissionError(f"PR #{number} isn't mine ({(pr.get('user') or {}).get('login')}'s): its author or the "
                              "operator merges it")
    if (pr.get("head") or {}).get("repo", {}).get("full_name") != slug:
        raise PermissionError("that PR comes from another repository: the operator merges it")
    files = _files(slug, number, pr.get("changed_files"))
    hits = touches_limits(slug, files)
    if hits:
        _leave_for_operator(slug, number, hits)
        return {"refused": (f"PR #{number} touches the code behind my limits ({', '.join(hits[:5])}"
                            f"{', …' if len(hits) > 5 else ''}). I never merge that myself: it's left open for the "
                            "operator to review and merge, and the PR says so."),
                "repo": slug, "number": number, "url": pr["html_url"], "limits": hits}
    w = pr_watch(n, number, timeout_s)
    if w.get("not_passed") or (slug in REQUIRED_CHECKS and w["ci"] != "green"):
        names = ", ".join(w.get("not_passed") or REQUIRED_CHECKS[slug])
        return {"refused": f"{slug}'s required check ({names}) hasn't passed on PR #{number} (CI is {w['ci']}); "
                           "not merged. Fix what it reports, push, and merge again.",
                "repo": slug, "number": number, "runs": w["runs"], "url": pr["html_url"]}
    if w["ci"] in ("red", "pending"):
        return {"refused": f"CI on PR #{number} is {w['ci']}; not merged", "repo": slug, "number": number,
                "runs": w["runs"], "url": pr["html_url"]}
    number, pr = _pr(slug, number)
    if pr["head"]["sha"][:8] != w["sha"]:
        return {"refused": "the PR changed while CI ran; watch it again, then merge", "repo": slug, "number": number}
    for _ in range(6):                               # GitHub computes mergeability in the background
        if pr.get("mergeable") is not None:
            break
        time.sleep(5)
        number, pr = _pr(slug, number)
    if pr.get("mergeable") is False or pr.get("mergeable_state") == "dirty":
        return {"refused": f"PR #{number} conflicts with {pr['base']['ref']}: work_push rebases it", "repo": slug,
                "number": number}
    if touches_limits(slug, _files(slug, number, pr.get("changed_files"))):
        return {"refused": "the PR changed and now touches the code behind my limits: left for the operator",
                "repo": slug, "number": number}
    # rebase: his commits land as they are, authored by bromigodev's noreply address (a squash
    # would be authored with the account's own email)
    m = _api("PUT", f"/repos/{slug}/pulls/{number}/merge", {"merge_method": "rebase", "sha": pr["head"]["sha"]})
    branch = pr["head"]["ref"]
    out = {"repo": slug, "number": number, "merged": bool(m.get("merged")), "sha": (m.get("sha") or "")[:8],
           "ci": w["ci"], "url": pr["html_url"], "arrives": ARRIVES.get(slug, "")}
    if branch.startswith(BRANCH_PREFIX):
        try:
            _api("DELETE", f"/repos/{slug}/git/refs/heads/{urllib.parse.quote(branch, safe='/')}")
        except RuntimeError:
            pass
    try:
        if os.path.isdir(os.path.join(_dir(n), ".git")):
            out["clone"] = {k: v for k, v in _update(n).items() if k in ("branch", "deleted", "conflicts", "note")}
    except Exception as e:
        out["clone"] = f"not updated: {str(e)[:160]}"
    try:
        from . import events
        events.emit("git.push", repo=slug, branch=pr["base"]["ref"], sha=out["sha"], ok=out["merged"])
    except Exception:
        pass
    return out


def pr_close(repo, number, comment=""):
    """Close one of his own PRs without merging, and delete its branch."""
    slug = REPOS[_name(repo)]
    number, pr = _pr(slug, number)
    if (pr.get("user") or {}).get("login") != ACCOUNT:
        raise PermissionError(f"PR #{number} isn't mine")
    if (comment or "").strip():
        _api("POST", f"/repos/{slug}/issues/{number}/comments", {"body": comment.strip()[:4000]})
    _api("PATCH", f"/repos/{slug}/pulls/{number}", {"state": "closed"})
    branch = pr["head"]["ref"]
    if branch.startswith(BRANCH_PREFIX) and (pr.get("head") or {}).get("repo", {}).get("full_name") == slug:
        try:
            _api("DELETE", f"/repos/{slug}/git/refs/heads/{urllib.parse.quote(branch, safe='/')}")
        except RuntimeError:
            pass
    return {"repo": slug, "number": number, "closed": True}


# ------------------------------------------------------------------ his terminal: the other ways to main
_MAIN = {"main", "master"}


def _slug_of(d):
    import subprocess
    try:
        url = subprocess.run(["git", "-C", d, "config", "--get", "remote.origin.url"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else None


def _in_work(p):
    rp = os.path.realpath(p)
    w = os.path.realpath(WORK)
    return rp == w or rp.startswith(w + os.sep)


def _commands(command, depth=0):
    """The simple commands in a command line, in order, as lists of words (inside `sh -c '...'`
    too). bashlex where it's installed, else a split on ; & | and newlines."""
    out = []
    try:
        import bashlex

        class V(bashlex.ast.nodevisitor):
            def visitcommand(self, node, parts):
                out.append([p.word for p in parts if p.kind == "word"])
        for t in bashlex.parse(command):
            V().visit(t)
    except Exception:
        for part in re.split(r"[;&|\n]+", command):
            try:
                ws = shlex.split(part)
            except ValueError:
                ws = part.split()
            if ws:
                out.append(ws)
    more = []
    for ws in out:
        if depth < 2 and ws and os.path.basename(ws[0]) in ("sh", "bash", "zsh", "dash") and "-c" in ws[1:3]:
            k = ws.index("-c") + 1
            if k < len(ws):
                more += _commands(ws[k], depth + 1)
    return out + more


def _push_dsts(args, cur):
    """The branches a `git push <args>` updates (main for --all or --mirror)."""
    opts = [x for x in args if x.startswith("-")]
    pos = [x for x in args if not x.startswith("-")]
    dsts = ["main", "master"] if any(o in ("--all", "--mirror", "--branches") for o in opts) else []
    for spec in pos[1:]:
        spec = spec.lstrip("+")
        dst = spec.split(":", 1)[1] if ":" in spec else spec
        dst = cur if dst in ("HEAD", "@") else dst
        dsts.append(re.sub(r"^refs/heads/", "", dst))
    if len(pos) <= 1 and not dsts:
        dsts.append(cur)                                 # `git push` / `git push origin`: the current branch
    return dsts


def shell_refusal(command, cwd=None):
    """-> a refusal for a terminal command that would reach a default branch around pr_merge:
    gh pr merge; the API's merge, contents and git-data writes; a push to main or master from
    one of his clones; a push to main or master from any checkout whose outgoing commits touch
    LIMITS. None otherwise."""
    import subprocess
    d = cwd or HOME
    for ws in _commands(command):
        if not ws:
            continue
        b = os.path.basename(ws[0])
        if b in ("cd", "pushd") and len(ws) > 1:
            d = os.path.normpath(os.path.join(d, os.path.expanduser(ws[1])))
            continue
        for i, w in enumerate(ws):                       # also after env, xargs, timeout, …
            if os.path.basename(w) == "gh":
                rest = ws[i + 1:]
                if rest[:2] == ["pr", "merge"]:
                    return "merges go through my pr_merge tool (it leaves PRs that touch my limits to the operator)"
                if rest[:1] == ["api"]:
                    method = next((rest[j + 1] for j, x in enumerate(rest[:-1]) if x in ("-X", "--method")), "")
                    method = method or next((x.split("=", 1)[1] for x in rest if x.startswith("--method=")), "")
                    path = " ".join(rest)
                    writes = method.upper() in ("PUT", "POST", "PATCH", "DELETE") or any(
                        x in ("-f", "-F", "--field", "--raw-field", "--input") for x in rest)
                    if re.search(r"/pulls/\d+/merge\b|/merges\b", path) or (
                            writes and re.search(r"/contents/|/git/(refs|commits|trees|blobs)\b", path)):
                        return "changes reach a default branch only through a PR and my pr_merge tool"
            if os.path.basename(w) != "git":
                continue
            rest, gd = list(ws[i + 1:]), d
            while rest and rest[0].startswith("-") and rest[0] != "push":
                if rest[0] == "-C" and len(rest) > 1:
                    gd = os.path.normpath(os.path.join(gd, os.path.expanduser(rest[1])))
                rest = rest[2:] if rest[0] in ("-C", "-c", "--git-dir", "--work-tree") else rest[1:]
            if not rest or rest[0] != "push":
                continue
            try:
                cur = subprocess.run(["git", "-C", gd, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                                     text=True, timeout=5).stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                cur = ""
            to_main = [x for x in _push_dsts(rest[1:], cur) if x in ("main", "master")]
            if not to_main:
                continue
            if _in_work(gd):
                return ("in my clones, changes reach main only through a PR: work_push pushes my branch, pr_open and "
                        "pr_merge do the rest")
            slug = _slug_of(gd)
            if slug in LIMITS:
                try:
                    files = subprocess.run(["git", "-C", gd, "log", "--name-only", "--format=", "--branches", "--not",
                                            "--remotes"], capture_output=True, text=True, timeout=15).stdout.split()
                except (OSError, subprocess.TimeoutExpired):
                    return "couldn't check what that push contains"
                hits = touches_limits(slug, files)
                if hits:
                    return (f"that push takes the code behind my limits ({', '.join(hits[:3])}) to {to_main[0]}: "
                            "that's the operator's to merge; I open a PR instead")
    return None


# ------------------------------------------------------------------ bromigos-holo work …
def main(argv):
    import sys
    if not argv or argv[0] in ("-h", "--help", "help"):
        print("bromigos-holo work clone|update [repo|all]   clone VECTOR's working copies, or fetch and rebase them\n"
              "bromigos-holo work status [repo|all]         his clones, branches and open PRs\n"
              "bromigos-holo work limits [repo]             the paths his merge tool leaves to you\n"
              "bromigos-holo work codeowners <repo>         the CODEOWNERS file made from them\n"
              f"repos: {', '.join(REPOS)}")
        return 0
    verb, arg = argv[0], (argv[1] if len(argv) > 1 else "all")
    if verb in ("clone", "update"):
        out = work_clone(arg)
    elif verb == "status":
        out = work_status(arg)
    elif verb == "limits":
        out = {s: LIMITS[s] for s in ([REPOS[_name(arg)]] if arg != "all" else LIMITS)}
    elif verb == "codeowners":
        sys.stdout.write(codeowners(REPOS[_name(arg)]))
        return 0
    else:
        print(f"unknown verb {verb}; bromigos-holo work help", file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
