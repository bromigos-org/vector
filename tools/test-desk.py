#!/usr/bin/env python3
"""Tests for VECTOR launching programs, writing and tracking software, and new repos.
    ~/.local/share/bromigos/venv-brain/bin/python tools/test-desk.py
Real, harmless actions: launches System Monitor by fuzzy name and closes it; creates a
clearly named private throwaway repo under blackflame007, writes a tiny script there,
commits and pushes it to a branch through VECTOR's own terminal, runs the done-check
before and after, then deletes the repo (the test harness, not VECTOR) and confirms
it's gone."""
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from holo.vector import desk, shell, tools, track  # noqa: E402

bad = 0


def ok(cond, what, detail=""):
    global bad
    print(f"{'pass' if cond else 'FAIL'}  {what}" + (f" — {detail}" if detail else ""))
    bad += 0 if cond else 1


def launch_and_close():
    print("== launch by fuzzy name, then close")
    before = {w["address"] for w in desk.windows()}
    r, _ = tools.call("launch_app", {"name": "system monitor"})
    ok('"ok":true' in r, "launch_app 'system monitor'", r[:90])
    new = []
    for _ in range(40):
        time.sleep(0.5)
        new = [w for w in desk.windows() if w["address"] not in before]
        if new:
            break
    ok(bool(new), "its window appeared", new[0]["class"] if new else "no new window")
    if new:
        r, _ = tools.call("window", {"action": "close", "target": new[0]["address"]})
        time.sleep(1.5)
        ok(new[0]["address"] not in {w["address"] for w in desk.windows()}, "window closed", r[:80])
    r, _ = tools.call("launch_app", {"name": "zzz-no-such-program"})
    ok("no installed program" in r, "unknown program is reported", r[:80])
    r, _ = tools.call("run_detached", {"command": "sudo true"})
    ok("privilege escalation" in r, "run_detached goes through the terminal's limits", r[:80])


def repo_round_trip():
    print("== a throwaway private repo, a script on a branch, the done-check")
    name = f"vector-test-scratch-{time.strftime('%Y%m%d-%H%M%S')}"
    full = f"blackflame007/{name}"
    path = os.path.expanduser(f"~/github.com/blackflame007/{name}")
    try:
        r, _ = tools.call("github_repo_create", {"owner": "blackflame007", "name": name,
                                                 "description": "VECTOR test scratch repo; deleted by the test"})
        d = json.loads(r)
        ok(d.get("ok") and d.get("visibility") == "private", "github_repo_create (private by default)", r[:120])
        vis = subprocess.run(["gh", "repo", "view", full, "--json", "visibility,name"], capture_output=True, text=True)
        ok('"PRIVATE"' in vis.stdout, "GitHub says it's private", vis.stdout.strip()[:80])
        ok(all(os.path.exists(os.path.join(path, f)) for f in ("README.md", "AGENTS.md", ".gitignore")), "seeded")
        r, _ = tools.call("github_repo_create", {"owner": "someone-else", "name": "x", "description": "y"})
        ok("owner:" in r, "unknown owner refused", r[:80])
        # VECTOR's own terminal writes a script on a branch
        cmd = (f"cd {path} && git switch -q -c vector/hello && mkdir -p bin && "
               "printf '#!/bin/sh\\necho hello from VECTOR\\n' > bin/hello && chmod +x bin/hello && ./bin/hello")
        rr = shell.RUNNER.run(cmd, timeout_s=30)
        ok(rr.get("exit") == 0 and "hello from VECTOR" in rr.get("output", ""), "script written and run", str(rr)[:90])
        chk = track.changes_check()
        mine = [p for p in chk["problems"] if p.get("repo", "").endswith(name)]
        ok(not chk["clear"] and mine and "bin/hello" in mine[0].get("uncommitted", []),
           "done-check lists the uncommitted script", json.dumps(mine)[:120])
        rr = shell.RUNNER.run(f"cd {path} && git add bin/hello && git commit -qm 'Added: bin/hello' && "
                              "git push -q -u origin vector/hello 2>&1 | tail -1", timeout_s=60)
        ok(rr.get("exit") == 0, "commit and push to a branch", (rr.get("refused") or rr.get("output", ""))[:90])
        remote = subprocess.run(["gh", "api", f"repos/{full}/branches/vector/hello", "--jq", ".name"],
                                capture_output=True, text=True)
        ok(remote.stdout.strip() == "vector/hello", "the branch is on GitHub")
        chk = track.changes_check()
        mine = [p for p in chk["problems"] if p.get("repo", "").endswith(name)]
        ok(not mine, "done-check is clear for the repo after commit and push", json.dumps(mine)[:120])
        # the host's own uncommitted work is never flagged
        dot = track.touch(os.path.expanduser("~/.dotfiles"))
        chk = track.changes_check()
        dmine = [p for p in chk["problems"] if p.get("repo") == "~/.dotfiles"]
        ok(dot and not dmine, "the host's own uncommitted dotfiles are not flagged", json.dumps(dmine)[:120])
    finally:
        subprocess.run(["gh", "repo", "delete", full, "--yes"], capture_output=True, text=True)
        gone = subprocess.run(["gh", "repo", "view", full], capture_output=True, text=True)
        ok(gone.returncode != 0, "test repo deleted and gone", gone.stderr.strip()[:60])
        shutil.rmtree(path, ignore_errors=True)
        track._repos.pop(path, None)


def main():
    launch_and_close()
    repo_round_trip()
    print("FAILURES:", bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
