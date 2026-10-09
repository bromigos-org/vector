#!/usr/bin/env python3
"""Checks for the worlds' holograms in the gallery (holo/worlds.py, holo/fmt.py, holo/gallery.py).

Everything runs in a throwaway HOME, a throwaway bromigOS share ($BROMIGOS_SHARE_DIR: two
first-party worlds, a world with no models, the Drift's shared models) and throwaway
extensions (a theme extension with holograms, one with a broken model, one reusing a
first-party world's name), with small models this test bakes; the theme in force is a
palette.json it writes. It checks:

  - the shelves: the world in force first, then VECTOR's own, the Drift's, the first-party
    worlds in order, the extensions' by name; a world's post (a live-bound model) first;
    a name VECTOR's collection holds stays his; an extension can't take a first-party
    world's name; a model that won't load is left out until its file changes;
  - the colours: a world's colors.toml resolved as `bromigos theme set` resolves it
    (references chained through roles, [colors] and table.key), under the user's overlay;
    a loop or a bad value means no palette (drawn in the theme in force); the world in
    force, VECTOR's and the Drift's draw in palette.json; high contrast draws every model
    in it; the Wick's shades retint onto the world's ramp;
  - the gallery without GL: its order, PGUP/PGDN and the digits, hover text for a world,
    a part bound to nothing drawn lit; render.tinted() inside and outside;
  - the guard: nothing in the share, the extensions or the user's config is written;
  - with EGL (an NVIDIA device, else Mesa; never a window): a world's model drawn in that
    world's primary hue, the world in force in its own.

    python3 tools/test-gallery-worlds.py [--out DIR]     # DIR: the renders; exit 1 on a failure
"""
import colorsys
import ctypes
import hashlib
import json
import os
import shutil
import sys
import tempfile

SANDBOX = tempfile.mkdtemp(prefix="vector-gallery-worlds-")
for k in ("WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS",
          "BROMIGOS_HOLO_MODELS"):
    os.environ.pop(k, None)
for k, sub in (("HOME", ""), ("XDG_CONFIG_HOME", ".config"), ("XDG_STATE_HOME", ".local/state"),
               ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache"), ("XDG_RUNTIME_DIR", "run")):
    os.environ[k] = os.path.join(SANDBOX, sub)
    os.makedirs(os.environ[k], exist_ok=True)
os.chmod(os.environ["XDG_RUNTIME_DIR"], 0o700)
SHARE = os.path.join(SANDBOX, "share")
os.environ["BROMIGOS_SHARE_DIR"] = SHARE
os.environ["PYOPENGL_PLATFORM"] = "egl"
sys.dont_write_bytecode = True
VEC = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, VEC)

import numpy as np  # noqa: E402

failures, lines = [], []


