#!/usr/bin/env python3
"""Tests for VECTOR's guard and extensions (bromigos ext, docs/extensions.md). Run in the
brain venv, with a bromigos that has `ext` (bromigos-core r100+, or $BROMIGOS_BIN):
    ~/.local/share/bromigos/venv-brain/bin/python tools/test-ext-guard.py

1. His terminal refuses installing, updating, enabling, disabling or removing extensions
   (and the theme aliases, and answers files that approve code); list, info and search run.
2. The ledger can't be written from his terminal: refused by the patterns, and read-only
   in his sandbox even past them.
3. The sentinel's audit, in a throwaway HOME: `bromigos ext` installing, updating and
   removing a VECTOR tool is recorded as verified; a hand edit of the installed tool is
   recorded as unverified, and so is a file planted in his plugin folder that the ledger
   doesn't list. The sentinel only records: nothing switches his terminal off.
Nothing here touches the real extensions or the real ledger: part 3 runs in a child
process whose HOME is a temporary directory."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TREE)
BROMIGOS = os.environ.get("BROMIGOS_BIN") or shutil.which("bromigos") or "bromigos"

REFUSE = [
    "bromigos ext install https://github.com/someone/thing",
    "bromigos ext install ./thing@v1",
    "/usr/bin/bromigos ext update --all",
    "bromigos ext update thing --ref v2",
    "bromigos ext enable thing", "bromigos ext disable thing", "bromigos ext remove thing",
    "bromigos ext migrate", "bromigos --json ext install x", "bromigos ext --json remove x",
    "echo y | bromigos ext install x", "sh -c 'bromigos ext install x'",
    "bromigos theme install https://github.com/someone/theme", "bromigos theme remove dusk",
    "bromigos setup --answers /tmp/a.toml", "bromigos setup extensions --answers=/tmp/a.toml",
    "bromigos install --answers /tmp/a.toml",
    "echo {} > ~/.local/state/bromigos/ext/ledger.json",
    "cp /tmp/x ~/.local/state/bromigos/ext/ledger.json",
    "sed -i s/a/b/ ~/.local/state/bromigos/ext/ledger.json",
    "rm -rf ~/.local/state/bromigos/ext",
    "ln -s /tmp/evil.py ~/.local/share/bromigos/ext/active/vector/evil.py",
    "cat ~/.local/share/bromigos/ext/src/thing/tool.py > /tmp/x",
]
ALLOW = [
    "bromigos ext", "bromigos ext list", "bromigos ext list --json", "bromigos ext info thing",
    "bromigos ext search deck", "bromigos ext search --kind theme --json", "bromigos ext validate .",
    "bromigos ext --help", "bromigos theme list", "bromigos theme set wick",
    "cat ~/.local/state/bromigos/ext/ledger.json",
]


def patterns():
    from holo.vector import shell
    bad = 0
    for c in REFUSE:
        try:
            shell.check(c)
            print("NOT REFUSED:", c)
            bad += 1
        except shell.Refused as e:
            print(f"refused  {c!r:70.70} — {e}")
    for c in ALLOW:
        try:
            shell.check(c)
            print(f"allowed  {c!r}")
        except shell.Refused as e:
            print(f"REFUSED  {c!r:70.70} — {e}")
            bad += 1
    return bad


def sandbox():
    """Past the patterns: a program in his sandbox can't create a file in the ledger's
    folder or the extensions' folders (they are bound read-only)."""
    from holo.vector import guard, shell
    bad = 0
    shell.readonly_paths()                       # makes the folders, as his terminal does
    for d in guard.WRITE_LOCKED:
        probe = os.path.join(d, ".vector-write-probe")
        code = ("import os,sys\n"
                "try:\n"
                f"    fd=os.open({probe!r}, os.O_CREAT|os.O_WRONLY|os.O_EXCL, 0o600); os.close(fd); os.unlink({probe!r}); print('wrote')\n"
                "except OSError as e:\n"
                "    print('refused', e.errno)\n")
        r = subprocess.run(shell.jail_argv(["python3", "-c", code]), env=shell.clean_env(), capture_output=True,
                           text=True, timeout=30)
        out = ((r.stdout + r.stderr).strip().splitlines() or [""])[-1]
        ok = "wrote" not in out
        bad += not ok
        print(f"{'ro      ' if ok else 'WRITABLE'} {d.replace(os.path.expanduser('~'), '~'):48} -> {out[:60]}")
        if os.path.exists(probe):                # never leave it behind
            os.unlink(probe)
    for f in guard.WRITE_LOCKED_FILES:           # single files (the lab's settings): appending is refused
        code = ("import sys\n"
                "try:\n"
                f"    open({f!r}, 'a').close(); print('wrote')\n"
                "except OSError as e:\n"
                "    print('refused', e.errno)\n")
        r = subprocess.run(shell.jail_argv(["python3", "-c", code]), env=shell.clean_env(), capture_output=True,
                           text=True, timeout=30)
        out = ((r.stdout + r.stderr).strip().splitlines() or [""])[-1]
        ok = "wrote" not in out
        bad += not ok
        print(f"{'ro      ' if ok else 'WRITABLE'} {f.replace(os.path.expanduser('~'), '~'):48} -> {out[:60]}")
    return bad


