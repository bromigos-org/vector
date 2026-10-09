#!/usr/bin/env python3
"""Tests for reduced motion in the holograms (holo/motion.py; bromigOS docs/accessibility.md).
No GL, no desktop: the setting is read from a throwaway config, and VECTOR's construct, the
model stage and his console's scene run without drawing (their per-part states and the still
frame's signature are plain numbers).

    python3 tools/test-motion.py

1. The setting: accessibility.toml's `motion` (XDG_CONFIG_HOME honoured), else the theme's
   palette.json, else full; a bad value or a broken file falls through; followed at run time.
2. The clock: full is real time, reduced 0.4 of it, still stands; a mode change doesn't jump.
3. The construct: full blinks, flickers and glitches (the checks see it); reduced has no
   blink, spark flicker, crackle or voice-change glitch, and turns at 0.4 of full's pace;
   still holds identical parts until his state changes, then holds the new pose.
4. A model on the table: reduced has no shimmer and no scan band; still lands an explode at
   once and doesn't turn.
5. His console: still shows his words whole, its signature stays put until something it
   shows changes (a message, his state), and a reply that ends is noticed without a frame.
6. The window: under still no frame timer runs; a check redraws only on a change, and
   moving again brings the frame timer back.
"""
import json
import os
import random
import sys
import tempfile
import types

tmp = tempfile.mkdtemp(prefix="vector-motion-")
os.environ.update(HOME=tmp, XDG_CONFIG_HOME=os.path.join(tmp, "config"),
                  XDG_STATE_HOME=os.path.join(tmp, "state"), XDG_RUNTIME_DIR=os.path.join(tmp, "run"),
                  XDG_DATA_HOME=os.path.join(tmp, "share"), XDG_CACHE_HOME=os.path.join(tmp, "cache"))
for k in ("WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
    os.environ.pop(k, None)
os.makedirs(os.path.join(tmp, "config/bromigos"))
os.makedirs(os.path.join(tmp, "run"), mode=0o700)
A11Y = os.path.join(tmp, "config/bromigos/accessibility.toml")
PALETTE = os.path.join(tmp, "state/bromigos/theme/current/palette.json")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np  # noqa: E402

from holo import motion  # noqa: E402

bad = 0


def ok(cond, what, detail=""):
    global bad
    print(f"{'pass' if cond else 'FAIL'}  {what}" + (f" — {detail}" if detail else ""))
    bad += 0 if cond else 1


def setting(text):
    if text is None:
        if os.path.exists(A11Y):
            os.unlink(A11Y)
        return
    with open(A11Y, "w") as f:
        f.write(text)


class FakeTime:
    """time for the scene modules: a clock the test moves."""

    def __init__(self, t=1000.0):
        self.t = t

    def monotonic(self):
        return self.t

    def time(self):
        return 1.8e9 + self.t

    def __getattr__(self, name):
        import time
        return getattr(time, name)


# ---- 1. the setting
setting(None)
ok(motion.read() == "full", "no file, no palette: full")
for m in ("full", "reduced", "still"):
    setting(f'motion = "{m}"\ncontrast = "normal"\n')
    ok(motion.read() == m, f"accessibility.toml motion = {m}")
setting('motion = "fast"\n')
ok(motion.read() == "full", "an unknown value: full")
setting('motion = "reduced\n')
ok(motion.read() == "full", "a broken file: full")
os.makedirs(os.path.dirname(PALETTE))
from holo.render import T  # noqa: E402
if hasattr(T, "motion"):
    with open(PALETTE, "w") as f:
        json.dump({"theme": "wick", "motion": "still", "colors": {}}, f)
    setting(None)
    ok(motion.read() == "still", "no accessibility.toml: palette.json's motion (bromigos_theme.motion())")
    setting('contrast = "high"\n')
    ok(motion.read() == "still", "accessibility.toml without motion: palette.json's")
    setting('motion = "reduced"\n')
    ok(motion.read() == "reduced", "accessibility.toml wins over palette.json")
    os.unlink(PALETTE)
    T.version(0.0)
else:
    print("skip  palette.json fallback (this bromigos_theme has no motion())")
setting('motion = "full"\n')
motion.forget()
ok(motion.current() == "full", "current(): full")
setting('motion = "still"\n')
ok(motion.current(every=0.0) == "still", "current() follows a change at run time")
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "other")
os.makedirs(os.path.join(tmp, "other/bromigos"))
with open(os.path.join(tmp, "other/bromigos/accessibility.toml"), "w") as f:
    f.write('motion = "reduced"\n')
ok(motion.read() == "reduced", "XDG_CONFIG_HOME is honoured")
os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "config")