def report(name, ok, detail=""):
    lines.append(f"{'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(name)


# ------------------------------------------------------------------ the throwaway machine

def bake(path, name, title, parts):
    """A small .holo.npz: one box per part, side by side. parts: [(id, label, bind)]."""
    pos, part, tri, edge, meta_parts = [], [], [], [], []
    c = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], np.float32)
    faces = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
             (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    edges = [(0, 1), (1, 3), (3, 2), (2, 0), (4, 5), (5, 7), (7, 6), (6, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
    n = len(parts)
    for i, (pid, label, bind) in enumerate(parts):
        v = c * np.array([0.8 / n, 1.0, 0.4], np.float32) + np.array([-0.4 + i / n * 0.8, 0, -0.2], np.float32)
        base = len(pos) * 8
        pos.append(v)
        part += [i] * 8
        tri += [(a + base, b + base, d + base) for a, b, d in faces]
        edge += [(a + base, b + base) for a, b in edges]
        ctr = v.mean(0).tolist()
        meta_parts.append({"id": pid, "label": label, "bind": bind, "hint": f"the {label.lower()}",
                           "centroid": ctr, "extent": [0.8 / n, 1.0, 0.4], "explode": [0, 0, 0.5],
                           "anchor": ctr, "tris": 12, "edges": 12})
    pos = np.concatenate(pos)
    meta = {"format": "bromigos-holo/1", "name": name, "title": title, "subtitle": f"{title.title()} · a test",
            "height": 1.0, "radius": 0.5, "counts": {"verts": len(pos), "tris": len(tri), "edges": len(edge)},
            "parts": meta_parts}
    np.savez(path, meta=np.frombuffer(json.dumps(meta).encode(), np.uint8), pos=pos,
             nrm=np.zeros_like(pos, np.float16), part=np.array(part, np.uint8),
             tri=np.array(tri, np.uint32), edge=np.array(edge, np.uint32))


def collection(d, models):
    """models: {name: (title, [(id, label, bind)])}; a manifest with each title and parts."""
    os.makedirs(d, exist_ok=True)
    man = {"models": {}}
    for name, (title, parts) in models.items():
        bake(os.path.join(d, name + ".holo.npz"), name, title, parts)
        man["models"][name] = {"title": title, "parts": [{"id": i, "label": l, "bind": b} for i, l, b in parts]}
    with open(os.path.join(d, "manifest.json"), "w") as f:
        json.dump(man, f)


def theme(d, name, colors):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "theme.toml"), "w") as f:
        f.write(f'name = "{name}"\n')
    with open(os.path.join(d, "colors.toml"), "w") as f:
        f.write(colors)


WORLD = """background = "#05090a"
void = "background"
panel = "#0c1513"
selection = "#1c2d27"
primary = "glow_src"
soft = "#d6e8b4"
dim = "#5d7a4b"
text = "#cfdcc4"
muted = "#7a8a78"
faint = "#4a5a4c"
warn = "#e8a040"
danger = "#e0604f"
warn_surface = "#3e2c0e"
danger_surface = "#1c0806"
danger_text = "#ffd9d2"

[colors]
glow_src = "holo.line"
white = "#eef4e0"

[holo]
line = "{primary}"
glow = "primary"
label = "text"
highlight = "white"
"""
MIRE, TIDE, ZEPH = "#b8e05a", "#3fd8e8", "#ff5fa8"
POST = [("relay", "RELAY", "wick.beam"), ("hull", "HULL", "")]
SHAPE = [("dome", "DOME", ""), ("legs", "LEGS", "")]

theme(os.path.join(SHARE, "themes", "mire"), "Mire", WORLD.replace("{primary}", MIRE))
collection(os.path.join(SHARE, "themes", "mire", "holo"),
           {"sleeper": ("THE SLEEPER", SHAPE), "stilt-shack": ("THE STILT SHACK", POST),
            "wick": ("A WORLD'S WICK", SHAPE)})                      # VECTOR's name: his stays
theme(os.path.join(SHARE, "themes", "tidewell"), "Tidewell", WORLD.replace("{primary}", TIDE))
collection(os.path.join(SHARE, "themes", "tidewell", "holo"), {"choir": ("THE CHOIR", SHAPE)})
theme(os.path.join(SHARE, "themes", "wick"), "Wick", WORLD.replace("{primary}", "#39ff14"))
os.makedirs(os.path.join(SHARE, "themes", "wick", "holo"))           # reserved: no models
theme(os.path.join(SHARE, "themes", "loopy"), "Loopy", WORLD.replace("{primary}", "holo.glow"))
collection(os.path.join(SHARE, "themes", "loopy", "holo"), {"knot": ("THE KNOT", SHAPE)})
collection(os.path.join(SHARE, "theme", "holo"), {"long-hauler": ("THE LONG-HAULER", SHAPE),
                                                  "unlisted-relay": ("AN UNLISTED RELAY", POST)})
EXT_SRC = os.path.join(os.environ["XDG_DATA_HOME"], "bromigos", "ext", "src")
ACTIVE = os.path.join(os.environ["XDG_DATA_HOME"], "bromigos", "ext", "active", "themes")
os.makedirs(ACTIVE)
for ext, colors_, models in (("zephyr", WORLD.replace("{primary}", ZEPH), {"kite": ("THE KITE", SHAPE)}),
                             ("mire", WORLD.replace("{primary}", "#ff0000"), {"impostor": ("AN IMPOSTOR", SHAPE)}),
                             ("yonder", WORLD.replace("{primary}", "#88aaff"), {"wreck": ("A WRECK", SHAPE),
                                                                                "beacon": ("A BEACON", SHAPE)})):
    d = os.path.join(EXT_SRC, ext)
    theme(d, ext.title(), colors_)
    collection(os.path.join(d, "holo"), models)
    os.symlink(d, os.path.join(ACTIVE, ext))
BROKEN = os.path.join(EXT_SRC, "yonder", "holo", "wreck.holo.npz")
with open(BROKEN, "wb") as f:
    f.write(b"not a zip")
USER_TIDE = os.path.join(os.environ["XDG_CONFIG_HOME"], "bromigos", "themes", "tidewell")
os.makedirs(USER_TIDE)
with open(os.path.join(USER_TIDE, "colors.toml"), "w") as f:
    f.write('soft = "#abcdef"\n[holo]\nhighlight = "#40e0d0"\n')  # the user's overlay


def palette_json(theme_, primary, contrast="normal"):
    d = os.path.join(os.environ["XDG_STATE_HOME"], "bromigos", "theme", "current")
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, ".palette.json")
    with open(tmp, "w") as f:
        json.dump({"schema": 1, "theme": theme_, "name": theme_.title(), "mode": "dark",
                   "colors": {"primary": primary, "background": "#02070d"},
                   "contrast": {"mode": contrast}, "motion": "full"}, f)
    os.replace(tmp, os.path.join(d, "palette.json"))           # a new inode, as `theme set` does
    if "bromigos_theme" in sys.modules:                         # seen at once, not within a second
        sys.modules["bromigos_theme"].version(every=0)


