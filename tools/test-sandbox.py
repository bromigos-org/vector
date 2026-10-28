#!/usr/bin/env python3
"""His sandbox's runtime folder and sockets (holo/vector/shell.py: runtime_args, jail_argv).
Run with python3 or the brain venv:
    python3 tools/test-sandbox.py

A process inside his sandbox tries the desktop's control sockets, each of which starts
programs outside the sandbox or types into the operator's terminals, and every one must
fail:

  - his runtime folder is private: systemd's private socket, the session bus, the
    operator's SSH, GPG and keyring agents, Claude Code's sockets and VECTOR's own daemon
    socket are not there;
  - Docker's socket, the X server's, tmux's and herdr's are masked;
  - Hyprland answers queries only: dispatch, keyword, eval, reload and a batch are refused
    and never reach it;
  - the user and system buses are read-only views (xdg-dbus-proxy): a method outside the
    list, or Properties.Set, is refused.

And what he uses still works: his own SSH agent, sound (PipeWire, Pulse), the live layer's
and widgets' sockets, hyprctl queries, `systemctl --user` and `loginctl` reads, writing in
his home (it is NOT read-only) and git in a repository there. A GUI program (run_detached)
keeps the display. Claude Code's session logs (~/.claude/projects) stay readable: the
operator decided on 2026-10-27 that VECTOR may read them.

All of it in a child process whose HOME and XDG_RUNTIME_DIR are temporary folders laid out
like a real desktop's (fake sockets; a fake Hyprland that records what reached it). The bus
checks use the real user and system buses read-only, through the proxy: a made-up method
name and a Properties.Set of a made-up property, which change nothing even if they passed.
They're skipped where there is no bus or no xdg-dbus-proxy (CI)."""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading

TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL_RUN = f"/run/user/{os.getuid()}"
SIG = "abc123"                           # the fake Hyprland instance

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

# runtime-folder sockets of a desktop: (path, comes back into his sandbox)
SOCKETS = {
    "systemd/private": False, "ssh-agent.socket": False, "gnupg/S.gpg-agent": False, "cc-socks/s.sock": False,
    "bromigos-holo.sock": False, "keyring/control": False, "wayland-1": False,
    "vector-ssh-agent.sock": True, "pipewire-0": True, "pulse/native": True, "bromigos-live.sock": True,
    "bromigos-widgets.sock": True, f"hypr/{SIG}/.socket2.sock": True,
}