# ---- 2. the clock
c = motion.Clock()
ok(c(100.0) == 100.0 and c(101.0) == 101.0, "full: the clock is real time")
c = motion.Clock()
c(100.0, "reduced")
a = c(100.0, "reduced")
b = c(110.0, "reduced")
ok(abs((b - a) - 4.0) < 1e-9, "reduced: 0.4 of real time", f"{b - a:.3f} s in 10 s")
s0 = c(111.0, "still")
s1 = c(200.0, "still")
ok(s0 == s1 and abs(s0 - b) < 0.5, "still: the clock stands, and switching doesn't jump", f"{b:.2f} -> {s0:.2f}")
ok(abs(c(201.0, "full") - (s1 + 1.0)) < 1e-9, "back to full: it goes on from where it stood")

# ---- 3. the construct
from holo.vector import avatar as AV  # noqa: E402


def run(mode, seconds=40.0, fps=30, state="idle", alarm=False, voice_switch_at=None):
    random.seed(3)
    np.random.seed(3)
    a = AV.Avatar()
    a.mode = mode
    a.set_state(state)
    if alarm:
        a.set_mood("alarmed")
    aps, sparks, glitch, crackle, cols = [], [], [], [], []
    n = int(seconds * fps)
    for i in range(n):
        if voice_switch_at is not None and i == int(voice_switch_at * fps):
            a.set_voice_colour("#ff766f", switch=True)
        a.update(1.0 / fps, mode)
        pm, pc, pw = a.part_state()
        aps.append(float(a.aperture.x[0]))
        sparks.append(float(pm[AV.P_SPARK][0, :3].dot(pm[AV.P_SPARK][0, :3])))
        glitch.append(a.glitch)
        crackle.append(float(a.crackle.min()))
        cols.append(pc.copy())
    return a, np.array(aps), np.array(sparks), np.array(glitch), np.array(crackle), cols


fa, faps, _, fgl, fcr, _ = run("full", voice_switch_at=5.0, alarm=True)
ra, raps, _, rgl, rcr, rcols = run("reduced", voice_switch_at=5.0, alarm=True)
ok(faps.min() < 0.3, "full: he blinks (the iris closes; the check sees it)", f"lowest aperture {faps.min():.2f}")
ok(raps.min() > 0.3, "reduced: no blinking", f"lowest aperture {raps.min():.2f}")
ok(fgl.max() > 0.5 and fcr.min() < 1.0, "full: a voice change glitches, the alarm crackles (the check sees it)")
ok(rgl.max() == 0.0 and rcr.min() == 1.0, "reduced: no glitch at a voice change, no crackle")
# the spark's light, frame to frame: full flickers; reduced holds (its colour only fades, when
# a voice or a mood changes it)
step = lambda cols: max(abs(float(b[AV.P_SPARK, :3].sum() - a[AV.P_SPARK, :3].sum())) for a, b in zip(cols, cols[1:]))
_, _, fsp, _, _, fcols = run("full", seconds=10)
_, _, rsp, _, _, rcols2 = run("reduced", seconds=10)
ok(np.ptp(fsp) > 0.0 and step(fcols) > 0.1, "full: the spark flickers (the check sees it)", f"largest step {step(fcols):.3f}")
ok(np.ptp(rsp) < 1e-5 and step(rcols2) < 1e-6, "reduced: the spark holds steady (no flicker)",
   f"size spread {np.ptp(rsp):.1e}, light step {step(rcols2):.1e}")
ok(step(rcols) < step(fcols) / 4, "reduced: a voice change and an alarm fade the colours in, no jumps",
   f"largest step {step(rcols):.3f} (full's flicker {step(fcols):.3f})")
fa, *_ = run("full", seconds=10, state="thinking")
ra, *_ = run("reduced", seconds=10, state="thinking")
ok(abs(ra.spin_a / fa.spin_a - 0.4) < 0.01, "reduced: the gimbals turn at 0.4 of full's pace",
   f"{ra.spin_a:.2f} vs {fa.spin_a:.2f} rad")
sa, saps, ssp, _, _, scols = run("still", seconds=10, state="thinking")
ok(np.ptp(saps) == 0.0 and np.ptp(ssp) == 0.0 and all((c == scols[0]).all() for c in scols),
   "still: every frame's parts are identical (held)")
pm0, pc0, _ = sa.part_state()
sa.set_state("speaking")
sa.update(1 / 30, "still")
pm1, pc1, _ = sa.part_state()
ok(not np.allclose(pc0, pc1) or not np.allclose(pm0, pm1), "still: a new state is a new pose")
for _ in range(60):
    sa.update(1 / 30, "still")
pm2, pc2, _ = sa.part_state()
ok(np.array_equal(pm1, pm2) and np.array_equal(pc1, pc2), "still: ... which holds")

# ---- 4. a model on the table


class FakeSource:
    version = 0


