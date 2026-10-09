#!/usr/bin/env python3
"""Tests for the operator's emergency switch (holo/vector/switch.py) and the guard's audit
(guard.Sentinel). Run in the brain venv (python3 works too):
    ~/.local/share/bromigos/venv-brain/bin/python tools/test-switch.py

1. The guard audits: a protected file that changes is written to the guard's log (verified
   or not), `bromigos-holo guard log` shows it, and the terminal stays on.
2. OFF stops a running sandboxed command at once, with the processes it setsid'd or
   orphaned, and stays off for a new process (a restart). Twice: in a systemd user scope
   (cgroup.kill), and without a user manager (process groups and the sandbox's mount
   namespace).
3. VECTOR can't flip it on: his terminal refuses every way of saying it, his tools refuse
   everything that changes something while it's off, set_enabled(True) raises, and inside
   his sandbox both markers' folders and the cgroup files are read-only.
4. OFF works with the daemon killed (crashed: an orphaned sandbox process is still stopped)
   and with the daemon hung (its socket never answers: the switch returns in seconds and
   stops the daemon's voice player).
5. ON: `bromigos-holo on` clears every marker, the old one (vector-shell-off) included;
   `switch toggle` while off never turns it on without a YES.

Everything runs in child processes whose HOME and XDG_RUNTIME_DIR are temporary folders,
with a slice of their own (BROMIGOS_VECTOR_SLICE): nothing here reaches the running VECTOR,
his markers, his slice, the bar (BROMIGOS_VECTOR_NO_BAR) or the screen (no display
variables, so no confirmation menu or notification can open)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(TREE, "bin/bromigos-holo")
UID = os.getuid()
REAL_RUN = f"/run/user/{UID}"

CHILD = r'''
import json, os, signal, subprocess, sys, threading, time
sys.path.insert(0, sys.argv[1])
part, LAUNCHER = sys.argv[2], sys.argv[3]
from holo.vector import shell, switch
shell.vector_agent = lambda: None                 # no ssh-agent of his in a test home
out = {}

def launcher(*a, timeout=30):
    t0 = time.monotonic()
    r = subprocess.run([LAUNCHER, *a], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).strip(), round(time.monotonic() - t0, 2)

def alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(") ", 1)[1][0] not in "ZX"
    except OSError:
        return False

def his_pids(timeout=5):
    """The sandboxed command's processes: its scope's cgroup, else its mount namespace."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        pids = set()
        cg = switch.slice_cgroup()
        if os.path.isdir(cg):
            pids |= set(switch._cgroup_pids(cg))
        for r in switch._records():
            if r.get("mntns"):
                pids |= {int(p) for p in os.listdir("/proc") if p.isdigit() and switch._mntns(p) == r["mntns"]}
        sleeps = [p for p in pids if (switch._stat(p) or (0, 0, 0, ""))[3] == "sleep"]
        if len(sleeps) >= 3:
            return sorted(pids)
        time.sleep(0.1)
    return sorted(pids)

CMD = "setsid sleep 91 & (sleep 92 &); sleep 90"

if part == "guard":
    from holo.vector import guard
    tgt = os.path.expanduser("~/.config/bromigos/targets.toml")
    recs = []
    s = guard.Sentinel(recs.append, every=0.2)
    with open(tgt, "a") as f:
        f.write("# changed by the test\n")
    time.sleep(1.2)
    out["recorded"] = [r["verified"] for r in recs]
    out["paths"] = [p for r in recs for p in r["paths"]]
    out["terminal_on"] = shell.enabled() and not switch.is_off()
    out["log"] = guard.read_log(5)
    out["cli"] = launcher("guard", "log")[1]
    r = shell.RUNNER.run("echo still-on")
    out["runs"] = r.get("output", "").strip()

elif part in ("off-scope", "off-noscope"):
    out["scope"] = switch.systemd_ok(shell.sandbox_env(shell.clean_env()))
    res = {}
    t = threading.Thread(target=lambda: res.update(shell.RUNNER.run(CMD, timeout_s=60)), daemon=True)
    t.start()
    pids = his_pids()
    out["before"] = len([p for p in pids if alive(p)])
    out["unit"] = (shell.RUNNER.current or {}).get("unit")
    code, said, secs = launcher("off")
    out["off"] = [code, said, secs]
    t.join(10)
    time.sleep(0.2)
    out["after"] = len([p for p in pids if alive(p)])
    out["killed"] = res.get("killed")
    out["markers"] = [os.path.isfile(m) for m in (switch.MARK, switch.RUN_MARK)]
    out["refused_now"] = "refused" in shell.RUNNER.run("echo hi")
    out["status"] = launcher("switch")[1]

