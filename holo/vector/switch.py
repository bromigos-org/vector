#!/usr/bin/env python3
"""VECTOR's emergency switch: the operator's manual kill switch (docs/safety.md, "The
switch"). Nothing flips it on its own; the guard only keeps an audit log.

OFF, from any of its places (SUPER+CTRL+ESCAPE, the bar, Settings › VECTOR, `bromigos vector
off`), does this at once:
  1. writes the markers, so nothing new starts and the state survives a restart;
  2. kills every process of his sandbox: the cgroup of the systemd user slice his commands
     run in (cgroup.kill reaches every process in it, however it forked or setsid'd), and the
     process groups and mount namespaces recorded here when each command started (for a
     machine without a systemd user manager);
  3. checks the markers again and rewrites any that a dying process removed;
  4. tells his daemon, if it answers: the daemon cancels the turn and the tool calls in
     flight, and stops his speech and the conversation mode. If the daemon doesn't answer
     (hung), his voice players are stopped here, found by their parent (the daemon's pid,
     from its socket's peer credentials) and their exact name.
While it's off his terminal runs nothing, and his tools refuse everything that changes
something (tools.call); he can still talk and look things up.

ON is the operator's alone: `bromigos vector on`, or the key, the bar or Settings after a
confirmation. His terminal refuses every way of flipping it on (shell.check), his tools
can't reach it, and his sandbox can't write the markers' folders (shell.readonly_paths).

This file needs only Python's standard library and no running daemon. The launcher runs it
as `python3 -I switch.py`, so a broken venv or a hung daemon can't stop it.

The markers (any one present means off; a symlink in their place counts too):
  ~/.local/state/bromigos/vector-switch/off     survives restarts and reboots
  $XDG_RUNTIME_DIR/bromigos-vector-switch/off   this session; /run/user is root's, so this
                                                folder can't be renamed away from inside
                                                the sandbox either
  ~/.local/state/bromigos/vector-shell-off      the old terminal switch, still read as off
"""
import json
import os
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

HOME = os.path.expanduser("~")
UID = os.getuid()
STATE = os.path.join(HOME, ".local/state/bromigos")
RUN = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{UID}"
DIR = os.path.join(STATE, "vector-switch")
RUN_DIR = os.path.join(RUN, "bromigos-vector-switch")
MARK = os.path.join(DIR, "off")
RUN_MARK = os.path.join(RUN_DIR, "off")
LEGACY = os.path.join(STATE, "vector-shell-off")
PROCS = os.path.join(RUN_DIR, "procs")            # one file per sandboxed command that runs now
LOG = os.path.join(STATE, "vector-shell.log")     # the terminal's log: every flip is recorded there
SOCK = os.path.join(RUN, "bromigos-holo.sock")
# The systemd user slice his commands run in. Tests name their own, so they never touch his.
SLICE = os.environ.get("BROMIGOS_VECTOR_SLICE") or "vector-sandbox.slice"
PLAYERS = {"pw-play", "pw-cat", "paplay", "aplay"}  # his speech, played by the daemon
WAYBAR_SIGNAL = 9
KEY = "SUPER+CTRL+ESCAPE"


# ------------------------------------------------------------------ state
def is_off():
    """True when any marker is there (a file, a symlink, anything) or a markers' folder was
    swapped for a symlink: when in doubt, he's off."""
    if any(os.path.lexists(p) for p in (MARK, RUN_MARK, LEGACY)):
        return True
    return any(os.path.islink(d) for d in (DIR, RUN_DIR))


def who_switched_off():
    """The `by` of the newest marker (who flipped it), or None."""
    for p in (RUN_MARK, MARK):
        try:
            with open(p) as f:
                return (json.load(f) or {}).get("by")
        except (OSError, ValueError, AttributeError):
            continue
    return "the old terminal switch" if os.path.lexists(LEGACY) else None


def _real_dir(d):
    """`d` as a real folder of ours: a symlink or a file in its place is moved aside first."""
    if os.path.islink(d) or (os.path.lexists(d) and not os.path.isdir(d)):
        os.replace(d, f"{d}.moved-{int(time.time())}")
    os.makedirs(d, mode=0o700, exist_ok=True)


def ensure_dirs():
    """Both markers' folders exist (the sandbox binds them read-only, so they must be there
    before his first command), and the process folder."""
    for d in (DIR, RUN_DIR, PROCS):
        try:
            _real_dir(d)
        except OSError:
            pass
    return [d for d in (DIR, RUN_DIR) if os.path.isdir(d) and not os.path.islink(d)]