def snapshot():
    out = {}
    for root in (SHARE, EXT_SRC, os.path.join(os.environ["XDG_CONFIG_HOME"], "bromigos")):
        for dp, _, fs in os.walk(root):
            for f in fs:
                p = os.path.join(dp, f)
                with open(p, "rb") as fh:
                    out[p] = hashlib.sha256(fh.read()).hexdigest()
    return out


palette_json("tidewell", TIDE)
BEFORE = snapshot()


# ------------------------------------------------------------------ checks

class FakeSource:
    version = 0


class FakeLive:
    def __init__(self):
        self.sources = {k: FakeSource() for k in ("local", "lab")}

    def want(self, *a):
        pass

    def get(self, src):
        return None, None, None


def check_shelves():
    from holo import fmt, worlds
    sh = fmt.groups()
    order = [g for g, _ in sh]
    report("shelves: the world in force first, then VECTOR's, the Drift's, the worlds, the extensions'",
           order == ["tidewell", worlds.VECTOR, worlds.DRIFT, "mire", "loopy", "zephyr", "yonder"] or
           order == ["tidewell", worlds.VECTOR, worlds.DRIFT, "mire", "loopy", "yonder", "zephyr"], str(order))
    report("shelves: extensions' worlds by name after the first-party ones",
           order.index("yonder") < order.index("zephyr") and order.index("loopy") < order.index("yonder"), str(order))
    d = dict(sh)
    report("shelves: a world's post (a live-bound model) first", d["mire"] == ["stilt-shack", "sleeper"], str(d["mire"]))
    report("shelves: the Drift's post first", d[worlds.DRIFT] == ["unlisted-relay", "long-hauler"], str(d[worlds.DRIFT]))
    report("shelves: VECTOR's own models stay his (a world's 'wick' is his)",
           "wick" in d[worlds.VECTOR] and "wick" not in d["mire"] and fmt.group_of("wick") == worlds.VECTOR,
           str(d[worlds.VECTOR]))
    report("shelves: a world with no models has no shelf", "wick" not in d, "")
    report("shelves: an extension can't take a first-party world's name",
           "impostor" not in fmt.available() and "mire" not in [g for g in order if g != "mire"], "")
    report("shelves: worlds' titles from theme.toml", worlds.title("mire") == "MIRE" and
           worlds.title(worlds.DRIFT) == "THE DRIFT" and worlds.title("zephyr") == "ZEPHYR", "")
    palette_json("mire", MIRE)
    first = [g for g, _ in fmt.groups()][0]
    report("shelves: a theme switch puts the new world first", first == "mire", first)
    palette_json("wick", "#39ff14")
    first = [g for g, _ in fmt.groups()][0]
    report("shelves: under a theme without models, VECTOR's come first", first == worlds.VECTOR, first)
    palette_json("tidewell", TIDE)