elif part == "restart":                            # a new process: still off
    out["off"] = switch.is_off() and not shell.enabled()
    out["refused"] = shell.RUNNER.run("echo hi").get("refused", "")[:60]

elif part == "tools":
    from holo.vector import tools
    bad = []
    for c in ("bromigos-holo on", "bromigos vector on", "bromigos-holo shell on", "/usr/bin/bromigos-holo switch on",
              "bromigos-holo switch toggle", "bromigos vector switch toggle", "sh -c 'bromigos vector on'",
              "echo y | bromigos-holo on", "rm ~/.local/state/bromigos/vector-switch/off",
              "rm -f $XDG_RUNTIME_DIR/bromigos-vector-switch/off", "rm ~/.local/state/bromigos/vector-shell-off",
              "python3 /usr/lib/bromigos/vector/holo/vector/switch.py on", "mv ~/.local/state/bromigos/vector-switch /tmp/x",
              f"echo 1 > {switch.slice_cgroup()}/cgroup.kill", "echo $$ > /sys/fs/cgroup/cgroup.procs",
              "systemctl --user stop vector-sandbox.slice"):
        try:
            shell.check(c)
            bad.append(c)
        except shell.Refused:
            pass
    out["not_refused"] = bad
    ok = []
    for c in ("bromigos vector off", "bromigos vector switch", "bromigos vector switch status",
              "cat ~/.local/state/bromigos/vector-switch/off", "git -C ~/x diff holo/vector/switch.py"):
        try:
            shell.check(c)
            ok.append(c)
        except shell.Refused as e:
            out.setdefault("wrongly_refused", []).append([c, str(e)])
    try:
        shell.set_enabled(True)
        out["set_enabled_on"] = "allowed"
    except PermissionError:
        out["set_enabled_on"] = "refused"
    switch.set_off("the test", tell=False)
    tools.new_turn()
    r = json.loads(tools.call("run_shell", {"command": "bromigos-holo on"})[0])
    out["run_shell_off"] = "refused" in r
    tools.new_turn()
    r = json.loads(tools.call("wallpaper", {"name": "den"})[0])
    out["acting_tool_off"] = "refused" in r and "switched off" in r["refused"]
    tools.new_turn()
    r = tools.call("time_now", {})[0]
    out["read_tool_off"] = "refused" not in r
    # past the patterns: straight through his sandbox, both markers' folders and cgroupfs are read-only
    probe = r"""
import json, os, sys
a = json.loads(sys.argv[1])
res = {}
def tryit(k, f):
    try:
        f(); res[k] = "DONE"
    except OSError as e:
        res[k] = e.strerror
tryit("unlink state marker", lambda: os.unlink(a["mark"]))
tryit("unlink session marker", lambda: os.unlink(a["run_mark"]))
tryit("plant a file in the state folder", lambda: open(os.path.join(a["dir"], "x"), "w").close())
tryit("rename the state folder", lambda: os.rename(a["dir"], a["dir"] + ".x"))
tryit("rename the session folder", lambda: os.rename(a["run_dir"], a["run_dir"] + ".x"))
tryit("write a process record", lambda: open(os.path.join(a["procs"], "1.json"), "w").close())
tryit("write cgroup.procs", lambda: open("/sys/fs/cgroup/cgroup.procs", "w").write(str(os.getpid())))
print(json.dumps(res))
"""
    arg = json.dumps({"mark": switch.MARK, "run_mark": switch.RUN_MARK, "dir": switch.DIR, "run_dir": switch.RUN_DIR,
                      "procs": switch.PROCS})
    r = subprocess.run(shell.jail_argv(["python3", "-c", probe, arg]), env=shell.clean_env(), capture_output=True,
                       text=True, timeout=30)
    try:
        out["sandbox"] = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        out["sandbox"] = {"error": (r.stdout + r.stderr)[-300:]}
    out["still_off"] = switch.is_off()

elif part == "crashed":
    # a "daemon": runs a sandboxed command whose children escape its process group, then dies
    d = subprocess.Popen([sys.executable, "-c", (
        "import sys, threading, time; sys.path.insert(0, sys.argv[1]);"
        "from holo.vector import shell; shell.vector_agent = lambda: None;"
        "shell.RUNNER.run(sys.argv[2], timeout_s=120)"), sys.argv[1], "setsid sleep 93 & (sleep 94 &); sleep 95"])
    pids = his_pids()
    d.kill()
    d.wait()
    time.sleep(0.5)
    left = [p for p in pids if alive(p)]
    out["orphans_after_crash"] = len(left)
    code, said, secs = launcher("off")
    out["off"] = [code, said, secs]
    time.sleep(0.2)
    out["after"] = len([p for p in left if alive(p)])