def _write_marker(path, by):
    _real_dir(os.path.dirname(path))
    tmp = f"{path}.{os.getpid()}.part"
    with open(tmp, "w") as f:
        json.dump({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "by": by}, f)
    os.replace(tmp, path)                          # replaces a symlink itself, never its target


def migrate():
    """The old terminal switch (vector-shell-off) becomes the new markers, once."""
    if os.path.lexists(LEGACY) and not os.path.lexists(MARK):
        try:
            _write_marker(MARK, "the old terminal switch")
            _write_marker(RUN_MARK, "the old terminal switch")
            os.unlink(LEGACY)
        except OSError:
            pass


def _log(**rec):
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": "switch", **rec}) + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------ his sandbox's processes
_SYSTEMD = {"t": 0.0, "ok": False}
_LOCK = threading.Lock()


def _user_cgroup():
    """The user manager's cgroup (…/user@UID.service), where his slice lives."""
    want = f"user@{UID}.service"
    try:
        with open("/proc/self/cgroup") as f:
            for line in f:
                path = line.strip().split("::", 1)[-1]
                if want in path:
                    return "/sys/fs/cgroup" + path[:path.index(want) + len(want)]
    except OSError:
        pass
    return f"/sys/fs/cgroup/user.slice/user-{UID}.slice/{want}"


def slice_cgroup(name=None):
    """The cgroup folder of a user slice: a-b.slice lives in a.slice/a-b.slice."""
    name = name or SLICE
    parts = name[:-len(".slice")].split("-")
    path = [("-".join(parts[:i + 1]) + ".slice") for i in range(len(parts))]
    return os.path.join(_user_cgroup(), *path)


def systemd_ok(env=None):
    """Whether `systemd-run --user --scope` works here (probed, then cached)."""
    now = time.monotonic()
    with _LOCK:
        if _SYSTEMD["t"] and now - _SYSTEMD["t"] < (600 if _SYSTEMD["ok"] else 60):
            return _SYSTEMD["ok"]
    ok = False
    if shutil.which("systemd-run"):
        try:
            ok = subprocess.run(["systemd-run", "--user", "--scope", "--quiet", "--collect", f"--slice={SLICE}",
                                 "--", "/bin/true"], env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=8).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
    with _LOCK:
        _SYSTEMD.update(t=time.monotonic(), ok=ok)
    return ok


def scope(argv, kind="cmd", env=None):
    """(argv, unit): argv run in a transient scope of his slice, so the switch reaches every
    process it starts; (argv, None) where there's no systemd user manager."""
    if not systemd_ok(env):
        return list(argv), None
    unit = f"vector-{kind}-{int(time.time())}-{secrets.token_hex(3)}.scope"
    return ["systemd-run", "--user", "--scope", "--quiet", "--collect", f"--slice={SLICE}", f"--unit={unit}",
            "--"] + list(argv), unit


def _stat(pid):
    """(ppid, pgid, start time, name) of a process, or None."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            s = f.read()
    except OSError:
        return None
    name = s[s.index("(") + 1:s.rindex(")")]
    rest = s[s.rindex(")") + 2:].split()
    return int(rest[1]), int(rest[2]), int(rest[19]), name


def _mntns(pid):
    try:
        return os.readlink(f"/proc/{pid}/ns/mnt")
    except OSError:
        return None


def register(pid, unit=None):
    """Records a sandboxed command as it starts: its process group (start_new_session), its
    start time (so a reused pid is never killed) and, a moment later, the sandbox's own mount
    namespace, which every process inside it keeps, setsid or not."""
    st = _stat(pid)
    rec = {"pid": pid, "pgid": pid, "start": st[2] if st else None, "unit": unit, "slice": SLICE if unit else None,
           "mntns": None}
    path = os.path.join(PROCS, f"{pid}.json")

    def save():
        try:
            os.makedirs(PROCS, exist_ok=True)
            with open(path + ".part", "w") as f:
                json.dump(rec, f)
            os.replace(path + ".part", path)
        except OSError:
            pass
    save()

    def find_ns():
        host = _mntns("self")
        t0 = time.monotonic()
        while time.monotonic() - t0 < 3 and os.path.exists(path):
            try:
                with open(f"/proc/{pid}/task/{pid}/children") as f:
                    kids = f.read().split()
            except OSError:
                return
            for k in kids:
                ns = _mntns(k)
                if ns and ns != host:
                    rec["mntns"] = ns
                    if os.path.exists(path):
                        save()
                    return
            time.sleep(0.05)
    threading.Thread(target=find_ns, daemon=True, name="switch-ns").start()


def unregister(pid):
    try:
        os.unlink(os.path.join(PROCS, f"{pid}.json"))
    except OSError:
        pass


def _records():
    out = []
    try:
        names = os.listdir(PROCS)
    except OSError:
        return out
    for n in names:
        if n.endswith(".json"):
            try:
                with open(os.path.join(PROCS, n)) as f:
                    out.append(json.load(f))
            except (OSError, ValueError):
                pass
    return out


def _cgroup_pids(cg):
    pids = []
    for d, _dirs, files in os.walk(cg):
        if "cgroup.procs" in files:
            try:
                with open(os.path.join(d, "cgroup.procs")) as f:
                    pids += [int(x) for x in f.read().split()]
            except (OSError, ValueError):
                pass
    return pids


def kill_cgroup(cg):
    """SIGKILL every process in a cgroup and the cgroups under it; -> how many there were."""
    if not cg or not os.path.isdir(cg):
        return 0
    seen = set()
    for _ in range(5):
        pids = _cgroup_pids(cg)
        if not pids:
            break
        seen.update(pids)
        try:
            with open(os.path.join(cg, "cgroup.kill"), "w") as f:      # Linux 5.14+: all of them, atomically
                f.write("1")
        except OSError:
            for p in pids:
                try:
                    os.kill(p, signal.SIGKILL)
                except OSError:
                    pass
        time.sleep(0.05)
    return len(seen)


def kill_unit(unit):
    """Kill one command's scope (STOP on a single command)."""
    if unit:
        return kill_cgroup(os.path.join(slice_cgroup(), unit))
    return 0


