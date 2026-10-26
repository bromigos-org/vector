#!/usr/bin/env python3
"""Offscreen renders, timings and checks of the holo projector (holo/projector.py) through
the portrait stream's version 2 (holo/portrait.py --shm --projector), the way an app reads
it: a process of its own, frames in shared memory, a line each on stdout.

Runs headless (EGL: an NVIDIA device, else Mesa; never a window, never the running desktop)
in a throwaway HOME and runtime dir, with sample projections whose people, machines and
numbers are invented.

    python3 tools/projector-render.py OUTDIR              # a PNG of each projection
    python3 tools/projector-render.py OUTDIR --frames 48  # and 48 frames of each (an MP4 with ffmpeg)
    python3 tools/projector-render.py OUTDIR --bench      # GPU and CPU time per projection
    python3 tools/projector-render.py --check             # the protocol checks; exit 1 on a failure

--size W H and --scale S set the projector (default 426 x 220 at 2), --only KIND,... picks.
The checks: the ready line and layout; whole frames by the seqlock; a projection appears
(lit pixels) and its regions arrive; a new key rebuilds and the same key doesn't; pausing
stops the frames; idle runs near 10 frames a second, a flickering alert near 15 and a
build-up near --fps; the end of input ends it with exit 0; bad arguments exit 2.
"""
import argparse
import json
import math
import os
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
PORTRAIT = os.path.join(HERE, "holo", "portrait.py")
SIZE = 132