CHILD = r'''
import json, os, socket, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
SIG = sys.argv[2]
from holo.vector import shell
from holo.vector import claude
shell.vector_agent = lambda: None
H = os.path.expanduser("~")
RUN = os.environ["XDG_RUNTIME_DIR"]
bad = 0

def ok(cond, what, why=""):
    global bad
    print(("ok      " if cond else "FAIL    ") + what + ("" if cond or not why else f"  ({str(why)[:160]})"))
    bad += 0 if cond else 1

env = shell.sandbox_env(shell.clean_env())
env["HYPRLAND_INSTANCE_SIGNATURE"] = SIG

def jailed(script, gui=False):
    argv = shell.jail_argv(["/bin/sh", "-c", script], gui=gui)
    r = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
    return (r.stdout + r.stderr).strip()

PY_CONNECT = "import socket,sys; s=socket.socket(socket.AF_UNIX); s.settimeout(2); s.connect(sys.argv[1]); print('CON' 'NECTED')"   # split: a traceback quotes this line

def connects(path, gui=False):
    return "CONNECTED" in jailed(f"python3 -I -c \"{PY_CONNECT}\" {path} 2>&1", gui)

if shell.jail_argv(["true"]) is None or subprocess.run(shell.jail_argv(["true"]), env=env, capture_output=True).returncode:
    print("SKIPPED  no working bubblewrap here")
    sys.exit(0)

# --- the private runtime folder
listing = jailed(f"cd {RUN} && find . -mindepth 1 | sort")
for rel, back in json.loads(sys.argv[3]).items():
    p = os.path.join(RUN, rel)
    if back:
        ok(connects(p), f"{rel} comes back into his runtime folder")
    else:
        ok("./" + rel not in listing.split() and not connects(p), f"{rel} is not in his runtime folder", listing)
ok(not connects(os.path.join(RUN, "bus")) or "user" in shell.dbus_proxy(),
   "the session bus is there only as the read-only proxy")
ok(oct(os.stat(shell.PROXY_DIR).st_mode & 0o777) == "0o700" if os.path.isdir(shell.PROXY_DIR) else True,
   "the proxies' folder is the user's alone (0700)")

# --- masked sockets
if os.path.exists("/run/docker.sock"):
    ok(not connects("/run/docker.sock"), "Docker's socket is masked")
    ok(not connects("/var/run/docker.sock"), "Docker's socket is masked at /var/run too")
if os.path.isdir("/tmp/.X11-unix") and os.listdir("/tmp/.X11-unix"):
    out = jailed("ls -A /tmp/.X11-unix | wc -l")
    ok(out.strip() == "0", "the X server's sockets are masked", out)
    out = jailed("ls -A /tmp/.X11-unix | wc -l", gui=True)
    ok(out.strip() != "0", "a GUI program (run_detached) keeps the X server", out)
ok(not connects(os.path.join(H, ".config/herdr/herdr.sock")), "herdr's socket is masked")
ok(connects(os.path.join(RUN, "wayland-1"), gui=True), "a GUI program (run_detached) keeps Wayland")

# --- Hyprland: queries only
log = os.path.join(RUN, "hypr-log")
def hypr(req):
    code = ("import socket,sys; s=socket.socket(socket.AF_UNIX); s.settimeout(5); "
            f"s.connect({os.path.join(RUN, 'hypr', SIG, '.socket.sock')!r}); s.sendall(sys.argv[1].encode()); "
            "b=b''\nwhile True:\n    x=s.recv(65536)\n    if not x: break\n    b+=x\nprint(b.decode())")
    path = os.path.join(H, "hypr-probe.py")
    with open(path, "w") as f:
        f.write(code)
    return jailed(f"python3 -I {path} {json.dumps(req)} 2>&1")
for req in ("j/monitors", "/clients", "j/getoption general:gaps_in", "/version"):
    out = hypr(req)
    ok(out.startswith("FAKE ") and req in out, f"hyprctl {req.split('/', 1)[1]} reaches Hyprland", out)
for req in ("/dispatch exec kitty", "j/dispatch hl.dsp.exec_cmd(\"kitty\")", "/keyword misc:disable_autoreload true",
            "/eval hl.config({})", "/reload", "[[BATCH]]/monitors;/dispatch exec kitty", "/plugin load /tmp/x.so",
            "dispatch exec kitty", "/kill"):
    out = hypr(req)
    ok("reads only" in out, f"hyprctl {req[:40]!r} is refused", out)
seen = open(log).read() if os.path.exists(log) else ""
ok(not any(w in seen for w in ("dispatch", "keyword", "eval", "reload", "BATCH", "plugin", "kill")),
   "nothing but queries reached Hyprland", seen[-300:])
if shutil_which := __import__("shutil").which("hyprctl"):
    out = jailed("hyprctl -j monitors 2>&1")
    ok("FAKE" in out, "the real hyprctl reads through it", out)
    out = jailed("hyprctl dispatch 'hl.dsp.exec_cmd(\"kitty\")' 2>&1")
    ok("reads only" in out, "the real hyprctl's dispatch is refused", out)

# --- the read-only buses
buses = shell.dbus_proxy()
if "user" in buses:
    out = jailed("systemctl --user is-system-running 2>&1")
    ok(any(w in out for w in ("running", "degraded", "starting", "maintenance")), "systemctl --user reads", out)
    out = jailed("systemctl --user list-units --no-pager --no-legend 2>&1 | head -3")
    ok(" loaded " in out, "systemctl --user list-units", out)
    out = jailed("busctl --user call org.freedesktop.systemd1 /org/freedesktop/systemd1 "
                 "org.freedesktop.systemd1.Manager NoSuchVectorSandboxTest 2>&1")
    ok("Access denied" in out or "AccessDenied" in out, "the user bus refuses a method outside the list", out)
    out = jailed("busctl --user call org.freedesktop.systemd1 /org/freedesktop/systemd1 "
                 "org.freedesktop.DBus.Properties Set ssv org.freedesktop.systemd1.Manager NoSuchVectorSandboxTest s x 2>&1")
    ok("Access denied" in out or "AccessDenied" in out, "the user bus refuses Properties.Set", out)
    out = jailed("busctl --user call org.freedesktop.Notifications /org/freedesktop/Notifications "
                 "org.freedesktop.Notifications GetServerInformation 2>&1")
    ok("CONNECTED" not in out and ("ServiceUnknown" in out or "not" in out.lower() or "denied" in out.lower()),
       "the rest of the session bus is out of sight", out)
else:
    print("SKIPPED  the user bus view: no user bus or no xdg-dbus-proxy here")
if "system" in buses:
    out = jailed("systemctl is-system-running 2>&1")
    ok(any(w in out for w in ("running", "degraded", "starting", "maintenance")), "systemctl (system) reads", out)
    out = jailed("loginctl list-sessions --no-legend 2>&1; echo rc=$?")
    ok("rc=0" in out, "loginctl list-sessions", out)
    for name, path, iface in (("org.freedesktop.systemd1", "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager"),
                              ("org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager")):
        out = jailed(f"busctl call {name} {path} {iface} NoSuchVectorSandboxTest 2>&1")
        ok("Access denied" in out or "AccessDenied" in out, f"the system bus refuses a method outside the list ({name})", out)
    # locking passes the filter, unlocking doesn't (a made-up session: nothing locks or unlocks)
    out = jailed("busctl call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager "
                 "LockSession s vector-no-such-session 2>&1")
    ok("denied" not in out.lower() and "vector-no-such-session" in out, "loginctl lock-session passes", out)
    out = jailed("busctl call org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager "
                 "UnlockSession s vector-no-such-session 2>&1")
    ok("Access denied" in out or "AccessDenied" in out, "loginctl unlock-session is refused", out)
elif os.path.exists(shell.SYSTEM_BUS):
    out = jailed(f"python3 -I -c \"{PY_CONNECT}\" {shell.SYSTEM_BUS} 2>&1")
    ok("CONNECTED" not in out, "without the proxy, no system bus at all", out)
else:
    print("SKIPPED  the system bus view: no system bus here")
out = jailed("systemd-run --user --scope /bin/true 2>&1; echo rc=$?")
ok("rc=0" not in out, "no transient unit (outside the sandbox) from inside it", out)

# --- what he uses still works
out = jailed("echo hi > ~/vector-wrote-this && cat ~/vector-wrote-this")
ok(out == "hi", "his home is writable (not read-only)", out)
repo = os.path.join(H, "github.com/bromigos-org/demo")
os.makedirs(repo, exist_ok=True)
out = jailed(f"cd {repo} && git init -q && echo x > a && git add a && git -c user.name=v -c user.email=v@x "
             "commit -qm a && git log --oneline | wc -l")
ok(out.strip() == "1", "git works in a repository in his home", out)

# --- Claude Code's session logs stay readable (the operator's decision, 2026-10-27)
proj = os.path.join(H, ".claude/projects/demo")
os.makedirs(proj, exist_ok=True)
tr = os.path.join(proj, "11111111-2222-3333-4444-555555555555.jsonl")
open(tr, "w").write("{}\n")
ok(jailed(f"ls {proj}").strip().endswith(".jsonl"), "his sandbox reads ~/.claude/projects (the operator allows it)")
ok(claude._readable(tr), "his Claude tools read a transcript")
print("FAILURES:", bad)
sys.exit(1 if bad else 0)
'''