class FakeLive:
    def __init__(self):
        self.sources = {k: FakeSource() for k in ("local", "lab")}

    def want(self, *a):
        pass

    def get(self, src):
        return None, None, None


from holo import stage as ST  # noqa: E402
ft = FakeTime()
ST.time = ft
for m in ("full", "reduced", "still"):
    s = ST.Stage("workstation", FakeLive())
    s.motion = m
    ft.t += 5.0
    s.scan()
    ft.t += 0.6
    shimmer = [s.part_state(ft.t + k / 30)[1][:, :3].sum() for k in range(30)]
    band = s.scan_uniform(ft.t)[2]
    if m == "full":
        ok(np.ptp(shimmer) > 0 and band > 0.1, "full: the parts shimmer and a scan sweeps a band (the check sees it)")
    else:
        ok(np.ptp(shimmer) < 1e-6 and band == 0.0, f"{m}: no shimmer, no scan band")
    y0 = s.yaw
    s.toggle_explode()
    s.update(1 / 30)
    if m == "still":
        ok(s.explode == 1.0 and s.yaw == y0, "still: an explode lands at once, and the model doesn't turn")
    elif m == "reduced":
        r_turn = s.yaw - y0
    else:
        f_turn = s.yaw - y0
ok(abs(r_turn / f_turn - 0.4) < 1e-6, "reduced: the model turns at 0.4 of full's pace")

# ---- 5. his console
from holo.vector import scene as SC  # noqa: E402
SC.time = ft
setting('motion = "still"\n')
motion.forget()
p = SC.VectorScene(FakeLive())
p.add_user("how's the lab?")
p.begin_reply()
p.feed("All six nodes are up.")
sig0 = p.still_sig()
ok(p.msgs[-1].shown == len(p.msgs[-1].text), "still: his words show whole (no typing in)")
ft.t += 1.0
ok(p.still_sig() == sig0, "still: the signature stays put while nothing changes")
p.feed(" And every service answers.")
ok(p.still_sig() != sig0, "still: a new word is a change")
sig1 = p.still_sig()
p.set_state("thinking")
ok(p.still_sig() != sig1, "still: a new state is a change")
p.set_state("speaking")
p.end_reply()
sig2 = p.still_sig()
for _ in range(4):                           # the window's checks, four a second
    ft.t += motion.STILL_CHECK_MS / 1000
    sig3 = p.still_sig()
ok(p.avatar.state == "idle" and sig3 != sig2, "still: a reply that ends is noticed without a frame (speaking -> idle)")
setting('motion = "full"\n')
motion.forget()

# ---- 6. the window (its timers, without a display)
try:
    from holo import app as APP
except Exception as e:                       # GTK 3 or the layer shell missing
    APP = None
    print(f"skip  the window's timers ({type(e).__name__}: {e})")
if APP is not None:
    timers = {}

    def timeout_add(ms, fn, *a):
        tid = len(timers) + 1
        timers[tid] = (ms, fn)
        return tid

    def source_remove(tid):
        timers.pop(tid, None)

    APP.GLib = types.SimpleNamespace(timeout_add=timeout_add, source_remove=source_remove)

    class Scene:
        sig = 1

        def still_sig(self):
            return self.sig

        def busy(self):
            return False

        def render(self, fbo, w, h):
            pass

    class Area:
        renders = 0

        def queue_render(self):
            Area.renders += 1

    w = APP.HoloWindow.__new__(APP.HoloWindow)
    w.scene, w.area, w.visible = Scene(), Area(), True
    w.timer = w.still_timer = w.still_drawn = None
    w.fps = 0
    setting('motion = "still"\n')
    motion.forget()
    w.set_fps(60)
    ok(w.timer is None and w.still_timer is not None and timers[w.still_timer][0] == motion.STILL_CHECK_MS,
       "still: no frame timer; a change check instead")
    w.still_drawn = w.scene.still_sig()
    n = Area.renders
    for _ in range(8):
        w._still_check()
    ok(Area.renders == n, "still: nothing changed, nothing drawn", f"{Area.renders - n} renders")
    w.scene.sig = 2
    w._still_check()
    ok(Area.renders == n + 1, "still: a change is drawn")
    setting('motion = "reduced"\n')
    motion.forget()
    keep = w._still_check()
    ok(keep is False and w.still_timer is None and w.timer is not None and w.fps == 30,
       "moving again: the frame timer is back")
    setting('motion = "still"\n')
    motion.forget()
    w._tick()
    ok(w.timer is None and w.still_timer is not None, "still again while shown: the frame timer stops")
    w.set_fps(0)
    ok(w.timer is None and w.still_timer is None, "hidden: no timers at all")

print("all passed" if not bad else f"{bad} failed")
sys.exit(1 if bad else 0)