def stop_all():
    """Kill every process of his sandbox; -> how many were found."""
    killed = 0
    recs = _records()
    for s in {SLICE} | {r.get("slice") for r in recs if r.get("slice")}:
        killed += kill_cgroup(slice_cgroup(s))
    host = _mntns("self")
    spaces = set()
    for r in recs:
        st = _stat(r.get("pid") or -1)
        if st and r.get("start") is not None and st[2] == r["start"]:   # the same process, not a reused pid
            try:
                os.killpg(r["pgid"], signal.SIGKILL)
                killed += 1
            except OSError:
                pass
        if r.get("mntns") and r["mntns"] != host:
            spaces.add(r["mntns"])
    if spaces:                                       # a sandbox's own mount namespace: every process in it is his
        for p in os.listdir("/proc"):
            if p.isdigit() and int(p) != os.getpid() and _mntns(p) in spaces:
                try:
                    os.kill(int(p), signal.SIGKILL)
                    killed += 1
                except OSError:
                    pass
    for r in recs:
        st = _stat(r.get("pid") or -1)
        if not st or st[2] != r.get("start"):
            unregister(r.get("pid"))
    return killed


# ------------------------------------------------------------------ his daemon
def _daemon(verb, timeout=2.5):
    """Tell the daemon; -> (reply or None, the daemon's pid or None). None reply with a pid: hung."""
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    pid = None
    try:
        s.connect(SOCK)
        try:
            pid = struct.unpack("3i", s.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))[0]
        except OSError:
            pid = None
        s.sendall(verb.encode())
        return s.recv(4096).decode().strip() or None, pid
    except (OSError, socket.timeout):
        return None, pid
    finally:
        s.close()


def _stop_players(daemon_pid):
    """A hung daemon: stop the players speaking for it (its own children, by exact name)."""
    n = 0
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        st = _stat(p)
        if st and st[0] == daemon_pid and st[3] in PLAYERS:
            try:
                os.kill(int(p), signal.SIGKILL)
                n += 1
            except OSError:
                pass
    return n


