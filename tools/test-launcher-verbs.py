#!/usr/bin/env python3
"""bin/bromigos-holo's own verbs, against a stand-in daemon.

  - `stop now` sends "stop now" (the daemon quits at once); a bare `stop` sends "stop", and
    with a turn running it waits until the daemon has gone before it says "stopped";
  - `stop` with no daemon says "not running"; `stop later` is refused with the usage;
  - `restart` and `restart now` stop the daemon the same way and start a new one only after
    the old one has gone (a start while it still answered would find it and start nothing);
  - a daemon the launcher starts doesn't keep the launcher's output open, so a caller that
    reads it gets its end when the launcher exits, not when the daemon does;
  - the usage header names only verbs the launcher handles, and names every one of them but
    the scripts' own (vector-hide, gallery-hide, gallery-do, snap); every verb the launcher
    passes to the daemon is one holo/app.py handles.

Nothing reaches a desktop: a throwaway HOME and runtime directory, no display. "The daemon"
the launcher starts is a stand-in too: a fake brain environment's python
($HOME/.local/share/bromigos/venv-brain/bin/python, which the launcher prefers) that runs
the launcher's socket client as python3 does but answers `-m holo.app` with a stand-in.
Run: python3 tools/test-launcher-verbs.py (the package's check() does).
"""
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOLO = os.path.join(ROOT, "bin", "bromigos-holo")
INTERNAL = {"vector-hide", "gallery-hide", "gallery-do", "snap"}     # for scripts and tests
fails = []

# the brain environment's python: python3 for the launcher's socket client; for
# `-m holo.app`, a stand-in daemon that records its start, answers status and quits on stop
STUB = r'''#!/usr/bin/python3
import os, socket, sys, time
if sys.argv[1:3] != ["-m", "holo.app"]:
    os.execv("/usr/bin/python3", ["/usr/bin/python3", *sys.argv[1:]])
run = os.environ["XDG_RUNTIME_DIR"]
with open(os.path.join(run, "started"), "a") as f:
    f.write(f"{time.time()}\n")
sock = os.path.join(run, "bromigos-holo.sock")
try:
    os.unlink(sock)
except FileNotFoundError:
    pass
s = socket.socket(socket.AF_UNIX)
s.bind(sock)
s.listen(8)
s.settimeout(0.2)
end = time.time() + 20
while time.time() < end:
    try:
        c, _ = s.accept()
    except socket.timeout:
        continue
    with c:
        cmd = c.recv(4096).decode()
        c.sendall(b"{}" if cmd == "status" else b"ok")
    if cmd.startswith("stop"):
        break
'''


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        fails.append(what)


class FakeHolo:
    """The running daemon: answers status, records every other command. Busy, it answers a
    bare stop as app.py does ("stopping after the current turn") and goes `linger` seconds
    later; "stop now", or a stop while idle, and it goes at once."""

    def __init__(self, path, busy=False, linger=1.5):
        self.got, self.busy, self.linger, self.gone_at = [], busy, linger, None
        self.s = socket.socket(socket.AF_UNIX)
        self.s.bind(path)
        self.s.listen(8)
        self.s.settimeout(0.05)
        self.quit_at = None
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while self.quit_at is None or time.time() < self.quit_at:
            try:
                c, _ = self.s.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with c:
                cmd = c.recv(4096).decode()
                if cmd == "status":
                    c.sendall(b"{}")
                    continue
                self.got.append(cmd)
                if cmd == "stop" and self.busy:
                    self.quit_at = time.time() + self.linger
                    c.sendall(b"stopping after the current turn")
                else:
                    if cmd.startswith("stop"):
                        self.quit_at = time.time()
                    c.sendall(b"ok")
        self.gone_at = time.time()
        self.s.close()               # a daemon that has exited: connections are refused


def holo(tmp, *args, timeout=60):
    env = {"HOME": tmp, "XDG_RUNTIME_DIR": tmp, "XDG_STATE_HOME": os.path.join(tmp, "state"),
           "PATH": "/usr/bin:/bin", "HYPRLAND_INSTANCE_SIGNATURE": "none", "LANG": "C.UTF-8"}
    t0 = time.time()
    r = subprocess.run([HOLO, *args], env=env, capture_output=True, text=True, timeout=timeout)
    return r, time.time() - t0


def sandbox():
    tmp = tempfile.TemporaryDirectory()
    venv = os.path.join(tmp.name, ".local", "share", "bromigos", "venv-brain", "bin")
    os.makedirs(venv)
    py = os.path.join(venv, "python")
    with open(py, "w") as f:
        f.write(STUB)
    os.chmod(py, 0o755)
    return tmp


def starts(tmp):
    try:
        return [float(x) for x in open(os.path.join(tmp, "started")).read().split()]
    except FileNotFoundError:
        return []


def stop_stub(tmp):
    """Send the stand-in daemon a stop, if one is answering, so it never outlives the test."""
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(2)
    try:
        s.connect(os.path.join(tmp, "bromigos-holo.sock"))
        s.sendall(b"stop now")
        s.recv(64)
    except OSError:
        pass
    finally:
        s.close()