def check_colours():
    from holo import worlds
    worlds.RECHECK = 0.0
    p = worlds.palette("mire")
    report("colours: references chained (primary -> [colors] -> holo.line -> #hex)",
           p is not None and p.hex("primary") == MIRE and p.hex("phosphor") == MIRE and p.hex("holo.glow") == MIRE,
           repr(p))
    report("colours: the Wick's old names answer (void, guard, amber, static)",
           p.hex("void") == "#05090a" and p.hex("guard") == "#1c2d27" and p.hex("amber") == "#e8a040"
           and p.hex("static") == "#7a8a78", "")
    t = worlds.palette("tidewell")
    report("colours: the user's overlay merges over the world's, table by table",
           t.hex("soft") == "#abcdef" and t.hex("holo.highlight") == "#40e0d0" and t.hex("holo.line") == TIDE
           and t.hex("primary") == TIDE, f"soft {t.hex('soft')}, holo.highlight {t.hex('holo.highlight')}, "
           f"holo.line {t.hex('holo.line')}")
    report("colours: an extension's world has its own", worlds.palette("zephyr").hex("primary") == ZEPH, "")
    report("colours: a loop is no palette (drawn in the theme in force)", worlds.palette("loopy") is None, "")
    report("colours: no such theme is no palette", worlds.palette("nowhere") is None, "")
    bg = p.retint_rgb((0.0, 0.02, 0.0, 0.9))
    report("colours: the Wick's backing retints to the world's background",
           worlds._fmt(bg[:3]) == "#05090a" and bg[3] == 0.9, worlds._fmt(bg[:3]))
    mid = worlds._fmt(p.retint_rgb((0.0, 0.03, 0.0))[:3])
    report("colours: a dark Wick shade lands on the world's background -> panel ramp",
           mid not in ("#000800", "#05090a", "#0c1513"), mid)
    report("colours: the world in force, VECTOR's and the Drift's draw in palette.json",
           worlds.palette_for("tidewell") is None and worlds.palette_for(worlds.VECTOR) is None
           and worlds.palette_for(worlds.DRIFT) is None and worlds.palette_for("mire") is not None, "")
    palette_json("tidewell", TIDE, contrast="high")
    hc = [worlds.palette_for(g) for g in ("mire", "zephyr", "tidewell")]
    report("colours: high contrast draws every model in the palette in force", hc == [None, None, None], str(hc))
    palette_json("tidewell", TIDE)
    from holo import render
    from holo.render import col, tinted
    out_ = col("phosphor")[:3]
    with tinted(p):
        inside = col("phosphor")[:3]
    after = col("phosphor")[:3]
    report("tinted(): col() answers in the world's colours inside, the theme in force outside",
           worlds._fmt(inside) == MIRE and worlds._fmt(out_) == TIDE and after == out_ and render._world is None,
           f"inside {worlds._fmt(inside)}, outside {worlds._fmt(out_)}")


def check_gallery():
    from holo import bind, fmt, worlds
    from holo.gallery import Gallery
    report("a part bound to nothing is drawn lit, with no readout", bind.reading("", FakeLive()) == bind.SHAPE
           and bind.SHAPE[0] == "ok" and bind.SHAPE[2] == [], str(bind.reading("", FakeLive())))
    g = Gallery(FakeLive())
    report("gallery: the world in force's model is on the table first", g.names[0] == "choir", g.names[0])
    report("gallery: drawn in the theme in force for its world", g.palette() is None, "")
    g.on_key("Next")
    report("gallery: PGDN goes to the next shelf's first model", g.names[g.idx] == "workstation", g.names[g.idx])
    g.on_key("Next")
    g.on_key("Next")
    report("gallery: a world's model in that world's colours", g.names[g.idx] == "stilt-shack"
           and g.palette() is not None and g.palette().hex("primary") == MIRE, f"{g.names[g.idx]} {g.palette()}")
    g.on_key("2")
    report("gallery: a digit picks a model on this shelf", g.names[g.idx] == "sleeper", g.names[g.idx])
    g.on_key("Prior")
    report("gallery: PGUP goes back a shelf", g.names[g.idx] == "unlisted-relay", g.names[g.idx])
    g.on_key("Left")
    report("gallery: ←/→ still step model by model across shelves", g.names[g.idx] == "emblem", g.names[g.idx])
    hint = g._strip_hint("world", "mire")
    report("gallery: hover text says what a world is and what a click does",
           hint.startswith("MIRE: 2 holograms, in its own colours") and "CLICK" in hint, hint)
    hint = g._strip_hint("model", "sleeper")
    report("gallery: hover text for a model", hint.startswith("THE SLEEPER") and "CLICK" in hint, hint)
    s = g.stage
    s.toggle_explode()
    s.isolate(0)
    report("gallery: explode and isolate still work on a world's model",
           s.explode_to == 1.0 and s.iso == 0, f"explode_to {s.explode_to}, iso {s.iso}")
    # a model that won't load: taken off its shelf, left out until its file changes
    g.show_world(group="yonder")
    first = g.names[g.idx]
    g.show("wreck")
    names = dict(g.shelves).get("yonder", [])
    report("gallery: a broken model is taken off its shelf, and the table moves on",
           first == "beacon" and g.names[g.idx] not in ("wreck", "beacon") and names == ["beacon"]
           and "wreck" not in fmt.available(), f"then {g.names[g.idx]}, shelf {names}")
    bake(BROKEN, "wreck", "A WRECK", SHAPE)
    os.utime(BROKEN, (1e9, 2e9))
    report("gallery: ... and back once its file changes", "wreck" in fmt.available(), "")
    with open(BROKEN, "wb") as f:                     # as the extension shipped it (the guard check below)
        f.write(b"not a zip")
    _ = worlds


