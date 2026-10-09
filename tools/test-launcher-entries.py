#!/usr/bin/env python3
"""VECTOR's app launcher entries (assets/applications/): what bromigOS's checks ask of every
entry (its tools/launcher/check.py), for the two this package ships.

  - each passes desktop-file-validate, and has a Name, GenericName, Comment (the launcher's
    hover hint), Keywords, Categories, an Icon, TryExec and X-Bromigos-Menu (the menu item
    it is, so bromigOS's menu lists it once);
  - its icon is in assets/icons at every size the package installs (16, 24, 32, scalable);
  - its Exec, and each action's, runs the right command: bin/bromigos-holo, run as the
    entry says against a stand-in daemon socket, sends exactly that verb, and holo/app.py
    handles the verb.

Nothing reaches a desktop: a throwaway HOME and runtime directory, no display.
Run: python3 tools/test-launcher-entries.py (the package's check() does).
"""
import configparser
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS = os.path.join(ROOT, "assets", "applications")
ICONS = os.path.join(ROOT, "assets", "icons")
SIZES = ("16x16", "24x24", "32x32", "scalable")
REQUIRED = ("Name", "GenericName", "Comment", "Keywords", "Categories", "Icon", "TryExec", "Exec",
            "X-Bromigos-Menu")
# entry -> the verbs its Exec and actions send to the daemon
EXPECT = {
    "org.bromigos.Vector.desktop": {"": "vector-show", "conversation": "conversation", "chat": "chat",
                                    "history": "history"},
    "org.bromigos.HologramGallery.desktop": {"": "gallery-show"},
}
fails = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        fails.append(what)


class FakeHolo:
    """bromigos-holo's daemon socket: answers status, records every other command."""

    def __init__(self, path):
        self.got = []
        self.s = socket.socket(socket.AF_UNIX)
        self.s.bind(path)
        self.s.listen(8)
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            with c:
                cmd = c.recv(65536).decode()
                if cmd != "status":
                    self.got.append(cmd)
                c.sendall(b"ok")


def main():
    handled = set(re.findall(r'verb == "([a-z-]+)"', open(os.path.join(ROOT, "holo", "app.py")).read()))
    have_validate = shutil.which("desktop-file-validate")
    check(bool(have_validate), "desktop-file-validate is installed (desktop-file-utils)")
    files = sorted(f for f in os.listdir(APPS) if f.endswith(".desktop"))
    check(set(files) == set(EXPECT), f"the entries are {sorted(EXPECT)}")
    for f in files:
        path = os.path.join(APPS, f)
        if have_validate:
            r = subprocess.run(["desktop-file-validate", path], capture_output=True, text=True)
            check(r.returncode == 0 and not r.stdout.strip(), f"{f}: desktop-file-validate {r.stdout.strip()}")
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.optionxform = str
        cp.read(path)
        e = cp["Desktop Entry"]
        for k in REQUIRED:
            check(bool(e.get(k, "").strip()), f"{f}: has {k}")
        for size in SIZES:
            check(os.path.isfile(os.path.join(ICONS, size, "apps", e["Icon"] + ".svg")), f"{f}: icon {e['Icon']} at {size}")
        execs = {"": e["Exec"]}
        for a in [x for x in e.get("Actions", "").split(";") if x]:
            execs[a] = cp[f"Desktop Action {a}"]["Exec"]
        for action, line in execs.items():
            argv = shlex.split(line)
            want = EXPECT.get(f, {}).get(action)
            check(argv[0] == "bromigos-holo" == e["TryExec"], f"{f} {action or 'Exec'}: runs bromigos-holo")
            check(want in handled, f"{f} {action or 'Exec'}: the daemon handles {want!r}")
            with tempfile.TemporaryDirectory() as tmp:
                fake = FakeHolo(os.path.join(tmp, "bromigos-holo.sock"))
                env = {"HOME": tmp, "XDG_RUNTIME_DIR": tmp, "XDG_STATE_HOME": os.path.join(tmp, "state"),
                       "PATH": "/usr/bin:/bin", "HYPRLAND_INSTANCE_SIGNATURE": "none", "LANG": "C.UTF-8"}
                r = subprocess.run([os.path.join(ROOT, "bin", "bromigos-holo"), *argv[1:]], env=env,
                                   capture_output=True, text=True, timeout=20)
                fake.s.close()
                check(r.returncode == 0 and fake.got == [want],
                      f"{f} {action or 'Exec'}: `{line}` sends {want!r} (sent {fake.got}, rc {r.returncode})")
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