def test_stop():
    with sandbox() as tmp:
        fake = FakeHolo(os.path.join(tmp, "bromigos-holo.sock"), busy=True)
        r, dt = holo(tmp, "stop", "now")
        check(fake.got == ["stop now"], f"`stop now` sends 'stop now' (sent {fake.got})")
        check(r.returncode == 0 and r.stdout.strip() == "stopped" and fake.gone_at is not None,
              f"`stop now`: the daemon has gone when it says stopped (rc {r.returncode}, {r.stdout.strip()!r})")
        check(dt < fake.linger, f"`stop now` doesn't wait for the turn ({dt:.1f} s)")
        check(not starts(tmp), "`stop now` starts nothing")

    with sandbox() as tmp:
        fake = FakeHolo(os.path.join(tmp, "bromigos-holo.sock"), busy=True)
        r, dt = holo(tmp, "stop")
        out = r.stdout.split("\n")
        check(fake.got == ["stop"], f"`stop` sends 'stop' (sent {fake.got})")
        check(r.returncode == 0 and out[:2] == ["stopping after the current turn", "stopped"],
              f"`stop` with a turn running says it waits, then stopped (rc {r.returncode}, {r.stdout!r})")
        check(dt >= fake.linger and fake.gone_at is not None,
              f"`stop` returns only once the daemon has gone ({dt:.1f} s, linger {fake.linger} s)")

    with sandbox() as tmp:
        fake = FakeHolo(os.path.join(tmp, "bromigos-holo.sock"))
        r, _ = holo(tmp, "stop")
        check(fake.got == ["stop"] and r.returncode == 0 and r.stdout.strip() == "stopped",
              f"`stop` while idle: stopped at once (sent {fake.got}, {r.stdout.strip()!r})")

    with sandbox() as tmp:
        r, _ = holo(tmp, "stop")
        check(r.returncode == 0 and r.stdout.strip() == "not running" and not starts(tmp),
              f"`stop` with no daemon: not running, nothing started ({r.stdout.strip()!r})")

    with sandbox() as tmp:
        fake = FakeHolo(os.path.join(tmp, "bromigos-holo.sock"))
        for verb in ("stop", "restart"):
            r, _ = holo(tmp, verb, "later")
            check(r.returncode == 2 and f"bromigos-holo {verb} [now]" in r.stderr,
                  f"`{verb} later` is refused with the usage (rc {r.returncode}, {r.stderr.strip()!r})")
        check(fake.got == [] and fake.gone_at is None, f"a refused stop sends nothing (sent {fake.got})")


def test_restart():
    with sandbox() as tmp:
        fake = FakeHolo(os.path.join(tmp, "bromigos-holo.sock"), busy=True)
        r, dt = holo(tmp, "restart", "now")
        st = starts(tmp)
        check(fake.got == ["stop now"], f"`restart now` sends 'stop now' (sent {fake.got})")
        check(r.returncode == 0 and r.stdout.strip() == "started" and len(st) == 1,
              f"`restart now` starts one new daemon (rc {r.returncode}, {r.stdout.strip()!r}, {len(st)} started)")
        check(dt < fake.linger + 6, f"`restart now` doesn't wait for the turn ({dt:.1f} s)")
        stop_stub(tmp)

    with sandbox() as tmp:
        fake = FakeHolo(os.path.join(tmp, "bromigos-holo.sock"), busy=True)
        r, _ = holo(tmp, "restart")
        st = starts(tmp)
        check(fake.got == ["stop"], f"`restart` sends 'stop' (sent {fake.got})")
        check(r.returncode == 0 and r.stdout.rstrip().endswith("started") and len(st) == 1,
              f"`restart` starts one new daemon (rc {r.returncode}, {r.stdout!r}, {len(st)} started)")
        check(bool(st) and fake.gone_at is not None and st[0] >= fake.gone_at,
              "`restart` with a turn running starts the new daemon only after the old one has gone")
        stop_stub(tmp)


def test_start():
    with sandbox() as tmp:
        r, dt = holo(tmp, "vector")
        st = starts(tmp)
        check(r.returncode == 0 and len(st) == 1, f"`vector` with no daemon starts one (rc {r.returncode}, {len(st)} started)")
        check(dt < 8, f"the started daemon doesn't hold the launcher's output open ({dt:.1f} s; it lives 20 s)")
        stop_stub(tmp)


def test_usage():
    src = open(HOLO).read()
    header = []
    for line in src.splitlines()[1:]:
        if not line.startswith("#"):
            break
        header.append(line)
    named = set()
    for line in header:
        m = re.match(r"#\s+bromigos-holo (.*)", line)
        if m:
            words = re.split(r"\s{2,}", m.group(1))[0]          # the usage, before its note
            named |= set(re.findall(r"(?:^|\| )([a-z][a-z-]*)", words))
    body = src.split('case "${1:-status}" in', 1)[1]
    handled = set()
    for pat in re.findall(r"^    ([a-z|-]+)\)", body, re.M):
        handled |= set(pat.split("|"))
    passed = set(re.search(r"^    ([a-z|-]+)\)\n\s+start && send \"\$@\"", body, re.M).group(1).split("|"))
    daemon = set(re.findall(r'verb == "([a-z-]+)"', open(os.path.join(ROOT, "holo", "app.py")).read()))
    check(named <= handled, f"the usage names only verbs the launcher handles (not: {sorted(named - handled)})")
    check(handled - INTERNAL <= named, f"the usage names every verb (missing: {sorted(handled - INTERNAL - named)})")
    check(passed <= daemon, f"every verb passed to the daemon is one app.py handles (not: {sorted(passed - daemon)})")
    with sandbox() as tmp:
        r, _ = holo(tmp, "no-such-verb")
        check(r.returncode == 1 and r.stdout.splitlines() == [h for h in header],
              "an unknown verb prints the whole usage header and exits 1")


def main():
    test_stop()
    test_restart()
    test_start()
    test_usage()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