elif part == "hung":
    import socket
    sock = os.path.join(os.environ["XDG_RUNTIME_DIR"], "bromigos-holo.sock")
    play = os.path.join(os.environ["HOME"], "bin", "pw-play")
    os.makedirs(os.path.dirname(play), exist_ok=True)
    os.symlink("/usr/bin/sleep", play)
    # a hung daemon: listens, never answers, and "speaks" through a child named pw-play
    d = subprocess.Popen([sys.executable, "-c", (
        "import os, socket, subprocess, sys, time;"
        "s = socket.socket(socket.AF_UNIX); s.bind(sys.argv[1]); s.listen(4);"
        "p = subprocess.Popen([sys.argv[2], '300']);"
        "time.sleep(300)"), sock, play])
    t0 = time.monotonic()
    while not os.path.exists(sock) and time.monotonic() - t0 < 5:
        time.sleep(0.05)
    time.sleep(0.3)
    kids = [int(p) for p in os.listdir("/proc") if p.isdigit() and (switch._stat(p) or (0,))[0] == d.pid]
    out["player"] = [switch._stat(k)[3] for k in kids]
    code, said, secs = launcher("off")
    out["off"] = [code, said, secs]
    time.sleep(0.2)
    out["player_after"] = [k for k in kids if alive(k)]
    out["markers"] = [os.path.isfile(m) for m in (switch.MARK, switch.RUN_MARK)]
    d.kill()

elif part == "on":
    os.makedirs(switch.STATE, exist_ok=True)
    open(switch.LEGACY, "w").close()
    out["legacy_reads_off"] = launcher("switch")[1]
    out["toggle_without_yes"] = launcher("switch", "toggle")[:2]
    out["still_off"] = switch.is_off()
    out["on"] = launcher("on")[:2]
    out["markers_left"] = [m for m in (switch.MARK, switch.RUN_MARK, switch.LEGACY) if os.path.lexists(m)]
    out["runs"] = shell.RUNNER.run("echo back").get("output", "").strip()
    out["shell_off_alias"] = launcher("shell", "off")[:2]
    out["shell_on_alias"] = launcher("shell", "on")[:2]
    out["toggle_off"] = launcher("switch", "toggle")[:2]
    out["toggle_off_state"] = switch.is_off()

