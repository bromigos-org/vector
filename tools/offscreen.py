#!/usr/bin/env python3
"""Render the hologram scenes headless (EGL, NVIDIA device) to PNG, without touching the
desktop. For screenshots and tuning.

  offscreen.py gallery OUT.png [model] [explode 0..1] [iso part id] [t seconds]
  offscreen.py vector OUT.png [state] [exhibit] [t seconds]
      (env OFF_CHAT=0 folds the chat box away; OFF_CONV=1 shows conversation mode;
       OFF_LONG=1 a long conversation, OFF_SCROLL=N scrolled back N px,
       OFF_ENTRY=N an input box grown to N px tall: the transcript ends above it;
       OFF_ATTACH=1 a message sent with attachments)
  offscreen.py vector-input OUT.png
      the input box with attachment chips, + ATTACH and some typed text: GTK widgets, drawn
      by GTK's broadway backend on a private broadwayd (no display, nothing on screen);
      OFF_CHIPS=N shows N chips (0 to 5, default 3)

The readings are this machine's and the lab's, live; OFF_DEMO=1 uses made-up ones instead
(DemoLive), for anything published. The frame sits over bromigOS's packaged empty den
(OFF_BG names another plate): never a user's own wallpaper, which may show a person.
"""
import ctypes
import os
import sys
import time

os.environ["PYOPENGL_PLATFORM"] = "egl"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

import numpy as np  # noqa: E402
from OpenGL import EGL, GL  # noqa: E402

W, H = int(os.environ.get("OFF_W", 1760)), int(os.environ.get("OFF_H", 1040))
PLATE = "/usr/share/bromigos/wallpaper/bromigos-den-empty-2560x1440.jpg"


class DemoLive:
    """Made-up readings for this machine and a lab: nothing on a render names a real host."""

    MACHINE = {"host": "workstation", "kernel": "6.12.8-arch1-1", "cpu": 14.0, "cpu_max": 61.0, "cores": 32,
             "cpu_temp": 52.0, "freq": 4200.0, "load": (1.42, 1.10, 0.96), "uptime": 3 * 86400 + 5 * 3600,
             "mem_used": 21.4 * 2**30, "mem_total": 64 * 2**30, "mem_pct": 33.0, "swap_used": 0.4 * 2**30,
             "swap_total": 16 * 2**30,
             "gpu": {"name": "RTX 4080", "util": 9, "vram_used": 3.1 * 2**30, "vram_total": 16 * 2**30, "temp": 44,
                     "watts": 38.0, "fan": 30},
             "fans": [("cpu fan", 1180), ("rear", 920), ("front", 860)],
             "disks": [{"mount": "/", "dev": "nvme0n1p2", "pct": 41.0, "used": 0.8e12, "total": 2e12},
                       {"mount": "/data", "dev": "sda1", "pct": 63.0, "used": 2.5e12, "total": 4e12}],
             "disk_rd": 2.4e6, "disk_wr": 1.1e6, "net_rx": 3.2e6, "net_tx": 0.4e6}
    LAB = {"cluster": {"nodesReady": 4, "nodesTotal": 4, "podsRunning": 96, "podsNotRunning": 0, "alertsFiring": 1,
                       "cpuNow": 23.0},
           "network": {"wanDownBps": 1.8e6, "wanUpBps": 0.6e6, "clients": 21, "guests": 2, "ispLatencyMs": 14,
                       "poeWatts": 38},
           "services": {k: {"status": "up"} for k in ("grafana", "prometheus", "argocd", "vault", "searxng", "gitea",
                                                      "jellyfin", "home-assistant", "minio", "ollama")},
           "gpu": {"count": 2, "utilNow": 31, "tempC": 58, "powerW": 212, "vramUsed": 20e9, "vramTotal": 48e9},
           "nas": {"pool": {"used": 18e12, "total": 40e12, "raidMembers": 6}, "disks": [{"health": "good"}] * 6},
           "nodes": [{"name": "node-1", "cpuPct": 31, "memPct": 58}, {"name": "node-2", "cpuPct": 22, "memPct": 47},
                     {"name": "node-3", "cpuPct": 12, "memPct": 40}, {"name": "node-4", "cpuPct": 9, "memPct": 35}],
           "nodeDisks": [{"node": "node-1", "pct": 48}],
           "proxmox": {"cpuPct": 18, "memPct": 61, "vms": [{"status": "running"}] * 5},
           "ai": {"models": [{"up": True}] * 3, "rpmNow": 4.2},
           "argocd": {"healthy": 24, "total": 24}, "traefik": {"rpsNow": 3.4},
           "solar": {"productionW": 2100, "energyTodayWh": 9400, "lifetimeWh": 12.4e6, "status": "normal"}}

    class _Source:
        version = 1

    def __init__(self):
        self.sources = {"local": self._Source(), "lab": self._Source()}

    def want(self, *names):
        pass

    def get(self, name):
        d = {"local": self.MACHINE, "lab": self.LAB}.get(name)
        return (d, None, time.time() - 1) if d else (None, "no such source here", 0.0)

    def version(self):
        return 1