def env():
    sandbox = tempfile.mkdtemp(prefix="vector-projector-")
    e = {k: v for k, v in os.environ.items()
         if k not in ("WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS")}
    for k, sub in (("HOME", ""), ("XDG_CONFIG_HOME", ".config"), ("XDG_STATE_HOME", ".local/state"),
                   ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache"), ("XDG_RUNTIME_DIR", "run")):
        e[k] = os.path.join(sandbox, sub)
        os.makedirs(e[k], exist_ok=True)
    os.chmod(e["XDG_RUNTIME_DIR"], 0o700)
    # the theme in force, if there is one: the renders look like the desktop's colours
    real = os.path.expanduser("~/.local/state/bromigos/theme/current/palette.json")
    if os.path.exists(real) and os.environ.get("PROJECTOR_THEME", "1") != "0":
        dst = os.path.join(e["XDG_STATE_HOME"], "bromigos", "theme", "current")
        os.makedirs(dst, exist_ok=True)
        shutil.copy(real, dst)
    return sandbox, e


def sample_picture(path):
    """An invented picture: a dusk ridge, drawn here."""
    from PIL import Image, ImageDraw
    w, h = 640, 400
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        k = y / h
        d.line([(0, y), (w, y)], fill=(int(30 + 120 * k), int(40 + 60 * k), int(90 + 40 * (1 - k))))
    pts = [(0, 300)] + [(x, 260 - 60 * math.sin(x / 90) - 30 * math.sin(x / 33)) for x in range(0, w + 1, 16)] + [(w, h), (0, h)]
    d.polygon(pts, fill=(12, 16, 22))
    d.ellipse((470, 80, 530, 140), fill=(250, 230, 190))
    im.save(path)


def samples(pic):
    now = time.time() * 1000
    day = 86_400_000
    pts = []
    for hr in range(0, 7 * 24 + 1):
        t = now - 7 * day + hr * 3_600_000
        v = 61 + 0.6 * (hr / 24)
        if t > now - 2 * day:
            v += 12.0 * (t - (now - 2 * day)) / (2 * day) * 1.05
        pts.append([t, round(v + 0.4 * math.sin(hr / 3), 2)])
    machines = [
        {"id": "host-1", "name": "host-1", "kind": "host", "up": True, "cpu": 38, "level": "ok"},
        {"id": "node-1", "name": "node-1", "kind": "node", "up": True, "cpu": 44, "level": "ok"},
        {"id": "node-2", "name": "node-2", "kind": "node", "up": True, "cpu": 27, "level": "ok"},
        {"id": "node-3", "name": "node-3", "kind": "node", "up": True, "cpu": 41, "level": "crit"},
        {"id": "node-4", "name": "node-4", "kind": "node", "up": True, "cpu": 19, "level": "ok"},
        {"id": "gpu-1", "name": "gpu-1", "kind": "gpu", "up": True, "cpu": 71, "level": "warn"},
        {"id": "nas-1", "name": "nas-1", "kind": "nas", "up": True, "cpu": 12, "level": "ok"},
        {"id": "pi-1", "name": "pi-1", "kind": "pi", "up": True, "cpu": 9, "level": "ok"},
        {"id": "pi-2", "name": "pi-2", "kind": "pi", "up": False, "cpu": 0, "level": "off"},
    ]
    groups = [{"name": "apps", "states": ["ok"] * 34}, {"name": "ai", "states": ["ok"] * 12},
              {"name": "media", "states": ["ok"] * 5 + ["warn"] + ["ok"] * 8},
              {"name": "monitoring", "states": ["ok"] * 14}, {"name": "storage", "states": ["ok"] * 3 + ["crit"] + ["ok"] * 5}]
    lab = {"kind": "lab", "key": "lab", "machines": machines, "groups": groups,
           "callouts": [{"machine": "node-3", "title": "node-3", "detail": "disk 94% · critical", "level": "crit"},
                        {"machine": "gpu-1", "title": "gpu-1", "detail": "83°C · warm", "level": "warn"}]}
    return {
        "world": {"kind": "world", "key": "world"},
        "contact": {"kind": "contact", "key": "msg-1", "who": "Sam", "text": "game tonight?", "meta": "09:22 · 2 NEW"},
        "call": {"kind": "contact", "key": "call-1", "who": "Alex", "call": True, "text": "ringing · 0:07",
                 "meta": "answer on your phone"},
        "lab": lab,
        "focus": dict(lab, key="lab-focus", focus="node-3", callouts=lab["callouts"][:1]),
        "nolab": {"kind": "nolab", "key": "nolab"},
        "chart": {"kind": "chart", "key": "chart-1", "title": "NODE-3 · DISK · 7 DAYS", "subtitle": "+6%/DAY OVER 2 DAYS",
                  "unit": "%", "min": 0, "max": 100, "points": pts, "threshold": {"value": 90, "label": "90%"},
                  "forecast": {"at": now + 2.5 * day, "value": 100, "label": "FULL IN 3 DAYS"}, "level": "crit",
                  "gauge": True},
        "card": {"kind": "card", "key": "pic-1", "image": pic, "eyebrow": "Alex sent a photo", "title": "ridge.png",
                 "caption": "the ridge at dusk", "meta": "48 KB · 640 × 400"},
        "orbit": {"kind": "orbit", "key": "line", "people": [
            {"name": "Sam", "ago": "now", "bright": 1.0}, {"name": "Alex", "ago": "4 min", "bright": 0.8},
            {"name": "Rook", "ago": "12 min", "bright": 0.6}, {"name": "Mom", "ago": "2 h", "bright": 0.45}]},
        "media": {"kind": "media", "key": "air", "eyebrow": "On the big screen", "title": "Night Signal",
                  "subtitle": "S1 · E3", "meta": "12:40 / 24:00 · vol 60%", "progress": 0.53},
        "weather": {"kind": "weather", "key": "wx", "eyebrow": "Outside · Harbor Town", "temp": "78°F",
                    "words": "clear · wind W 12 mph", "meta": "low 48°F · humidity 18%", "sun": 0.62},
        "list": {"kind": "list", "key": "jobs", "title": "Machine jobs", "rows": [
            {"label": "render: den flythrough", "detail": "gpu-1 · 41%", "progress": 0.41, "level": "ok"},
            {"label": "backup: photos", "detail": "nas-1", "progress": None, "level": "ok"},
            {"label": "index: media library", "detail": "node-3 · stalled", "progress": 0.88, "level": "warn"}]},
    }


class Stream:
    """holo/portrait.py --shm --projector, read the way an app reads it."""

    def __init__(self, e, sandbox, w, h, scale, fps=30, stats=False, extra=()):
        self.shm = os.path.join(e["XDG_RUNTIME_DIR"], "holo.shm")
        args = [sys.executable, PORTRAIT, "--size", str(SIZE), "--fps", str(fps), "--shm", self.shm,
                "--projector", str(w), str(h), "--scale", str(scale), *extra]
        if stats:
            args.append("--stats")
        self.proc = subprocess.Popen(args, env=e, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        self.buf = b""
        self.ready = self.line(90)
        self.mm = None
        self.regions, self.meta, self.stats = None, None, []
        if self.ready and self.ready.startswith("VECTOR-PORTRAIT 2"):
            _, _, size, pw, ph = self.ready.split()
            self.size, self.pw, self.ph = int(size), int(pw), int(ph)
            sys.path.insert(0, HERE)
            from holo.portrait import layout
            self.offsets, total = layout(self.size, self.pw, self.ph)
            import mmap
            fd = os.open(self.shm, os.O_RDONLY)
            self.mm = mmap.mmap(fd, total, mmap.MAP_SHARED, mmap.PROT_READ)
            os.close(fd)

    def line(self, seconds=10.0):
        end = time.monotonic() + seconds
        while b"\n" not in self.buf:
            left = end - time.monotonic()
            if left <= 0 or not select.select([self.proc.stdout], [], [], left)[0]:
                return None
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                return None
            self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return line.decode()

    def send(self, line):
        self.proc.stdin.write((line + "\n").encode())
        self.proc.stdin.flush()

    def project(self, spec):
        self.send("project " + json.dumps(spec))

    def read_slot(self, which, slot):
        import numpy as np
        k = int(slot) + (2 if which == "projector" else 0)
        off = self.offsets[k]
        w, h = (self.size, self.size) if which == "portrait" else (self.pw, self.ph)
        s1 = struct.unpack_from("<Q", self.mm, off)[0]
        px = np.frombuffer(self.mm, np.uint8, w * h * 4, off + 64).reshape(h, w, 4).copy()
        s2 = struct.unpack_from("<Q", self.mm, off)[0]
        return px if s1 == s2 and s1 % 2 == 0 else None

    def frames(self, which="projector", n=1, seconds=20.0):
        """The newest of the next n whole frames of `which` (and what else came)."""
        got, end = None, time.monotonic() + seconds
        while n > 0 and time.monotonic() < end:
            line = self.line(max(0.1, end - time.monotonic()))
            if line is None:
                break
            word, _, rest = line.partition(" ")
            if word == "frame":
                w, slot, _seq = rest.split()
                if w == which:
                    px = self.read_slot(w, slot)
                    if px is not None:
                        got = px
                        n -= 1
            elif word == "regions":
                self.regions = json.loads(rest)
            elif word == "meta":
                self.meta = json.loads(rest)
            elif word == "stats":
                self.stats.append(json.loads(rest))
        return got

    def count(self, which, seconds):
        n, end = 0, time.monotonic() + seconds
        while time.monotonic() < end:
            line = self.line(max(0.05, end - time.monotonic()))
            if line and line.startswith("frame " + which):
                n += 1
            elif line and line.startswith("stats "):
                self.stats.append(json.loads(line[6:]))
        return n

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            while self.proc.stdout.read(65536):
                pass
            code = self.proc.wait(15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = None
        if self.mm:
            self.mm.close()
        return code


def over_panel(px, bg=(8, 14, 22)):
    """Premultiplied RGBA over the panel's dark colour, as an RGB image."""
    import numpy as np
    from PIL import Image
    a = px[..., 3:4].astype(np.float32) / 255.0
    rgb = px[..., :3].astype(np.float32) + np.array(bg, np.float32) * (1 - a)
    return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")


def lit(px):
    return int((px[..., 3] > 8).sum())


def render(out, kinds, w, h, scale, frames):
    sandbox, e = env()
    os.makedirs(out, exist_ok=True)
    pic = os.path.join(sandbox, "ridge.png")
    sample_picture(pic)
    specs = samples(pic)
    s = Stream(e, sandbox, w, h, scale)
    try:
        if not s.mm:
            print("no frames:", s.proc.stderr.read().decode(errors="replace").strip() or s.ready)
            return 1
        for k in kinds:
            s.project(specs[k])
            if frames > 1:
                seq_dir = os.path.join(out, f"{k}-frames")
                os.makedirs(seq_dir, exist_ok=True)
                for i in range(frames):
                    px = s.frames("projector", 1)
                    over_panel(px).save(os.path.join(seq_dir, f"{i:04d}.png"))
                if shutil.which("ffmpeg"):
                    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", "30", "-i",
                                    os.path.join(seq_dir, "%04d.png"), "-pix_fmt", "yuv420p", "-vf",
                                    "pad=ceil(iw/2)*2:ceil(ih/2)*2", os.path.join(out, f"{k}.mp4")], check=False)
            else:
                s.frames("projector", 75)          # let it build up (2.5 s at 30 fps)
            px = s.frames("projector", 1)
            if px is None:
                print(f"{k}: no frame:", s.proc.stderr.read().decode(errors="replace").strip()[-400:])
                return 1
            over_panel(px).save(os.path.join(out, f"{k}.png"))
            with open(os.path.join(out, f"{k}.regions.json"), "w") as f:
                json.dump({"regions": s.regions, "meta": s.meta}, f)
            print(os.path.join(out, f"{k}.png"), f"{lit(px)} lit, {len(s.regions or [])} regions")
    finally:
        s.close()
        shutil.rmtree(sandbox, ignore_errors=True)
    return 0


def bench(out, kinds, w, h, scale):
    sandbox, e = env()
    pic = os.path.join(sandbox, "ridge.png")
    sample_picture(pic)
    specs = samples(pic)
    s = Stream(e, sandbox, w, h, scale, stats=True)
    rows = {}
    try:
        if not s.mm:
            print("no frames:", s.proc.stderr.read().decode(errors="replace").strip())
            return 1
        for k in kinds:
            s.project(specs[k])
            s.stats.clear()
            s.count("projector", 11.0)                 # two 5 s windows: building, then settled
            if s.stats:
                last = s.stats[-1]
                rows[k] = {"gpu_ms": last["kinds"].get(specs[k]["kind"]), "fps": last["projector"]["fps"],
                           "cpu_pct": last["cpu_pct"], "portrait_gpu_ms": last["portrait"]["gpu_ms"]}
                print(f"{k:8s} gpu {rows[k]['gpu_ms']} ms/frame at {rows[k]['fps']} fps, "
                      f"stream cpu {rows[k]['cpu_pct']}%")
        s.send("pause 1")
        s.count("projector", 1.0)
        paused = s.count("projector", 3.0)
        rows["paused_frames_3s"] = paused
        print(f"paused: {paused} frames in 3 s")
    finally:
        s.close()
        shutil.rmtree(sandbox, ignore_errors=True)
    if out:
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "bench.json"), "w") as f:
            json.dump(rows, f, indent=1)
    return 0


