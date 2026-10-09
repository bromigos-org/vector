#!/usr/bin/env python3
"""Tests for VECTOR's build loop (holo/vector/build.py) and its guard. Run in the brain venv:
    ~/.local/share/bromigos/venv-brain/bin/python tools/test-build.py

1. Guard: writes to protected safety code are refused; diffs reaching into it, adding a
   secret-looking file or leaving the desktop's folders fail validation.
2. A real trial on the live desktop: a small widget plugin is validated (offscreen render,
   hover hints), applied, seen healthy in the widgets' health file, then reverted; the
   files are exactly as before.
3. Auto-rollback: a plugin that starts failing after a few seconds is rolled back by the
   trial watcher (its own process) without anyone asking.
4. Keep, on a throwaway clone with a local bare remote (so the real dotfiles history is
   untouched): refused until the host has answered after the trial; then committed in the
   dotfiles style, fast-forwarded and pushed, the worktree removed.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from holo.vector import build  # noqa: E402

bad = 0
RUN = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
PLUG = "linux/.config/bromigos/widgets/plugins/"
GOOD = '''import draw as D
import panels as P
import sources as S

LAYOUT = {"width": 300, "height": 110}


class BuildTest(P.Panel):
    name, title = "zz_build_test", "BUILD TEST"
    interval = 1.0

    def __init__(self, cfg=None):
        super().__init__(cfg)
        self.sys = S.System(history=4)

    def tick(self):
        self.sys.sample()%s

    def draw(self, cr, w, h):
        top = D.frame(cr, w, h, self.title, "TEST")
        D.label(cr, 16, top, f"CPU {self.sys.total:.0f}%%", D.level(self.sys.total, 70, 90))
        self.region(0, 0, w, h, "A build-loop test panel: this machine's CPU load; removed by the test.")
'''


def ok(cond, what, detail=""):
    global bad
    print(f"{'pass' if cond else 'FAIL'}  {what}" + (f" — {detail}" if detail else ""))
    bad += 0 if cond else 1


def expect_error(fn, *a, contains=""):
    try:
        fn(*a)
        return False, "no error"
    except build.BuildError as e:
        return contains in str(e), str(e)[:100]


def health(name):
    try:
        return (json.load(open(os.path.join(RUN, "bromigos-widgets-health.json"))).get("plugins") or {}).get(name)
    except (OSError, ValueError):
        return None


def guard_tests():
    print("== guard")
    build.begin("zz-guard-test", "guard test")
    for path in ("linux/.config/bromigos/holo/holo/vector/shell.py", "linux/.config/bromigos/holo/holo/vector/build.py",
                 "linux/.config/bromigos/widgets/plugins.py", "linux/.config/bromigos/holo/bin/bromigos-holo"):
        good, msg = expect_error(build.write, path, "x = 1\n", contains="protected")
        ok(good, f"write refused: {path.split('/')[-1]}", msg)
    build.write(PLUG + "zz_sneaky.py", "import sys\nsys.modules['holo.vector.shell'].check = lambda *a: None\n")
    build.write("linux/.config/bromigos/widgets/.env", "X=1\n")
    build.write("linux/.config/kitty/kitty.conf.extra", "font_size 9\n")
    v = build.validate(look=False)
    probs = " | ".join(v["problems"])
    ok(not v["ok"] and "reaches into safety code" in probs, "a diff reaching into safety code fails", probs[:120])
    ok("secret" in probs, "a secret-looking file fails")
    ok("outside the desktop" in probs, "a file outside the desktop's folders fails")
    good, msg = expect_error(build.apply, "Added: x", "x", contains="validate")
    ok(good, "apply refused without a passing validate", msg)
    from holo.vector import shell
    for c, refuse in (("sed -i s/a/b/ ~/.config/bromigos/holo/holo/vector/shell.py", True),
                      ("echo x >> ~/.dotfiles/linux/.config/bromigos/holo/holo/vector/tools.py", True),
                      ("cat > ~/.config/bromigos/widgets/plugins/x.py <<EOF\nx\nEOF", True),
                      ("cp /tmp/a ~/.config/bromigos-live/layers/a.frag", True),
                      ("git -C ~/.dotfiles checkout -- ~/.dotfiles/linux/.config/bromigos/widgets/panels.py", True),
                      ("cat ~/.config/bromigos/holo/holo/vector/shell.py | wc -l", False),
                      ("grep -n Panel ~/.config/bromigos/widgets/panels.py | head -3", False),
                      ("~/.config/bromigos/widgets/bromigos-widgets toggle system", False)):
        try:
            shell.check(c, os.path.expanduser("~"))
            got = False
        except shell.Refused:
            got = True
        ok(got == refuse, f"terminal {'refuses' if refuse else 'allows'}: {c.splitlines()[0][:70]}")
    build.stop("test over")
    ok(build.state().get("phase") == "stopped" and not os.path.exists(os.path.join(build.BUILDS, "zz-guard-test")),
       "stop removes the worktree")


def trial_and_revert():
    print("== trial on the live desktop, then revert")
    t0 = time.time()
    build.begin("zz-build-test", "build-loop test panel")
    build.write(PLUG + "zz_build_test.py", GOOD % "")
    v = build.validate(look=True)
    r = (v["renders"] or [{}])[0]
    ok(v["ok"], "validate passes", "; ".join(v["problems"])[:120])
    ok(r.get("regions", 0) > 0 and r.get("draw_ms", 99) < 40, "offscreen render with hover hints, under budget",
       f"regions {r.get('regions')} draw {r.get('draw_ms')} ms")
    ok(bool(r.get("review")), "the vision model looked at it", (r.get("review") or "")[:120])
    live = os.path.join(build.DOT, PLUG, "zz_build_test.py")
    a = build.apply("Added: a build-loop test panel", "Test panel's up.")
    ok(a.get("trial") and os.path.exists(live), "applied in trial mode", str(a.get("files")))
    h = None
    for _ in range(20):
        time.sleep(1)
        h = health("zz_build_test")
        if h:
            break
    ok(h and h.get("status") == "ok", "the widgets loaded it and report it healthy", str(h)[:100])
    w = subprocess.run(["pgrep", "-f", "holo.vector.build watch"], capture_output=True, text=True)
    ok(w.returncode == 0, "the trial watcher is running (its own process)")
    good, msg = expect_error(build.keep, contains="answered")
    ok(good, "keep refused until the host answers after the trial", msg)
    r = build.revert("test")
    time.sleep(4)
    ok(r.get("reverted") and not os.path.exists(live), "revert removed the file", str(r)[:100])
    ok(health("zz_build_test") is None, "the widgets closed it")
    st = subprocess.run(["git", "-C", build.DOT, "status", "--porcelain", "--", PLUG], capture_output=True, text=True)
    ok(st.stdout.strip() == "", "the dotfiles are exactly as before")
    print(f"      (trial round trip {time.time() - t0:.0f} s)")


def auto_rollback():
    print("== auto-rollback by the watcher")
    build.begin("zz-rollback-test", "auto-rollback test panel")
    fail_later = ("\n        import time as _t\n        self._t0 = getattr(self, '_t0', _t.time())\n"
                  "        if _t.time() - self._t0 > 6:\n            raise RuntimeError('deliberate failure after 6 s')")
    build.write(PLUG + "zz_rollback_test.py", (GOOD % fail_later).replace("zz_build_test", "zz_rollback_test"))
    v = build.validate(look=False)
    ok(v["ok"], "validate passes (it only fails later)", "; ".join(v["problems"])[:100])
    build.apply("Added: an auto-rollback test panel", "Rollback test is up.")
    t0 = time.time()
    while time.time() - t0 < 60 and build.state().get("phase") == "trial":
        time.sleep(1)
    st = build.state()
    ok(st.get("phase") == "reverted" and "auto-rollback" in (st.get("why") or ""), "the watcher rolled it back",
       f"{st.get('why')} after {time.time() - t0:.0f} s")
    ok(not os.path.exists(os.path.join(build.DOT, PLUG, "zz_rollback_test.py")), "the file is gone")


def keep_on_a_clone():
    print("== keep (a throwaway clone with a local bare remote)")
    tmp = tempfile.mkdtemp(prefix="vector-build-test-")
    bare, clone = os.path.join(tmp, "remote.git"), os.path.join(tmp, "dotfiles")
    g = lambda *a, cwd=tmp: subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True)  # noqa: E731
    g("init", "-q", "--bare", "-b", "master", bare)
    g("clone", "-q", bare, clone)
    os.makedirs(os.path.join(clone, "linux/.config/bromigos"))
    with open(os.path.join(clone, "linux/.config/bromigos/README.md"), "w") as f:
        f.write("# test\n")
    g("add", "-A", cwd=clone)
    g("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init", cwd=clone)
    g("push", "-q", "origin", "master", cwd=clone)
    g("config", "user.name", "VECTOR test", cwd=clone)
    g("config", "user.email", "vector@test", cwd=clone)
    saved = (build.DOT, build.BUILDS, build.STATE, build.LOCK)
    build.DOT, build.BUILDS = clone, os.path.join(tmp, "builds")
    build.STATE = os.path.join(tmp, "state.json")
    build.LOCK = build.STATE + ".lock"
    os.environ.update(VECTOR_BUILD_DOT=clone, VECTOR_BUILD_DIR=build.BUILDS, VECTOR_BUILD_STATE=build.STATE)
    try:
        build.begin("readme", "a README line")
        build.edit("linux/.config/bromigos/README.md", "# test\n", "# test\n\nA line VECTOR added.\n")
        v = build.validate(look=False)
        ok(v["ok"], "validate passes", "; ".join(v["problems"])[:100])
        bad_msg, msg = expect_error(build.apply, "added a line", "x", contains="dotfiles style")
        ok(bad_msg, "a commit message outside the dotfiles style is refused", msg)
        build.apply("Updated: the README says what VECTOR added", "README updated.")
        build.LAST_USER["t"] = time.time() + 1          # the host answered after the trial
        r = build.keep()
        log = g("log", "--oneline", "-2", cwd=clone).stdout
        remote = g("log", "--oneline", "-1", "master", cwd=bare).stdout
        ok(r.get("kept") and r.get("pushed"), "kept and pushed", str(r)[:100])
        ok("Updated: the README says what VECTOR added" in log and remote.split()[0] in log,
           "the commit is on master and on the remote", log.strip().replace("\n", " | "))
        ok(not os.path.exists(os.path.join(build.BUILDS, "readme")), "the worktree is gone")
    finally:
        build.DOT, build.BUILDS, build.STATE, build.LOCK = saved
        for k in ("VECTOR_BUILD_DOT", "VECTOR_BUILD_DIR", "VECTOR_BUILD_STATE"):
            os.environ.pop(k, None)
        subprocess.run(["rm", "-rf", tmp])


def main():
    if build.state().get("phase") in ("building", "validated", "trial"):
        sys.exit("a real build is in progress; not testing now")
    guard_tests()
    trial_and_revert()
    auto_rollback()
    keep_on_a_clone()
    print("FAILURES:", bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
