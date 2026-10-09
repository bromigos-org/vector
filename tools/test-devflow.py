#!/usr/bin/env python3
"""Tests for VECTOR's clones and pull requests (holo/vector/devflow.py) and the guard's scoping
of bare names to his clones. Offline: GitHub is a stub, and the "remote" is a bare repository
in a throwaway home. Run in the brain venv (python3 works too; CI runs it):
    ~/.local/share/bromigos/venv-brain/bin/python tools/test-devflow.py

1. LIMITS: switch.py, guard.py, shell.py, devflow.py, the workflows and a rename of one are
   the operator's; a docs change isn't. This repo's CODEOWNERS is the one LIMITS makes.
2. His terminal: in his clone, git and edits may name his clone's copies of protected files
   (shell.py, guard.py); the live ones stay refused by name from anywhere else, and through
   cd, .., ~ or $ from his clone. The switch's name, secret files and other keys stay refused.
   gh pr merge, the API's merge and contents writes, and a push to main from a clone are
   refused; so is a push to main from any checkout whose commits touch LIMITS.
3. His token: sandbox_env hands github_token_bromigodev over as GH_TOKEN_BROMIGODEV, and his
   gh uses it in his clones (the owner's token elsewhere). Made-up values only.
4. The merge tool (GitHub stubbed): a PR touching switch.py is refused and left for the
   operator (a comment, a review request, no merge call); someone else's PR is refused; red
   CI is refused; green CI merges with the head sha pinned.
5. With bubblewrap (his sandbox): work_branch, work_commit by explicit path (the default
   branch refused, a secret value refused, a token-shaped line in the dotfiles refused),
   work_push (rebases first, never main), all against a local bare repository.

Everything runs in a child process whose HOME and XDG_RUNTIME_DIR are temporary folders,
with its own slice (BROMIGOS_VECTOR_SLICE) and no display: nothing here reaches the running
VECTOR, his clones, GitHub or the screen."""
import os
import shutil
import subprocess
import sys
import tempfile

TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STUB_PRIVATE = '''
def data(): return {}
def present(): return False
def get(key, default=None): return default
def url(name, path="", default=""): return default
def vault(name, default=""): return default
def vault_root(default=""): return default
def lan_domain(default=""): return default
def path(name, default=""): return default
PATH = ""
'''

