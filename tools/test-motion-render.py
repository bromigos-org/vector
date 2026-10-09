#!/usr/bin/env python3
"""Offscreen render checks for reduced motion in the holograms (holo/motion.py; bromigOS
docs/accessibility.md), in the style of bromigOS's live/tests/offscreen_motion.py.

Renders the model gallery and VECTOR's console (thinking, with an exhibit on his table)
headless (EGL: an NVIDIA device, else Mesa; never a window, never the running desktop) under
each motion setting, frame by frame at 30 fps on a clock the test moves, and checks what a
viewer would see:

  - full motion animates; reduced animates less; still holds one frame (the same pixels: no
    pixel moves by more than the GPU's own rounding, one level in 255 on a handful of pixels,
    which the same state drawn twice shows too; and the window doesn't draw it again anyway);
  - reduced and still have no flashes: no tile of the picture (a 4x4 grid) changes relative
    luminance by 0.10 or more (WCAG 2.3.1's "general flash" step) from one frame to the
    next, a scan, a voice change's glitch and an alarm included;
  - still draws a new frame when what it shows changes (his state, a message, the model
    exploded) and holds that one too; with no change its signature stays the same.

Everything runs in a throwaway HOME and runtime dir; readings are a fixed "reading…" (a
stand-in for the live sources), so runs repeat.

    python3 tools/test-motion-render.py [--out DIR]   # DIR: sample PNGs; exit 1 on a failure
"""
import ctypes
import os
import random
import shutil
import sys
import tempfile