def check_guard():
    after = snapshot()
    changed = sorted(p for p in set(after) | set(BEFORE) if after.get(p) != BEFORE.get(p) and p != BROKEN)
    report("guard: nothing in the share, the extensions or the user's config was written",
           not changed, ", ".join(changed[:4]))
    src = "".join(open(os.path.join(VEC, "holo", f)).read() for f in ("worlds.py", "fmt.py", "gallery.py"))
    report("guard: the gallery's discovery opens nothing for writing",
           not any(x in src for x in ('"w")', "'w')", '"wb")', "'wb')", '"a")', "os.replace", "os.rename",
                                      "shutil.", "os.remove", "os.unlink")), "")


def egl():
    from OpenGL import EGL
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
    return bool(EGL.eglMakeCurrent(dpy, EGL.EGL_NO_SURFACE, EGL.EGL_NO_SURFACE, ctx))


def hue_of(img):
    """The mean hue (degrees) of the picture's bright, saturated pixels: the model and table."""
    c = img.reshape(-1, 3) / 255.0
    mx, mn = c.max(1), c.min(1)
    sel = (mx > 0.5) & ((mx - mn) / np.maximum(mx, 1e-6) > 0.35)
    if sel.sum() < 200:
        return None
    hs = np.array([colorsys.rgb_to_hsv(*px)[0] for px in c[sel][:: max(1, sel.sum() // 4000)]])
    a = np.angle(np.exp(2j * np.pi * hs).mean())
    return (np.degrees(a) + 360) % 360


def check_render(out):
    try:
        if not egl():
            raise RuntimeError("no EGL context")
        from OpenGL import GL
    except Exception as e:
        report("render: skipped (no EGL here)", True, str(e)[:80])
        return
    from holo.gallery import Gallery
    w, h = 880, 520
    fbo = GL.glGenFramebuffers(1)
    GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
    rb = GL.glGenRenderbuffers(1)
    GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, rb)
    GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_RGBA8, w, h)
    GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_RENDERBUFFER, rb)
    g = Gallery(FakeLive(), scale=0.5)

    def shot(model):
        g.show(model)
        s = g.stage
        s.born -= 5
        s.scan_t0 -= 10
        s.fade = 1.0
        for _ in range(2):
            g.render(fbo, w, h)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
        px = GL.glReadPixels(0, 0, w, h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
        img = np.frombuffer(px, np.uint8).reshape(h, w, 4)[::-1, :, :3].astype(np.float32)
        if out:
            from PIL import Image
            Image.fromarray(img.astype(np.uint8)).save(os.path.join(out, f"gallery-{model}.png"))
        return hue_of(img)

    def near(a, b, tol=25):
        return a is not None and abs((a - b + 180) % 360 - 180) <= tol

    want = {m: colorsys.rgb_to_hsv(*[int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)])[0] * 360
            for m, c in (("sleeper", MIRE), ("choir", TIDE), ("kite", ZEPH))}
    for model, world in (("sleeper", "Mire, a world not in force"), ("choir", "Tidewell, the world in force"),
                         ("kite", "an extension's world")):
        got = shot(model)
        report(f"render: {model} drawn in {world}'s hue", near(got, want[model]),
               f"hue {got if got is None else round(got)}°, its primary {round(want[model])}°")


def main():
    out = None
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
        os.makedirs(out, exist_ok=True)
    try:
        check_shelves()
        check_colours()
        check_gallery()
        check_guard()
        check_render(out)
    finally:
        shutil.rmtree(SANDBOX, ignore_errors=True)
    print("\n".join(lines))
    print(f"\n{len(lines) - len(failures)}/{len(lines)} checks pass" + (f"; failed: {', '.join(failures)}" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