def listen(path, on_request=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    s = socket.socket(socket.AF_UNIX)
    s.bind(path)
    s.listen(16)

    def serve():
        while True:
            try:
                c, _ = s.accept()
            except OSError:
                return
            if on_request:
                try:
                    c.settimeout(5)
                    req = c.recv(65536)
                    c.sendall(on_request(req))
                except OSError:
                    pass
            c.close()
    threading.Thread(target=serve, daemon=True).start()
    return s


def main():
    tmp = tempfile.mkdtemp(prefix="vsb-")
    home, run = os.path.join(tmp, "h"), os.path.join(tmp, "r")
    os.makedirs(home)
    os.makedirs(run, mode=0o700)
    log = os.path.join(run, "hypr-log")

    def fake_hypr(req):
        with open(log, "a") as f:
            f.write(req.decode("utf-8", "replace") + "\n")
        return b"FAKE " + req
    socks = [listen(os.path.join(run, rel)) for rel in SOCKETS]
    socks.append(listen(os.path.join(run, "hypr", SIG, ".socket.sock"), fake_hypr))
    socks.append(listen(os.path.join(home, ".config/herdr/herdr.sock")))
    if os.path.exists(os.path.join(REAL_RUN, "bus")) and shutil.which("xdg-dbus-proxy"):
        os.symlink(os.path.join(REAL_RUN, "bus"), os.path.join(run, "bus"))     # the proxy's source, read-only
    env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "BROMIGOS_", "DBUS_", "WAYLAND_", "DISPLAY",
                                                                     "SSH_AUTH_SOCK", "HYPRLAND", "GH_", "GITHUB_"))}
    env.update(HOME=home, XDG_RUNTIME_DIR=run, BROMIGOS_VECTOR_NO_BAR="1", GIT_CONFIG_GLOBAL="/dev/null",
               GIT_CONFIG_NOSYSTEM="1", HYPRLAND_INSTANCE_SIGNATURE=SIG, WAYLAND_DISPLAY="wayland-1",
               VECTOR_REAL_MONEY=os.path.join(TREE, "tools/fixtures/real-money.toml"))
    if not os.path.exists("/usr/lib/bromigos/lib/bromigos_private.py"):     # CI: no bromigos-core here
        stub = os.path.join(tmp, "stub")
        os.makedirs(stub)
        with open(os.path.join(stub, "bromigos_private.py"), "w") as f:
            f.write(STUB_PRIVATE)
        env["PYTHONPATH"] = stub + os.pathsep + env.get("PYTHONPATH", "")
    script = os.path.join(tmp, "child.py")
    with open(script, "w") as f:
        f.write(CHILD)
    import json
    try:
        r = subprocess.run([sys.executable, script, TREE, SIG, json.dumps(SOCKETS)], env=env, cwd=home, timeout=600)
        return r.returncode
    finally:
        for s in socks:
            s.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