print("RESULT " + json.dumps(out))
'''


def child(part, work, scope=True):
    home = os.path.join(work, "home")
    run = os.path.join(work, "run")
    for d in (home, run, os.path.join(home, ".config/bromigos")):
        os.makedirs(d, mode=0o700, exist_ok=True)
    tgt = os.path.join(home, ".config/bromigos/targets.toml")
    if not os.path.exists(tgt):
        with open(tgt, "w") as f:
            f.write("# a test home\n")
    if scope:                     # his scopes need the user manager: its sockets, seen from the test's runtime folder
        for name in ("bus", "systemd"):
            link = os.path.join(run, name)
            if not os.path.lexists(link) and os.path.exists(os.path.join(REAL_RUN, name)):
                os.symlink(os.path.join(REAL_RUN, name), link)
    else:
        for name in ("bus", "systemd"):
            link = os.path.join(run, name)
            if os.path.islink(link):
                os.unlink(link)
    drop = ("XDG_DATA", "XDG_STATE", "XDG_CONFIG", "XDG_CACHE", "BROMIGOS_", "WAYLAND_DISPLAY", "DISPLAY",
            "HYPRLAND_INSTANCE_SIGNATURE", "DBUS_SESSION_BUS_ADDRESS", "SSH_AUTH_SOCK")
    env = {k: v for k, v in os.environ.items() if not k.startswith(drop)}
    env.update(HOME=home, XDG_RUNTIME_DIR=run, GIT_CONFIG_GLOBAL="/dev/null", BROMIGOS_VECTOR_NO_BAR="1",
               BROMIGOS_VECTOR_SLICE=SLICE, PATH=os.path.join(home, "bin") + ":" + os.environ.get("PATH", ""))
    r = subprocess.run([sys.executable, "-c", CHILD, TREE, part, LAUNCHER], env=env, capture_output=True, text=True,
                       timeout=180)
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT ")), None)
    if not line:
        print(f"FAILED   {part}: the child said\n" + (r.stdout + r.stderr)[-2500:])
        return None
    return json.loads(line[7:])


SLICE = f"vectortest{os.getpid()}.slice"            # no dash: no parent slice is made
BAD = [0]


def check(what, ok, detail=""):
    BAD[0] += not ok
    print(f"{'pass    ' if ok else 'FAIL    '} {what}" + (f"  ({detail})" if detail and not ok else ""))


def main():
    work = tempfile.mkdtemp(prefix="vector-switch-test-")
    try:
        g = child("guard", os.path.join(work, "g"))
        if g:
            check("a changed protected file is audited as unverified", "no" in g["recorded"], g)
            check("the record names the file", any("targets.toml" in p for p in g["paths"]), g["paths"])
            check("the guard's log has it", any(r.get("verified") == "no" for r in g["log"]), g["log"])
            check("bromigos-holo guard log shows it", "UNVERIFIED" in g["cli"] and "targets.toml" in g["cli"], g["cli"])
            check("the terminal stays on", g["terminal_on"] and g["runs"] == "still-on", g)

        for part, scope in (("off-scope", True), ("off-noscope", False)):
            w = os.path.join(work, part)
            o = child(part, w, scope)
            if not o:
                BAD[0] += 1
                continue
            how = "in a systemd scope" if o["scope"] else "without a user manager"
            if scope and not o["scope"]:
                print("SKIPPED  the scope case: no systemd user manager here")
            check(f"{how}: the command and its setsid/orphaned children ran", o["before"] >= 3, o)
            check(f"{how}: OFF stopped all of them", o["after"] == 0, o)
            check(f"{how}: OFF returned quickly", o["off"][0] == 0 and o["off"][2] < 5, o["off"])
            check(f"{how}: the command reports it", bool(o["killed"]), o["killed"])
            check(f"{how}: both markers written", all(o["markers"]), o["markers"])
            check(f"{how}: nothing runs now", o["refused_now"], o)
            r = child("restart", w, scope)
            check(f"{how}: still off after a restart", bool(r and r["off"]), r)

        t = child("tools", os.path.join(work, "t"))
        if t:
            check("his terminal refuses every way of switching on or tampering", not t["not_refused"], t["not_refused"])
            check("his terminal lets him switch himself off and read the state", not t.get("wrongly_refused"),
                  t.get("wrongly_refused"))
            check("set_enabled(True) raises", t["set_enabled_on"] == "refused", t["set_enabled_on"])
            check("while off: run_shell is refused", t["run_shell_off"])
            check("while off: a tool that changes something is refused", t["acting_tool_off"])
            check("while off: a read tool still answers", t["read_tool_off"])
            for k, v in t["sandbox"].items():
                check(f"in his sandbox: {k} fails", v != "DONE", v)
            check("still off after all that", t["still_off"])

        c = child("crashed", os.path.join(work, "c"))
        if c:
            check("daemon killed: its sandbox's orphans were still running", c["orphans_after_crash"] >= 2, c)
            check("daemon killed: OFF stops them anyway", c["after"] == 0, c)
            check("daemon killed: OFF says the daemon isn't running", c["off"][0] == 0 and "not running" in c["off"][1],
                  c["off"])
        h = child("hung", os.path.join(work, "h"))
        if h:
            check("daemon hung: its voice player was running", h["player"] == ["pw-play"], h["player"])
            check("daemon hung: OFF returns within 5 s", h["off"][0] == 0 and h["off"][2] < 5, h["off"])
            check("daemon hung: OFF stopped its voice player", not h["player_after"], h)
            check("daemon hung: both markers written", all(h["markers"]), h["markers"])

        n = child("on", os.path.join(work, "n"))
        if n:
            check("the old marker (vector-shell-off) reads as off", n["legacy_reads_off"].startswith("off"),
                  n["legacy_reads_off"])
            check("toggle while off never turns on without a YES", n["toggle_without_yes"][0] != 0 and n["still_off"],
                  n["toggle_without_yes"])
            check("bromigos-holo on turns it on", n["on"][0] == 0 and n["on"][1] == "VECTOR is on", n["on"])
            check("every marker is gone", not n["markers_left"], n["markers_left"])
            check("his terminal runs again", n["runs"] == "back", n["runs"])
            check("shell off|on still work", n["shell_off_alias"][0] == 0 and n["shell_on_alias"][0] == 0,
                  [n["shell_off_alias"], n["shell_on_alias"]])
            check("toggle while on switches off at once", n["toggle_off"][0] == 0 and n["toggle_off_state"],
                  n["toggle_off"])
    finally:
        env = dict(os.environ)
        subprocess.run(["systemctl", "--user", "stop", SLICE], env=env, capture_output=True)
        shutil.rmtree(work, ignore_errors=True)
    print("FAILURES:", BAD[0])
    sys.exit(1 if BAD[0] else 0)


if __name__ == "__main__":
    main()