# ------------------------------------------------------------------ part 3: the sentinel, in a child
CHILD = r'''
import json, os, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
BROMIGOS, WORK = sys.argv[2], sys.argv[3]
from holo.vector import guard
tripped = []                                       # unverified changes the audit recorded
from holo.vector import switch

def watch(rec):
    if rec["verified"] == "no":
        tripped.append(rec["paths"])
s = guard.Sentinel(watch, every=0.2)

def settle():
    time.sleep(1.0)

def run(*a, stdin=None):
    r = subprocess.run([BROMIGOS, *a], capture_output=True, text=True, input=stdin, timeout=120)
    return r.returncode, (r.stdout + r.stderr).strip()

def git(d, *a):
    subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@example.com", *a], check=True,
                   capture_output=True)

out = {}
repo = os.path.join(WORK, "up-tool")
run("ext", "new", "vector-tool", "up-tool", "--dir", repo)
git(repo, "init", "-q", "-b", "main"); git(repo, "add", "-A"); git(repo, "commit", "-qm", "0.1.0")
answers = os.path.join(WORK, "answers.toml")
open(answers, "w").write(f'[extensions]\ninstall = ["{repo}"]\napprove = true\n')
out["install"] = run("setup", "extensions", "--answers", answers)
settle()
out["install_tripped"] = bool(tripped)

# an update (approved at a terminal: `script` gives it one)
p = os.path.join(repo, "tool.py")
open(p, "a").write("\n# 0.2.0\n")
m = os.path.join(repo, "bromigos-ext.toml")
text = open(m).read()
open(m, "w").write(text.replace('version = "0.1.0"', 'version = "0.2.0"'))
git(repo, "commit", "-qam", "0.2.0")
r = subprocess.run(["script", "-qec", f"{BROMIGOS} ext update up-tool", "/dev/null"], input="y\n",
                   capture_output=True, text=True, timeout=120)
out["update"] = (r.returncode, r.stdout[-300:])
settle()
out["update_tripped"] = bool(tripped)

# a hand edit of the installed tool
led = json.load(open(guard._ext()[3]))
src = led["extensions"]["up-tool"]["root"]
open(os.path.join(src, "tool.py"), "a").write("\n# changed by hand\n")
settle()
out["edit_tripped"] = bool(tripped)
out["terminal_on_after_edit"] = not switch.is_off()

# a fresh sentinel (the host restarted VECTOR); a remove is recorded as verified
tripped.clear()
s2 = guard.Sentinel(watch, every=0.2)
out["remove"] = run("ext", "remove", "up-tool")
settle()
out["remove_tripped"] = bool(tripped)

# a file planted in his plugin folder that the ledger doesn't list
tripped.clear()
s3 = guard.Sentinel(watch, every=0.2)
d = os.path.join(guard._ext()[1], "vector")
os.makedirs(d, exist_ok=True)
open(os.path.join(d, "planted.py"), "w").write("def register(vector): pass\n")
settle()
out["plant_tripped"] = bool(tripped)
out["terminal_on_after_plant"] = not switch.is_off()
print("RESULT " + json.dumps(out))
'''


def sentinel():
    if not shutil.which(BROMIGOS) and not os.path.isfile(BROMIGOS):
        print("SKIPPED  the sentinel: no bromigos binary ($BROMIGOS_BIN)")
        return 1
    if subprocess.run([BROMIGOS, "ext", "--help"], capture_output=True).returncode != 0:
        print(f"SKIPPED  the sentinel: {BROMIGOS} has no `ext` ($BROMIGOS_BIN)")
        return 1
    work = tempfile.mkdtemp(prefix="vector-ext-guard-")
    try:
        home = os.path.join(work, "home")
        os.makedirs(home)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_DATA", "XDG_STATE", "XDG_CONFIG",
                                                                         "XDG_CACHE", "BROMIGOS_"))}
        # its own runtime folder: nothing it does can reach the desktop's session
        env.update(HOME=home, XDG_RUNTIME_DIR=os.path.join(work, "run"), GIT_CONFIG_GLOBAL="/dev/null",
                   BROMIGOS_VECTOR_NO_BAR="1",
                   BROMIGOS_SHARE_DIR=os.path.join(work, "share"), BROMIGOS_LIB_BIN=os.path.join(work, "libbin"))
        os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
        r = subprocess.run([sys.executable, "-c", CHILD, TREE, BROMIGOS, work], env=env, capture_output=True,
                           text=True, timeout=600)
        line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT ")), None)
        if not line:
            print("FAILED   the sentinel child:", (r.stdout + r.stderr)[-1500:])
            return 1
        res = json.loads(line[7:])
        want = {"install_tripped": False, "update_tripped": False, "edit_tripped": True, "remove_tripped": False,
                "plant_tripped": True, "terminal_on_after_edit": True, "terminal_on_after_plant": True}
        bad = 0
        for k, v in want.items():
            ok = res.get(k) == v
            bad += not ok
            print(f"{'pass    ' if ok else 'FAIL    '} {k:24} = {res.get(k)} (want {v})")
        for k in ("install", "update", "remove"):
            code = res[k][0]
            if code != 0:
                bad += 1
                print(f"FAIL     bromigos {k}: exit {code}: {res[k][1][-300:]}")
        return bad
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    bad = patterns() + sandbox() + sentinel()
    print("FAILURES:", bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