def _bar():
    if os.environ.get("BROMIGOS_VECTOR_NO_BAR"):    # tests: never signal the desktop's bar
        return
    try:
        subprocess.Popen(["pkill", f"-RTMIN+{WAYBAR_SIGNAL}", "-x", "waybar"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError:
        pass


# ------------------------------------------------------------------ the switch
def set_off(by="the operator", tell=True):
    """OFF, now. -> what it did."""
    t0 = time.monotonic()
    errors = []
    for m in (RUN_MARK, MARK):
        try:
            _write_marker(m, by)
        except OSError as e:
            errors.append(f"{m}: {e.strerror or e}")
    killed = stop_all()
    for m in (RUN_MARK, MARK):                        # nothing of his is alive now: make sure both are there
        if not os.path.isfile(m) or os.path.islink(m):
            try:
                _write_marker(m, by)
            except OSError as e:
                errors.append(f"{m}: {e.strerror or e}")
    daemon = "not asked"
    if tell:
        reply, pid = _daemon(f"switch off {by}")
        if reply is not None:
            daemon = "told"
        elif pid:
            daemon = f"didn't answer; stopped {_stop_players(pid)} of his voice players"
        else:
            daemon = "not running"
    out = {"state": "off" if is_off() else "ON (the markers couldn't be written)", "killed": killed,
           "daemon": daemon, "ms": int((time.monotonic() - t0) * 1000)}
    if errors:
        out["errors"] = errors
    _log(state="off", by=by, killed=killed, daemon=daemon, errors=errors or None)
    _bar()
    return out


def set_on(by="the operator"):
    """ON: the operator's alone. -> what it did."""
    errors = []
    for d in (DIR, RUN_DIR):
        if os.path.islink(d):
            try:
                _real_dir(d)
            except OSError as e:
                errors.append(f"{d}: {e.strerror or e}")
    for m in (RUN_MARK, MARK, LEGACY):
        try:
            if os.path.isdir(m) and not os.path.islink(m):
                shutil.rmtree(m)                      # a folder in a marker's place: still a marker
            elif os.path.lexists(m):
                os.unlink(m)
        except OSError as e:
            errors.append(f"{m}: {e.strerror or e}")
    reply, _pid = _daemon(f"switch on {by}")
    out = {"state": "off (couldn't remove a marker)" if is_off() else "on",
           "daemon": "told" if reply is not None else "not running"}
    if errors:
        out["errors"] = errors
    _log(state="on" if not is_off() else "off", by=by, errors=errors or None)
    _bar()
    return out


def confirm(question):
    """The operator's yes, from a rofi menu with NO first (Enter or Esc never says yes)."""
    if not shutil.which("rofi") or not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return False
    try:
        r = subprocess.run(["rofi", "-dmenu", "-i", "-no-custom", "-p", "", "-mesg", question],
                           input="NO\nYES\n", capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.stdout.strip() == "YES"


def _notify(text):
    if os.environ.get("BROMIGOS_VECTOR_NO_BAR"):
        return
    try:
        subprocess.Popen(["notify-send", "-a", "VECTOR", "-u", "normal", "-t", "6000",
                          "-h", "string:x-dunst-stack-tag:vector-switch", "VECTOR", text],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def status_text():
    if is_off():
        by = who_switched_off()
        return "off" + (f" (switched off by {by})" if by else "")
    return "on"


USAGE = """bromigos vector off | on | switch [status|toggle]   VECTOR's emergency switch
  off       switch VECTOR off now: stop his commands, his turn and his speech
  on        switch him back on (only you; his terminal refuses it)
  switch    say whether he's on or off
  switch toggle   off at once; on only after a YES in a confirmation menu ({key}, the bar)"""


def main(argv):
    args = [a for a in argv if not a.startswith("--from=")]
    src = next((a.split("=", 1)[1] for a in argv if a.startswith("--from=")), "")
    by = f"the operator ({src[:30]})" if src else "the operator"
    verb = args[0] if args else "status"
    ensure_dirs()
    migrate()
    if verb == "off":
        r = set_off(by)
        print("VECTOR is off" + (f": {r['killed']} sandboxed process(es) stopped" if r["killed"] else "")
              + f"; daemon {r['daemon']} ({r['ms']} ms)" + ("; " + "; ".join(r["errors"]) if r.get("errors") else ""))
        return 0 if r["state"] == "off" else 1
    if verb == "on":
        r = set_on(by)
        print("VECTOR is on" if r["state"] == "on" else "VECTOR is still " + r["state"]
              + ("; " + "; ".join(r["errors"]) if r.get("errors") else ""))
        return 0 if r["state"] == "on" else 1
    if verb == "toggle":
        if not is_off():
            r = set_off(by)
            _notify(f"Switched off. {KEY} again (or the bar) turns him back on, after a YES.")
            print(f"VECTOR is off ({r['killed']} sandboxed process(es) stopped)")
            return 0
        if confirm(f"TURN VECTOR BACK ON? He can act again within his limits ({status_text()})"):
            r = set_on(by)
            _notify("VECTOR is on again.")
            print("VECTOR is " + r["state"])
            return 0 if r["state"] == "on" else 1
        print("VECTOR stays off (no YES). In a terminal: bromigos vector on")
        return 1
    if verb in ("status", "show"):
        print(status_text())
        return 0
    print(USAGE.format(key=KEY))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