SANDBOX = tempfile.mkdtemp(prefix="vector-motion-render-")
for k in ("WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
    os.environ.pop(k, None)
for k, sub in (("HOME", ""), ("XDG_CONFIG_HOME", ".config"), ("XDG_STATE_HOME", ".local/state"),
               ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache"), ("XDG_RUNTIME_DIR", "run")):
    os.environ[k] = os.path.join(SANDBOX, sub)
    os.makedirs(os.environ[k], exist_ok=True)
os.chmod(os.environ["XDG_RUNTIME_DIR"], 0o700)
os.makedirs(os.path.join(SANDBOX, ".config/bromigos"))
A11Y = os.path.join(SANDBOX, ".config/bromigos/accessibility.toml")
os.environ["PYOPENGL_PLATFORM"] = "egl"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

import numpy as np  # noqa: E402
from OpenGL import EGL, GL  # noqa: E402

FPS = 30
STEP = 0.10                     # WCAG 2.3.1: a change of 10% of relative luminance
TILES = 4

failures, lines = [], []
NOISE_PX = 1e-4                 # at most this share of pixels may differ, by one level: GPU rounding


def held(a, b):
    """The same picture: no pixel more than one level apart, and only a handful that are."""
    d = np.abs(a - b).max(axis=2)
    return d.max() <= 1.0 and (d > 0).mean() <= NOISE_PX


def report(name, ok, detail):
    lines.append(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    if not ok:
        failures.append(name)


def egl():
    """A surfaceless GL 3.3 core context: the NVIDIA EGL device, else the default display."""
    attrs = [EGL.EGL_CONTEXT_MAJOR_VERSION, 3, EGL.EGL_CONTEXT_MINOR_VERSION, 3,
             EGL.EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL.EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL.EGL_NONE]
    dpy = None
    n = EGL.EGLint()
    try:
        from OpenGL.EGL.EXT.device_base import eglQueryDevicesEXT
        from OpenGL.EGL.EXT.platform_base import eglGetPlatformDisplayEXT
        from OpenGL.EGL.EXT.platform_device import EGL_PLATFORM_DEVICE_EXT
        devs = (EGL.EGLDeviceEXT * 8)()
        eglQueryDevicesEXT(8, devs, ctypes.byref(n))
        for i in range(n.value):
            d = eglGetPlatformDisplayEXT(EGL_PLATFORM_DEVICE_EXT, devs[i], None)
            if EGL.eglInitialize(d, None, None) and b"NVIDIA" in (EGL.eglQueryString(d, EGL.EGL_VENDOR) or b""):
                dpy = d
                break
    except Exception:
        dpy = None
    if dpy is None:
        dpy = EGL.eglGetDisplay(EGL.EGL_DEFAULT_DISPLAY)
        EGL.eglInitialize(dpy, None, None)
    EGL.eglBindAPI(EGL.EGL_OPENGL_API)
    cfg = EGL.EGLConfig()
    EGL.eglChooseConfig(dpy, [EGL.EGL_SURFACE_TYPE, EGL.EGL_PBUFFER_BIT, EGL.EGL_RENDERABLE_TYPE, EGL.EGL_OPENGL_BIT,
                              EGL.EGL_NONE], ctypes.byref(cfg), 1, ctypes.byref(n))
    ctx = EGL.eglCreateContext(dpy, cfg, EGL.EGL_NO_CONTEXT, attrs)
    EGL.eglMakeCurrent(dpy, EGL.EGL_NO_SURFACE, EGL.EGL_NO_SURFACE, ctx)


class Target:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.fbo = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        rb = GL.glGenRenderbuffers(1)
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, rb)
        GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_RGBA8, w, h)
        GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_RENDERBUFFER, rb)

    def read(self):
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        px = GL.glReadPixels(0, 0, self.w, self.h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
        img = np.frombuffer(px, np.uint8).reshape(self.h, self.w, 4)[::-1].astype(np.float32)
        # as seen over a dark desktop (premultiplied over black is the rgb itself)
        return img[..., :3]


def tiles(rgb):
    c = rgb / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    y = lin @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    h, w = y.shape
    return y[:h - h % TILES, :w - w % TILES].reshape(TILES, h // TILES, TILES, w // TILES).mean(axis=(1, 3))


class FakeTime:
    def __init__(self, t=5000.0):
        self.t = t

    def monotonic(self):
        return self.t

    def time(self):
        return 1.8e9 + self.t

    def __getattr__(self, name):
        import time
        return getattr(time, name)


CLOCK = FakeTime()


class FakeSource:
    version = 0


class FakeLive:
    def __init__(self):
        self.sources = {k: FakeSource() for k in ("local", "lab")}

    def want(self, *a):
        pass

    def get(self, src):
        return None, None, None


def set_motion(mode):
    from holo import motion
    with open(A11Y, "w") as f:
        f.write(f'motion = "{mode}"\n')
    motion.forget()
    assert motion.current() == mode


class Run:
    def __init__(self, scene, tgt):
        self.scene, self.tgt = scene, tgt
        self.prev = self.prev_t = None
        self.diffs, self.steps, self.kept, self.moved = [], [], [], []

    def frame(self, keep=False):
        self.scene.render(self.tgt.fbo, self.tgt.w, self.tgt.h)
        img = self.tgt.read()
        tl = tiles(img)
        if self.prev is not None:
            self.diffs.append(float(np.abs(img - self.prev).mean()))
            self.moved.append(not held(img, self.prev))
            self.steps.append(float(np.abs(tl - self.prev_t).max()))
        self.prev, self.prev_t = img, tl
        if keep:
            self.kept.append(img)
        return img


def frames(scene, tgt, seconds, each=None, keep=()):
    run = Run(scene, tgt)
    for i in range(int(round(seconds * FPS)) + 1):
        CLOCK.t += 1.0 / FPS
        if each:
            each(i / FPS, scene)
        run.frame(keep=i in keep)
    return run


def save(out, name, img):
    if out:
        from PIL import Image
        Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).save(os.path.join(out, name + ".png"))


def patch_time():
    from holo import gallery, stage, text
    from holo.vector import scene
    for mod in (gallery, stage, text, scene):
        mod.time = CLOCK


def check_gallery(out):
    from holo.gallery import Gallery
    tgt = Target(880, 520)
    res = {}
    for mode in ("full", "reduced", "still"):
        set_motion(mode)
        random.seed(5)
        np.random.seed(5)
        g = Gallery(FakeLive(), scale=0.5)
        g.show("workstation")                 # materialises and scans: a sweep of light in full

        def each(t, sc):
            if abs(t - 1.5) < 1e-6:
                sc.stage.scan()               # fresh readings: another scan half-way
        run = frames(g, tgt, 3.0, each=each, keep=(45,))
        res[mode] = run
        run.held_run = list(run.moved), list(run.diffs)
        save(out, f"gallery-{mode}", run.kept[0])
        if mode == "still":
            sig = g.still_sig()
            CLOCK.t += 2.0
            report("gallery: still: nothing changes, the signature holds", g.still_sig() == sig, "the same after 2 s")
            g.on_key("space")                 # explode it
            report("gallery: still: an explode is a change", g.still_sig() != sig, "signature changed")
            before = run.prev.copy()
            img = run.frame()
            moved = float(np.abs(img - before).mean())
            again = frames(g, tgt, 1.0)
            report("gallery: still: the change is one new frame, then held",
                   moved > 0.5 and not any(again.moved),
                   f"new frame differs by {moved:.2f}; then {sum(again.moved)} frames move")
    f, rd = np.mean(res["full"].diffs), np.mean(res["reduced"].diffs)
    report("gallery: full motion animates", f > 0.05, f"mean change {f:.3f}/frame")
    report("gallery: reduced animates less", 0 < rd < f, f"mean change {rd:.3f}/frame (full {f:.3f})")
    mv, df = res["still"].held_run
    report("gallery: still holds one frame", not any(mv),
           f"{sum(mv)} of {len(mv)} frames move (largest mean change {max(df):.4f}: GPU rounding)")
    report("gallery: full's scan (for comparison)", True, f"largest tile step {max(res['full'].steps):.4f}")
    for m in ("reduced", "still"):
        s = max(res[m].steps)
        report(f"gallery: {m}: no flash (scans included)", s < STEP, f"largest tile step {s:.4f} (< {STEP})")


def check_vector(out):
    from holo.vector.scene import VectorScene
    tgt = Target(1180, 640)
    res = {}
    for mode in ("full", "reduced", "still"):
        set_motion(mode)
        random.seed(5)
        np.random.seed(5)
        p = VectorScene(FakeLive())
        p.demo_transcript()
        p.panel = p.panel_to = 1.0
        p.set_state("thinking")
        p.exhibit("workstation", ["cpu", "gpu"])

        def each(t, sc):
            if abs(t - 1.0) < 1e-6:
                sc.set_voice("robot")         # a voice change: the iris's tremor in full
            if abs(t - 2.0) < 1e-6:
                sc.set_mood("alarmed")        # an alarm: crackle and a harder iris in full
                sc.memory_flash("recall", 2)
        run = frames(p, tgt, 4.0, each=each, keep=(30, 75))
        res[mode] = run
        run.held_run = list(run.moved)
        save(out, f"vector-{mode}-0", run.kept[0])
        save(out, f"vector-{mode}-1", run.kept[1])
        if mode == "still":
            CLOCK.t += 2.5                    # the memory flash ends, a frame later
            run.frame()
            hd = frames(p, tgt, 1.0)
            sig = p.still_sig()
            CLOCK.t += 1.0
            report("vector: still: nothing changes, the signature holds", p.still_sig() == sig and not any(hd.moved),
                   f"{sum(hd.moved)} frames move")
            p.add_user("and the GPU?")
            p.set_state("listening")
            report("vector: still: a message and a new state are a change", p.still_sig() != sig, "signature changed")
            before = hd.prev.copy()
            run.prev = before
            img = run.frame(keep=True)
            moved = float(np.abs(img - before).mean())
            again = frames(p, tgt, 1.0)
            report("vector: still: the change is one new frame, then held",
                   moved > 0.1 and not any(again.moved),
                   f"new frame differs by {moved:.2f}; then {sum(again.moved)} frames move")
            save(out, "vector-still-2", img)
    f, rd = np.mean(res["full"].diffs), np.mean(res["reduced"].diffs)
    report("vector: full motion animates", f > 0.05, f"mean change {f:.3f}/frame")
    report("vector: reduced animates less", 0 < rd < f, f"mean change {rd:.3f}/frame (full {f:.3f})")
    st = res["still"].held_run
    # still: the same frame, except where what it shows changed: the alarm and the memory flash
    # (frame 60), the flash's end (1.8 s later); the voice change (frame 30) doesn't show while
    # he thinks (his colour is the state's)
    moving = [i + 1 for i, m in enumerate(st) if m]
    report("vector: still holds its frame between changes", moving == [60, 114],
           f"frames {moving} of {len(st)} move (the alarm with a memory flash at 60, the flash gone at 114)")
    report("vector: full's glitch and crackle (for comparison)", True, f"largest tile step {max(res['full'].steps):.4f}")
    for m in ("reduced", "still"):
        s = max(res[m].steps)
        report(f"vector: {m}: no flash (a voice change, an alarm, a memory flash)", s < STEP,
               f"largest tile step {s:.4f} (< {STEP})")


def main():
    out = None
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
        os.makedirs(out, exist_ok=True)
    egl()
    patch_time()
    try:
        check_gallery(out)
        check_vector(out)
    finally:
        shutil.rmtree(SANDBOX, ignore_errors=True)
    print("\n".join(lines))
    print(f"\n{len(lines) - len(failures)}/{len(lines)} checks pass" + (f"; failed: {', '.join(failures)}" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