def egl():
    """A surfaceless GL 3.3 core context: the NVIDIA EGL device, else the default display
    (Mesa: run with EGL_PLATFORM=surfaceless and no WAYLAND_DISPLAY for a software render)."""
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


def target():
    fbo = GL.glGenFramebuffers(1)
    GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
    rb = GL.glGenRenderbuffers(1)
    GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, rb)
    GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_RGBA8, W, H)
    GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_RENDERBUFFER, rb)
    return fbo


def save(fbo, out):
    from PIL import Image
    GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
    px = GL.glReadPixels(0, 0, W, H, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
    a = np.frombuffer(px, np.uint8).reshape(H, W, 4)[::-1]
    # show it over the empty den, as it would sit on the desktop
    bgp = os.path.expanduser(os.environ.get("OFF_BG") or PLATE)
    rgb = a[..., :3].astype(np.float32)
    al = a[..., 3:4].astype(np.float32) / 255
    if os.path.exists(bgp):
        bg = np.asarray(Image.open(bgp).convert("RGB").resize((W, H)), np.float32)
        rgb = rgb + bg * (1 - al)            # premultiplied over
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(out)
    print(out)


def input_box(out):
    """VECTOR's input box (holo/app.py InputBox) with three attachment chips, drawn offscreen
    by GTK on a broadwayd of its own, over the console's dark green."""
    import shutil
    import subprocess
    if os.environ.get("GDK_BACKEND") != "broadway":
        exe = shutil.which("broadwayd")
        if not exe:
            sys.exit("vector-input needs broadwayd (gtk3)")
        disp = f":{40 + os.getpid() % 50}"
        d = subprocess.Popen([exe, disp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        env = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY", "DISPLAY")}
        env.update(GDK_BACKEND="broadway", BROADWAY_DISPLAY=disp)
        try:
            sys.exit(subprocess.call([sys.executable, *sys.argv], env=env))
        finally:
            d.terminate()
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk
    from holo import app
    from holo.render import T
    from holo.vector import attach
    css = Gtk.CssProvider()
    css.load_from_data((T.retint(app.CSS.decode()) + "#vector-input-back { background-color: rgb(0, 9, 0); }").encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER)
    box = app.InputBox(1180 - 540 - 14 - 30)
    box.entry.set_text("what's wrong with this build?")
    box.set_margin_start(16); box.set_margin_end(16); box.set_margin_top(14); box.set_margin_bottom(14)
    win = Gtk.OffscreenWindow()
    back = Gtk.EventBox(name="vector-input-back")    # the console's backing behind the box
    back.add(box)
    win.add(back)
    win.show_all()
    for _ in range(50):
        Gtk.main_iteration_do(False)
    n = int(os.environ.get("OFF_CHIPS", 3))           # chips are laid out by their styled width: added once shown
    for a in [attach.Attachment("build-error.png", "image", 245760, path="/home/user/Pictures/build-error.png"),
              attach.Attachment("cargo-build.log", "text", 18432, path="/home/user/wick-relay/cargo-build.log"),
              attach.Attachment("relay-beam-specification-rev4.pdf", "pdf", 1288490, path="/home/user/relay.pdf"),
              attach.Attachment("pasted image.png", "image", 88064, data=b""),
              attach.Attachment("notes.md", "text", 2048, path="/home/user/notes.md")][:n]:
        box._add(a)
    win.check_resize()                                # an offscreen window has no frame clock to do it
    win.queue_draw()
    for _ in range(40):
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.01)
    win.get_pixbuf().savev(out, "png", [], [])
    print(out)


def main():
    kind, out = sys.argv[1], sys.argv[2]
    args = sys.argv[3:]
    if kind == "vector-input":
        return input_box(out)
    egl()
    if os.environ.get("OFF_DEMO") == "1":
        live = DemoLive()
    else:
        from holo.live import Live
        live = Live()
    fbo = target()
    if kind == "gallery":
        from holo.gallery import Gallery
        g = Gallery(live)
        model = args[0] if args else "workstation"
        g.show(model)
        s = g.stage
        s.poll()
        time.sleep(float(os.environ.get("OFF_WARM", 4)))
        s.explode_to = s.explode = float(args[1]) if len(args) > 1 else 0.0
        if len(args) > 2 and args[2] != "-":
            s.iso = [p["id"] for p in s.parts].index(args[2])
            s.iso_amt[s.iso] = 1.0
        tt = float(args[3]) if len(args) > 3 else 3.0
        now = time.monotonic()
        s.born = now - tt
        s.scan_t0 = now - float(os.environ.get("OFF_SCAN", 1.2))
        s.read_at = 0
        s.fade = 1.0
        g.last_t = now
        g.render(fbo, W, H)       # first frame lays out callouts
        s.read_at = 0
        g.render(fbo, W, H)
        save(fbo, out)
    elif kind == "vector":
        from holo.vector.scene import VectorScene
        p = VectorScene(live)
        state = args[0] if args else "idle"
        p.set_state(state)
        if len(args) > 1 and args[1] != "-":
            p.exhibit(args[1], os.environ.get('OFF_PARTS', '').split(',') if os.environ.get('OFF_PARTS') else None)
        time.sleep(float(os.environ.get("OFF_WARM", 4)))
        tt = float(args[2]) if len(args) > 2 else 3.0
        p.demo_transcript()
        p.history_open = os.environ.get("HISTORY_OPEN") == "1"
        p.conversation = os.environ.get("OFF_CONV") == "1"          # conversation mode's notice
        p.panel = p.panel_to = float(os.environ.get("OFF_CHAT", 1))  # 0: chat box folded away
        if os.environ.get("OFF_LONG") == "1":                        # a long conversation, for scrollback
            for k in range(12):
                p.add_user(f"question {k + 1}: how is the lab doing?")
                p.begin_reply(); p.feed(f"Answer {k + 1}. All six nodes are up and every service answers. " * 2); p.end_reply()
        p.scroll = float(os.environ.get("OFF_SCROLL", 0))              # pixels scrolled back
        if os.environ.get("OFF_ATTACH") == "1":                        # a message with files (attach.line)
            p.add_user("what's wrong with this build?")
            p.add_attached("⟐ attached · build-error.png · 240 KB")
            p.add_attached("⟐ attached · cargo-build.log · 18 KB")
            p.begin_reply(); p.feed("The log ends in one error, sir: relay_beam isn't in scope at src/beam.rs line 42. "
                                    "Your screenshot shows the same line. Import it from the relay module and it builds."); p.end_reply()
            for m in p.msgs:
                m.shown = len(m.text)
        if os.environ.get("OFF_ENTRY"):                                # the box's height + margin + gap
            p.entry_clear = float(os.environ["OFF_ENTRY"]) + 34 + 6
        p.fade = 1.0
        for k in range(int(tt * 30)):
            p.advance(1 / 30)
            if p.stage:
                p.stage.read_at = 0
        p.set_state(state)
        p.fade = 1.0
        if p.stage:
            p.ex_amt = 1.0
        p.render(fbo, W, H)
        p.render(fbo, W, H)
        save(fbo, out)

    elif kind == "vector-switch":
        # the voice switch and the mood layer, frame by frame (30 fps): Voss -> Arc -> Voss -> alarmed
        from holo.vector.scene import VectorScene
        p = VectorScene(live)
        p.fade = p.fade_to = 1.0
        p.left_w = 560
        os.makedirs(out, exist_ok=True)
        timeline = {0: ("speaking", "main", None), 30: ("speaking", "scientist", None), 75: ("speaking", "main", None),
                    105: ("speaking", "robot", "alarmed"), 150: ("speaking", "main", None)}
        p.set_voice("main", False)
        for k in range(180):
            if k in timeline:
                st, voice, mood = timeline[k]
                p.set_state(st)
                p.set_voice(voice, True)
                if mood:
                    p.set_mood(mood)
            p.audio_level = 0.45 + 0.35 * abs(np.sin(k * 0.55))
            p.last_t = time.monotonic() - 1 / 30
            p.render(fbo, W, H)
            save(fbo, os.path.join(out, f"f{k:03d}.png"))


if __name__ == "__main__":
    main()