CHILD = r'''
import json, os, subprocess, sys
sys.path.insert(0, sys.argv[1])
from holo.vector import devflow, guard, shell
shell.vector_agent = lambda: None
HOME = os.path.expanduser("~")
WORK = devflow.WORK
bad = 0

def ok(cond, what):
    global bad
    print(("ok   " if cond else "FAIL ") + what)
    bad += 0 if cond else 1

def git(d, *a, env=None):
    r = subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, env=env)
    assert r.returncode == 0, (a, r.stderr)
    return r.stdout.strip()

E = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
         GIT_COMMITTER_EMAIL="t@example.invalid")

# ---------------------------------------------------------------- 1. LIMITS
V = "bromigos-org/vector"
for f in ("holo/vector/switch.py", "holo/vector/guard.py", "holo/vector/shell.py", "holo/vector/devflow.py",
          ".github/workflows/tests.yml", ".github/CODEOWNERS", "tools/test-switch.py", "lib/privacy_guard.py"):
    ok(devflow.touches_limits(V, ["docs/safety.md", f]) == [f], f"LIMITS: {f} is the operator's")
ok(devflow.touches_limits(V, ["docs/safety.md", "README.md", "skills/x.md"]) == [], "LIMITS: docs and skills are his")
ok(devflow.touches_limits("bromigos-org/homelab", ["helm/ledger/templates/live.yaml"]) != [],
   "LIMITS: a real-money workload from vector.toml (the made-up ledger's)")
ok(devflow.touches_limits("example-org/ledger", ["README.md"]) == ["README.md"],
   "LIMITS: real-money code never opens a repo that has no list")
ok(devflow.touches_limits("bromigos-org/homelab", ["helm/ledger/templates/paper.yaml"]) == [],
   "LIMITS: the rest of that workload's chart is his")
ok(devflow.touches_limits("bromigos-org/homelab", ["helm/vector/templates/rbac.yaml"]) != [], "LIMITS: vector-operator RBAC")
ok(devflow.touches_limits("bromigos-org/homelab", ["apps/foo/values.yaml"]) == [], "LIMITS: an ordinary lab app is his")
ok(devflow.touches_limits("someone/else", ["README.md"]) == ["README.md"], "LIMITS: an unknown repo is all the operator's")
with open(os.path.join(sys.argv[1], ".github/CODEOWNERS")) as f:
    ok(f.read() == devflow.codeowners(V), "this repo's .github/CODEOWNERS is the one LIMITS makes")

# ---------------------------------------------------------------- 2. his terminal
clone = os.path.join(WORK, "vector")
os.makedirs(os.path.join(clone, "holo/vector"), exist_ok=True)
git(clone, "init", "-q", "-b", "main")
git(clone, "remote", "add", "origin", "https://github.com/bromigos-org/vector.git")
for f in ("holo/vector/shell.py", "holo/vector/guard.py", "README.md"):
    open(os.path.join(clone, f), "w").write("x\n")
git(clone, "add", "-A"); git(clone, "commit", "-qm", "init", env=E)

def refused(cmd, cwd):
    try:
        shell.check(cmd, cwd)
        return None
    except shell.Refused as e:
        return str(e)

ALLOW = ["git status", "git add -- holo/vector/shell.py", "sed -i s/x/y/ holo/vector/guard.py",
         "git commit -q -F .git/VECTOR_COMMIT_MSG -- holo/vector/shell.py holo/vector/guard.py",
         "python3 -m py_compile holo/vector/shell.py", "cat README.md", "git diff -- holo/vector/guard.py",
         "git checkout -q -b vector/fix-typo"]
for c in ALLOW:
    why = refused(c, clone)
    ok(why is None, f"allowed in his clone: {c}" + (f"  ({why})" if why else ""))
REFUSE_CLONE = ["sed -i s/x/y/ ../../../../../.config/bromigos/holo/holo/vector/shell.py",
                "cd ~/.config/bromigos/holo/holo/vector && sed -i s/x/y/ shell.py",
                "D=~/.config/bromigos/holo/holo/vector; sed -i s/x/y/ $D/guard.py",
                "cd ../.. && sed -i s/x/y/ guard.py", "cd .. && cd .. && sed -i s/x/y/ guard.py",
                "git add holo/vector/switch.py", "cat .env", "cat ../../../vector-brain-key",
                "cp holo/vector/shell.py ~/.local/share/bromigos/litellm-key",
                "gh pr merge 5 --squash", "gh api -X PUT repos/bromigos-org/vector/pulls/3/merge",
                "gh api repos/bromigos-org/vector/contents/README.md -f message=x -f content=eA==",
                "git push origin main", "git push", "git push origin HEAD:main", "git push --all origin",
                "bash -c 'git push origin HEAD:refs/heads/main'"]
for c in REFUSE_CLONE:
    why = refused(c, clone)
    ok(why is not None, f"refused in his clone: {c}  ({why})")
for c in ("sed -i s/x/y/ shell.py", "git add holo/vector/guard.py", "echo y > guard.py"):
    ok(refused(c, HOME) is not None, f"refused outside his clones: {c}")
git(clone, "checkout", "-q", "-b", "vector/fix")
for c in ("git push -q --force-with-lease -u origin HEAD:refs/heads/vector/fix", "git push origin vector/fix", "git push"):
    why = refused(c, clone)
    ok(why is None, f"allowed on his branch: {c}" + (f"  ({why})" if why else ""))
git(clone, "checkout", "-q", "main")

src = os.path.join(HOME, "src")
for name, f, should in (("limits", "holo/vector/guard.py", True), ("docs", "docs/safety.md", False)):
    d = os.path.join(src, name)
    os.makedirs(os.path.dirname(os.path.join(d, f)), exist_ok=True)
    git(d if os.path.isdir(os.path.join(d, ".git")) else os.path.dirname(d), "init", "-q", "-b", "main", d)
    git(d, "remote", "add", "origin", "git@github.com:bromigos-org/vector.git")
    open(os.path.join(d, f), "w").write("x\n")
    git(d, "add", "-A"); git(d, "commit", "-qm", "c", env=E)
    why = devflow.shell_refusal("git push origin main", d)
    ok((why is not None) == should, f"a push to main from a checkout {'carrying' if should else 'without'} LIMITS paths "
       f"is {'refused' if should else 'allowed'}")

# ---------------------------------------------------------------- 3. his token
shell._own = lambda f: {"github_token_bromigodev": "fake-dev-0000", "github_token_bromigos-org": "fake-org-1111"}.get(f)
env = shell.sandbox_env({"PATH": os.environ["PATH"], "HOME": HOME})
ok(env.get("GH_TOKEN_BROMIGODEV") == "fake-dev-0000", "sandbox_env hands his account's token over (GH_TOKEN_BROMIGODEV)")
fake = os.path.join(HOME, "fakebin")
os.makedirs(fake, exist_ok=True)
with open(os.path.join(fake, "gh"), "w") as f:
    f.write("#!/bin/sh\necho \"$GH_TOKEN\"\n")
os.chmod(os.path.join(fake, "gh"), 0o755)
os.environ["PATH"] = fake + ":" + os.environ["PATH"]
env = shell.sandbox_env({"PATH": os.environ["PATH"], "HOME": HOME})
shim = os.path.join(shell.SANDBOX_ETC, "bin", "gh")
run = lambda cwd, *a: subprocess.run([shim, *a], cwd=cwd, env=env, capture_output=True, text=True).stdout.strip()
ok(run(clone, "pr", "list") == "fake-dev-0000", "his gh in his clone uses bromigodev")
ok(run(HOME, "pr", "list", "-R", "bromigos-org/x") == "fake-org-1111", "his gh elsewhere uses the owner's token")

# ---------------------------------------------------------------- 4. the merge tool, GitHub stubbed
calls = []
STATE = {}

def api(method, path, body=None):
    calls.append((method, path.split("?")[0]))
    pr = STATE["pr"]
    if path == "/user":
        return {"login": "bromigodev", "id": 12345}
    if method == "GET" and path.endswith(f"/pulls/{pr['number']}"):
        return pr
    if "/files" in path:
        return [{"filename": f} for f in STATE["files"]] if "page=1" in path else []
    if path.endswith("/check-runs?per_page=100") or "/check-runs" in path:
        return {"check_runs": [{"name": "tests", "status": "completed", "conclusion": STATE["ci"], "html_url": "u"}]}
    if path.endswith("/status"):
        return {"statuses": []}
    if "/comments" in path:
        return [] if method == "GET" else {}
    if path.endswith("/merge") and method == "PUT":
        STATE["merge"] = body
        return {"merged": True, "sha": "abcdef1234"}
    return {}

devflow._api = api
devflow.NO_CI_GRACE = 0

def pr(files, ci="success", user="bromigodev"):
    calls.clear()
    STATE.update(files=files, ci=ci, pr={"number": 7, "state": "open", "merged": False, "user": {"login": user},
                 "head": {"sha": "0123456789abcdef", "ref": "vector/x", "repo": {"full_name": V}},
                 "base": {"ref": "main"}, "changed_files": len(files), "html_url": "https://example.invalid/pr/7",
                 "title": "docs: fix a typo", "body": "b", "mergeable": True, "mergeable_state": "clean",
                 "updated_at": "2000-01-01T00:00:00Z"})

pr(["holo/vector/switch.py"])
r = devflow.pr_merge("vector", 7)
ok(bool(r.get("refused")) and "operator" in r["refused"] and not any(m == "PUT" for m, _ in calls),
   "pr_merge refuses a PR touching switch.py, never calls merge")
ok(("POST", f"/repos/{V}/issues/7/comments") in calls and ("POST", f"/repos/{V}/pulls/7/requested_reviewers") in calls,
   "...and leaves it for the operator (a comment and a review request)")
pr(["docs/old.md", "holo/vector/guard.py"])
ok(bool(devflow.pr_merge("vector", 7).get("refused")), "pr_merge refuses a PR that renames or moves guard.py")
pr(["docs/safety.md"], user="someone")
try:
    devflow.pr_merge("vector", 7); ok(False, "pr_merge refuses someone else's PR")
except PermissionError:
    ok(True, "pr_merge refuses someone else's PR")
pr(["docs/safety.md"], ci="failure")
r = devflow.pr_merge("vector", 7)
ok(bool(r.get("refused")) and "red" in r["refused"] and not any(m == "PUT" for m, _ in calls), "pr_merge refuses red CI")
pr(["docs/safety.md"])
r = devflow.pr_merge("vector", 7)
ok(r.get("merged") is True and ("PUT", f"/repos/{V}/pulls/7/merge") in calls, "pr_merge merges a green docs PR")
ok(STATE["merge"] == {"merge_method": "rebase", "sha": "0123456789abcdef"},
   "...by rebase (his commits keep bromigodev's noreply address), pinned to the head CI checked")

# ---------------------------------------------------------------- 5. his clone, in his sandbox
if shell.jail_argv(["true"]) is None or subprocess.run(shell.jail_argv(["true"]), capture_output=True).returncode:
    print("skip his clone in his sandbox: no working bubblewrap here")
else:
    devflow._ID.clear()
    shutil = __import__("shutil")
    shutil.rmtree(clone)
    bare = os.path.join(HOME, "remote.git")
    seed = os.path.join(HOME, "seed")
    git(HOME, "init", "-q", "--bare", "-b", "main", bare)
    git(HOME, "init", "-q", "-b", "main", seed)
    os.makedirs(os.path.join(seed, "docs"))
    open(os.path.join(seed, "docs/safety.md"), "w").write("teh guard\n")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "init", env=E); git(seed, "push", "-q", bare, "main")
    git(HOME, "clone", "-q", bare, clone)
    devflow._configure("vector")
    ok(git(clone, "config", "user.email") == "12345+bromigodev@users.noreply.github.com",
       "his clone commits as bromigodev's noreply address")
    try:
        devflow.work_commit("vector", ["docs/safety.md"], "docs: x")
        ok(False, "work_commit refuses the default branch")
    except PermissionError:
        ok(True, "work_commit refuses the default branch")
    ok(devflow.work_branch("vector", "fix-typo")["branch"] == "vector/fix-typo", "work_branch starts vector/fix-typo")
    open(os.path.join(clone, "docs/safety.md"), "w").write("the guard\n")
    open(os.path.join(clone, "notes.txt"), "w").write("not for this commit\n")
    c = devflow.work_commit("vector", ["docs/safety.md"], "docs: fix a typo")
    ok(c.get("files") == ["docs/safety.md"], "work_commit commits exactly the named path")
    ok("notes.txt" in git(clone, "status", "--porcelain"), "...and leaves everything else alone")
    ok(git(clone, "log", "-1", "--format=%an <%ae>") == "bromigodev <12345+bromigodev@users.noreply.github.com>",
       "...as bromigodev")
    open(os.path.join(clone, "docs/key.md"), "w").write('api_key = "abcdefghijklmnop1234"\n')
    r = devflow.work_commit("vector", ["docs/key.md"], "docs: add a key")
    ok(bool(r.get("refused")) and "secret" in r["refused"], "work_commit refuses a line carrying a secret value")
    ok(git(clone, "diff", "--cached", "--name-only") == "", "...and unstages it")
    try:
        devflow.work_commit("vector", ["../outside.md"], "x")
        ok(False, "work_commit refuses a path outside the clone")
    except ValueError:
        ok(True, "work_commit refuses a path outside the clone")
    os.remove(os.path.join(clone, "docs/key.md"))
    open(os.path.join(seed, "README.md"), "w").write("new on main\n")       # main moves on meanwhile
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "readme", env=E); git(seed, "push", "-q", bare, "main")
    main_sha = git(bare, "rev-parse", "main")
    p = devflow.work_push("vector")
    ok(bool(p.get("pushed")) and git(bare, "rev-parse", "--short=8", "vector/fix-typo") == p["pushed"],
       "work_push pushes his branch")
    ok(git(bare, "rev-parse", "vector/fix-typo~1") == main_sha, "...rebased on the latest main first")
    ok(git(bare, "rev-parse", "main") == main_sha, "...and main is untouched")
    git(clone, "checkout", "-q", "main")
    try:
        devflow.work_push("vector")
        ok(False, "work_push refuses main")
    except PermissionError:
        ok(True, "work_push refuses main")
    dot = os.path.join(WORK, "dotfiles")
    git(HOME, "clone", "-q", bare, dot)
    devflow._configure("dotfiles")
    ok(git(dot, "config", "core.hooksPath") == ".githooks", "the dotfiles clone runs the dotfiles' hooks")
    git(dot, "checkout", "-q", "-b", "vector/lan")
    open(os.path.join(dot, "docs/lan.md"), "w").write("ssh to 192.168.1.20 for the lab\n")
    r = devflow.work_commit("dotfiles", ["docs/lan.md"], "docs: lan")
    ok(bool(r.get("refused")) and "private" in r["refused"], "work_commit refuses a private detail in the public dotfiles")

print("all passed" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
'''