def check(w, h, scale):
    lines, failures = [], []

    def ok(name, cond, detail=""):
        lines.append(f"{'ok  ' if cond else 'FAIL'} {name}" + (f" ({detail})" if detail and not cond else ""))
        if not cond:
            failures.append(name)

    sandbox, e = env()
    try:
        bad = subprocess.run([sys.executable, PORTRAIT, "--size", "64", "--projector", "100", "100"],
                             env=e, capture_output=True, timeout=60)
        ok("--projector without --shm is refused", bad.returncode == 2, f"exit {bad.returncode}")
        s = Stream(e, sandbox, w, h, scale)
        pw, ph = round(w * scale), round(h * scale)
        ok("it says it's ready first", s.ready == f"VECTOR-PORTRAIT 2 {SIZE} {pw} {ph}", repr(s.ready))
        if not s.mm:
            print("no frames:", s.proc.stderr.read().decode(errors="replace").strip())
            return 1
        ok("the file starts with its magic", s.mm[:8] == b"VHOLOSHM")
        ok("the file is owner-only", os.stat(s.shm).st_mode & 0o777 == 0o600)
        portrait = s.frames("portrait", 3)
        ok("his portrait comes through", portrait is not None and lit(portrait) > SIZE * SIZE // 20)
        idle = s.frames("projector", 40)
        ok("the world's model idles on the projector", idle is not None and lit(idle) > pw * ph // 50,
           f"{lit(idle) if idle is not None else 0} lit")
        specs = samples(None)
        s.project(specs["lab"])
        lab = s.frames("projector", 75)
        ok("a projection replaces it", lab is not None and lit(lab) > pw * ph // 50)
        ids = {r["id"] for r in (s.regions or [])}
        ok("its regions arrive", "machine:node-3" in ids and "machine:gpu-1" in ids, str(sorted(ids)))
        r = next((r for r in s.regions or [] if r["id"] == "machine:node-3"), None)
        ok("regions are inside the projector", r is not None and 0 <= r["x"] <= w and 0 <= r["y"] <= h)
        fast = s.count("projector", 2.0)
        ok("a critical machine flickers at 15 frames a second", 24 <= fast <= 36, f"{fast} frames in 2 s")
        s.project(specs["weather"])
        s.frames("projector", 90)
        idle_n = s.count("projector", 3.0)
        ok("settled, it draws near 10 frames a second", 20 <= idle_n <= 40, f"{idle_n} frames in 3 s")
        s.project(dict(specs["weather"], temp="79°F"))
        same = s.count("projector", 1.0)
        ok("the same key updates in place (no build-up)", same <= 16, f"{same} frames in 1 s")
        s.project(dict(specs["weather"], key="wx-2"))
        again = s.count("projector", 1.0)
        ok("a new key builds up at the full rate", again >= 20, f"{again} frames in 1 s")
        s.send("pause 1")
        s.count("projector", 0.5)
        ok("paused, nothing comes", s.count("projector", 1.5) == 0)
        s.send("pause 0")
        ok("unpaused, it goes on", s.frames("projector", 2) is not None)
        s.project({"kind": "teleporter"})
        ok("an unknown kind is ignored, the stream goes on", s.frames("projector", 3) is not None)
        code = s.close()
        ok("at the end of input it stops, exit 0", code == 0, f"exit {code}")
        ok("the shared memory stays the caller's to remove", os.path.exists(s.shm))
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    print("\n".join(lines))
    print(f"\n{len(lines) - len(failures)}/{len(lines)} checks pass" + (f"; failed: {', '.join(failures)}" if failures else ""))
    return 1 if failures else 0


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("out", nargs="?")
    p.add_argument("--size", type=int, nargs=2, default=(426, 220), metavar=("W", "H"))
    p.add_argument("--scale", type=float, default=2.0)
    p.add_argument("--only", default="")
    p.add_argument("--frames", type=int, default=1)
    p.add_argument("--bench", action="store_true")
    p.add_argument("--check", action="store_true")
    a = p.parse_args()
    kinds = [k for k in a.only.split(",") if k] or list(samples(None))
    w, h = a.size
    if a.check:
        return check(w, h, a.scale)
    if not a.out:
        p.error("OUTDIR is needed")
    if a.bench:
        return bench(a.out, kinds, w, h, a.scale)
    return render(a.out, kinds, w, h, a.scale, a.frames)


if __name__ == "__main__":
    sys.exit(main())