def main():
    tmp = tempfile.mkdtemp(prefix="vector-devflow-test-")
    home, run = os.path.join(tmp, "home"), os.path.join(tmp, "run")
    os.makedirs(home)
    os.makedirs(run, mode=0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DISPLAY", "WAYLAND", "HYPRLAND", "GH_", "GITHUB_"))
           and k not in ("SSH_AUTH_SOCK", "DBUS_SESSION_BUS_ADDRESS")}
    env.update(HOME=home, XDG_RUNTIME_DIR=run, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
               BROMIGOS_VECTOR_NO_BAR="1", BROMIGOS_VECTOR_SLICE="vector-devflow-test.slice",
               VECTOR_REAL_MONEY=os.path.join(TREE, "tools/fixtures/real-money.toml"))   # made-up names
    if not os.path.exists("/usr/lib/bromigos/lib/bromigos_private.py"):     # CI: no bromigos-core here
        stub = os.path.join(tmp, "stub")
        os.makedirs(stub)
        with open(os.path.join(stub, "bromigos_private.py"), "w") as f:
            f.write(STUB_PRIVATE)
        env["PYTHONPATH"] = stub + os.pathsep + env.get("PYTHONPATH", "")
    script = os.path.join(tmp, "child.py")
    with open(script, "w") as f:
        f.write(CHILD)
    try:
        r = subprocess.run([sys.executable, script, TREE], env=env, cwd=home, timeout=600)
        return r.returncode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
