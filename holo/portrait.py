#!/usr/bin/env python3
"""VECTOR's portrait as a stream of frames, so another app can show him live without running
his renderer inside it (BRODEC's desktop panel does), and the same app's holo projector
(holo/projector.py), drawn in the same process and GL context.

  python3 holo/portrait.py --size N [--fps F]
  python3 holo/portrait.py --size N [--fps F] --shm PATH [--projector W H] [--scale S] [--stats]

It draws him the way his console's portrait does (holo.render.Holo and
holo.vector.avatar.Avatar, OpenGL 3.3 core) on a surfaceless EGL context of its own, so it
needs no window and no display: framed close on his aperture and core, over his table.

Version 1 (no --shm): frames on stdout.

stdout  one line, "VECTOR-PORTRAIT 1 N", once he's ready; then frames of N*N*4 bytes each,
        premultiplied RGBA, top row first, until it stops.

Version 2 (--shm PATH): frames in shared memory, short lines on stdout. It creates PATH
(0600; put it in $XDG_RUNTIME_DIR) and sizes it; the caller maps it read-only once it reads
the ready line. With --projector, it also draws the projector: a logical W x H frame drawn
at S times (the caller's scale factor), so PW = round(W*S), PH = round(H*S) pixels.

  layout  a 4096-byte header (b"VHOLOSHM", then the ready line's numbers), then four slots:
          portrait 0, portrait 1, projector 0, projector 1 (the projector's two have size 0
          without --projector). A slot is a 64-byte header (its little-endian u64 sequence
          number first) and then its pixels, w*h*4 bytes, premultiplied RGBA, top row first.
          Each slot starts on a 4096-byte boundary: slot k's offset is 4096 plus the sizes of
          the slots before it, each rounded up to 4096.
  writes  a seqlock: the sequence is odd while a slot is written and even once it's whole.
          A reader copies the pixels and checks the sequence is the same even number before
          and after; if not, it drops that frame.
stdout  "VECTOR-PORTRAIT 2 N PW PH" once both are ready (PW PH are 0 0 without a projector),
        then one line per frame and a few others:
          frame portrait SLOT SEQ      a whole portrait frame is in SLOT
          frame projector SLOT SEQ     a whole projector frame is in SLOT
          regions [{"id", "x", "y", "w", "h"}, ...]
                                       what's where on the projector, in logical pixels, for
                                       hover hints and clicks (when it changes, at most 5 a
                                       second)
          meta {...}                   what the projection shows that the caller may name
                                       (the world's model: {"model", "title"})
          stats {...}                  with --stats, every 5 s: frames, GPU and CPU time
stdin   one command per line:
          state idle|listening|thinking|speaking
          level 0.42      the microphone's loudness while he listens, 0..1
          voice 0.42      his own voice's loudness while he speaks, 0..1 (the clip
                          playing); fresh ones drive him, else a talking cadence
          pause 1|0       hidden: draw nothing until unpaused
          project {...}   what the projector shows, a JSON object (holo/projector.py)
        At the end of its input it stops.

He follows his state as in the console: listening moves with `level`, speaking moves with
`voice` while it keeps coming (his real voice) and pulses with a talking cadence without it, and the motion setting (holo.motion) calms or stills him and the
projector. Version 2 draws his portrait at --fps while he isn't idle, the projector at --fps
while a projection builds up and at 15 while it moves on its own (ringing, flickering,
turning), and each at 10 frames a second otherwise; still motion draws a frame a second. When it can't draw (no GPU, a driver refusing GL 3.3), it says why
in one line on stderr and exits 1.
"""
import argparse
import ctypes
import json
import math
import mmap
import os
import struct
import sys
import threading
import time

os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

STATES = ("idle", "listening", "thinking", "speaking")


class Commands:
    """What the reader of stdin last heard: his state, the loudness, paused, stopped, and the
    projection asked for (with a counter, so the drawing loop sees each new one)."""

    def __init__(self):
        self.state = "idle"
        self.level = 0.0
        self.voice = 0.0
        self.voice_at = -1.0
        self.paused = False
        self.projection = None
        self.projected = 0
        self.stop = threading.Event()
        self.wake = threading.Event()

    def take(self, line):
        word, _, value = line.strip().partition(" ")
        if word == "state" and value in STATES:
            self.state = value
        elif word == "level":
            try:
                self.level = max(0.0, min(1.0, float(value)))
            except ValueError:
                return
        elif word == "voice":
            try:
                self.voice = max(0.0, min(1.0, float(value)))
            except ValueError:
                return
            self.voice_at = time.monotonic()
        elif word == "pause":
            self.paused = value == "1"
        elif word == "project":
            try:
                spec = json.loads(value)
            except ValueError:
                return
            if not isinstance(spec, dict):
                return
            self.projection = spec
            self.projected += 1
        else:
            return
        self.wake.set()

    def read(self, stream):
        for line in stream:
            self.take(line)
        self.stop.set()
        self.wake.set()


VOICE_FRESH_S = 0.3


def speaking_level(cmds, phase, now=None):
    """How loud he is while speaking: his voice as the player reports it (`voice`) while it
    keeps coming, else a talking cadence (syllables over phrases)."""
    now = time.monotonic() if now is None else now
    if now - cmds.voice_at < VOICE_FRESH_S:
        return 0.15 + 0.85 * cmds.voice
    return 0.35 + 0.35 * abs(math.sin(phase * 9.0)) * (0.6 + 0.4 * math.sin(phase * 1.7))


def voice_now(cmds, now=None):
    """His voice's loudness while fresh, else None (the projector's speakers fall back to
    a cadence of their own)."""
    now = time.monotonic() if now is None else now
    return cmds.voice if now - cmds.voice_at < VOICE_FRESH_S else None


def egl_context():
    """A surfaceless desktop GL 3.3 core context on this thread: a GPU's EGL device when there
    is one (NVIDIA first), else the default display (Mesa, software when it must)."""
    from OpenGL import EGL
    attrs = [EGL.EGL_CONTEXT_MAJOR_VERSION, 3, EGL.EGL_CONTEXT_MINOR_VERSION, 3,
             EGL.EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL.EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT, EGL.EGL_NONE]
    n = EGL.EGLint()
    candidates = []
    try:
        from OpenGL.EGL.EXT.device_base import eglQueryDevicesEXT
        from OpenGL.EGL.EXT.platform_base import eglGetPlatformDisplayEXT
        from OpenGL.EGL.EXT.platform_device import EGL_PLATFORM_DEVICE_EXT
        devs = (EGL.EGLDeviceEXT * 8)()
        eglQueryDevicesEXT(8, devs, ctypes.byref(n))
        for i in range(n.value):
            d = eglGetPlatformDisplayEXT(EGL_PLATFORM_DEVICE_EXT, devs[i], None)
            if d and EGL.eglInitialize(d, None, None):
                vendor = EGL.eglQueryString(d, EGL.EGL_VENDOR) or b""
                candidates.append((0 if b"NVIDIA" in vendor else 1, i, d))
    except Exception:
        pass
    candidates.sort(key=lambda c: (c[0], c[1]))
    displays = [c[2] for c in candidates]
    if not displays:
        d = EGL.eglGetDisplay(EGL.EGL_DEFAULT_DISPLAY)
        EGL.eglInitialize(d, None, None)
        displays = [d]
    for display in displays:
        EGL.eglBindAPI(EGL.EGL_OPENGL_API)
        cfg = EGL.EGLConfig()
        EGL.eglChooseConfig(display, [EGL.EGL_SURFACE_TYPE, EGL.EGL_PBUFFER_BIT,
                                      EGL.EGL_RENDERABLE_TYPE, EGL.EGL_OPENGL_BIT, EGL.EGL_NONE],
                            ctypes.byref(cfg), 1, ctypes.byref(n))
        if n.value < 1:
            continue
        ctx = EGL.eglCreateContext(display, cfg, EGL.EGL_NO_CONTEXT, attrs)
        if ctx and EGL.eglMakeCurrent(display, EGL.EGL_NO_SURFACE, EGL.EGL_NO_SURFACE, ctx):
            return display, ctx
    raise RuntimeError("no EGL display gives a GL 3.3 core context")


def target(GL, size):
    fbo = GL.glGenFramebuffers(1)
    GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
    rb = GL.glGenRenderbuffers(1)
    GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, rb)
    GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_RGBA8, size, size)
    GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_RENDERBUFFER, rb)
    return fbo, rb


def stream(size, fps, cmds, out):
    """Draw him into `out` until the commands stop or nobody reads."""
    import numpy as np
    from OpenGL import EGL, GL

    from holo import gl as hgl
    from holo import motion
    from holo.render import Holo
    from holo.vector.avatar import Avatar

    display, context = egl_context()
    try:
        fbo, rb = target(GL, size)
        holo = Holo(1.0)
        avatar = Avatar()
        out.write(f"VECTOR-PORTRAIT 1 {size}\n".encode())
        out.flush()
        last = time.monotonic()
        t0 = last
        speak_phase = 0.0
        while not cmds.stop.is_set():
            if cmds.paused:
                cmds.wake.wait(0.5)
                cmds.wake.clear()
                last = time.monotonic()
                continue
            now = time.monotonic()
            dt = min(0.1, now - last)
            last = now
            t = now - t0
            mode = motion.current() if hasattr(motion, "current") else "full"
            if avatar.state != cmds.state:
                avatar.set_state(cmds.state)
            if cmds.state == "speaking":
                speak_phase += dt
                # a talking cadence: syllables over phrases
                avatar.level = speaking_level(cmds, speak_phase, now)
            elif cmds.state == "listening":
                avatar.level = cmds.level
            else:
                avatar.level = 0.0
            avatar.update(dt, mode)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
            holo.begin(size, size, t, calm=mode != "full")
            # framed on his aperture and core, close, like the console's portrait
            aim = np.array([0.0, 0.5, 0.0], np.float32)
            pitch = 0.1
            eye = aim + 1.05 * np.array([0, math.sin(pitch), math.cos(pitch)], np.float32)
            holo.camera(eye, aim, 32.0)
            base = hgl.rot_y(0.0)
            table = {"thinking": "amber", "error": "danger"}.get(avatar.state) or tuple(avatar.colour()[0])
            holo.draw_table(base @ hgl.rot_y(-t * 0.08), radius=0.3, cone_top=0.26, cone_h=0.55,
                            power=0.75 + 0.35 * avatar.level, colour=table)
            avatar.draw(holo, base, 1.0)
            holo.end(fbo, bg=(0.0, 0.0, 0.0, 0.0), bloom=1.2, fade=1.0)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
            px = GL.glReadPixels(0, 0, size, size, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
            rows = np.frombuffer(px, np.uint8).reshape(size, size, 4)[::-1]
            out.write(rows.tobytes())
            out.flush()
            busy = cmds.state != "idle" or avatar.level > 0.01
            frame = 1.0 / (fps if busy or mode == "full" else 10)
            if mode == "still":
                frame = 0.5
            spent = time.monotonic() - now
            cmds.wake.wait(max(0.0, frame - spent))
            cmds.wake.clear()
        avatar.release()
        if hasattr(holo, "release"):
            holo.release()
        GL.glDeleteFramebuffers(1, [fbo])
        GL.glDeleteRenderbuffers(1, [rb])
    finally:
        EGL.eglMakeCurrent(display, EGL.EGL_NO_SURFACE, EGL.EGL_NO_SURFACE, EGL.EGL_NO_CONTEXT)
        EGL.eglDestroyContext(display, context)


HEADER = 4096
SLOT_HEADER = 64
MAGIC = b"VHOLOSHM"


def slot_bytes(w, h):
    """One slot's size, header and pixels, rounded up to 4096."""
    if w <= 0 or h <= 0:
        return 0
    return -(-(SLOT_HEADER + w * h * 4) // 4096) * 4096


def layout(size, pw, ph):
    """The slots' offsets (portrait 0, 1, projector 0, 1) and the file's size."""
    sizes = [slot_bytes(size, size)] * 2 + [slot_bytes(pw, ph)] * 2
    offsets, at = [], HEADER
    for n in sizes:
        offsets.append(at)
        at += n
    return offsets, at


class Shared:
    """The frames' shared memory: two slots each for the portrait and the projector."""

    def __init__(self, path, size, pw, ph):
        self.offsets, total = layout(size, pw, ph)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.ftruncate(fd, total)
            self.mm = mmap.mmap(fd, total, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
        finally:
            os.close(fd)
        self.mm[:len(MAGIC)] = MAGIC
        struct.pack_into("<IIII", self.mm, 8, 2, size, pw, ph)
        self.seq = [0, 0, 0, 0]
        self.next = {"portrait": 0, "projector": 0}

    def write(self, which, rows):
        """Write a frame (h, w, 4 bottom row first, as glReadPixels gives it) into this stream's
        next slot; returns (slot, seq)."""
        import numpy as np
        slot = self.next[which]
        self.next[which] = 1 - slot
        k = slot + (2 if which == "projector" else 0)
        off = self.offsets[k]
        h, w = rows.shape[:2]
        self.seq[k] += 1                               # odd: being written
        struct.pack_into("<Q", self.mm, off, self.seq[k])
        dst = np.ndarray((h, w, 4), np.uint8, buffer=self.mm, offset=off + SLOT_HEADER)
        dst[...] = rows[::-1]
        self.seq[k] += 1                               # even: whole
        struct.pack_into("<Q", self.mm, off, self.seq[k])
        return slot, self.seq[k]

    def close(self):
        self.mm.close()


class GpuTimer:
    """GPU time of what's drawn between begin() and end(), read a frame later (never stalls)."""

    def __init__(self, GL):
        self.GL = GL
        self.q = list(GL.glGenQueries(2))
        self.i = 0
        self.pending = [False, False]

    def begin(self):
        self.GL.glBeginQuery(self.GL.GL_TIME_ELAPSED, self.q[self.i])

    def end(self):
        """End this frame's query; return the previous frame's GPU milliseconds, if ready."""
        GL = self.GL
        GL.glEndQuery(GL.GL_TIME_ELAPSED)
        self.pending[self.i] = True
        j = 1 - self.i
        ms = None
        if self.pending[j] and GL.glGetQueryObjectuiv(self.q[j], GL.GL_QUERY_RESULT_AVAILABLE):
            ms = GL.glGetQueryObjectuiv(self.q[j], GL.GL_QUERY_RESULT) / 1e6   # ns; 32 bits is 4 s
            self.pending[j] = False
        self.i = j
        return ms


def say(out, line):
    out.write((line + "\n").encode())
    out.flush()


def stream_shm(size, fps, cmds, out, shm, projector=None, scale=1.0, stats=False):
    """Version 2: his portrait and the projector into shared memory, a line per frame on stdout."""
    import numpy as np
    from OpenGL import EGL, GL

    from holo import gl as hgl
    from holo import motion
    from holo.projector import Projector
    from holo.render import Holo
    from holo.vector.avatar import Avatar

    pw, ph = (round(projector[0] * scale), round(projector[1] * scale)) if projector else (0, 0)
    display, context = egl_context()
    shared = None
    try:
        fbo, rb = target(GL, size)
        holo = Holo(1.0)
        avatar = Avatar()
        proj = pfbo = None
        if projector:
            pfbo, prb = target_rect(GL, pw, ph)
            proj = Projector(projector[0], projector[1], scale)
        shared = Shared(shm, size, pw, ph)
        timers = {"portrait": GpuTimer(GL), "projector": GpuTimer(GL)}
        say(out, f"VECTOR-PORTRAIT 2 {size} {pw} {ph}")
        clocks = {"portrait": motion.Clock(), "projector": motion.Clock()}
        t0 = time.monotonic()
        last = t0
        due = {"portrait": 0.0, "projector": 0.0}
        seen_projection = 0
        speak_phase = 0.0
        sent_regions, regions_at, sent_meta = None, 0.0, None
        tally = {"t": time.monotonic(), "cpu": time.process_time(), "portrait": [0, 0.0, 0], "projector": [0, 0.0, 0],
                 "kinds": {}}
        while not cmds.stop.is_set():
            if cmds.paused:
                cmds.wake.wait(0.5)
                cmds.wake.clear()
                last = time.monotonic()
                due = {"portrait": 0.0, "projector": 0.0}
                continue
            now = time.monotonic()
            dt = min(0.1, now - last)
            last = now
            mode = motion.current() if hasattr(motion, "current") else "full"
            if proj and cmds.projected != seen_projection:
                seen_projection = cmds.projected
                proj.set(cmds.projection)
                due["projector"] = 0.0
            if avatar.state != cmds.state:
                avatar.set_state(cmds.state)
                due["portrait"] = 0.0
            if cmds.state == "speaking":
                speak_phase += dt
                avatar.level = speaking_level(cmds, speak_phase, now)
            elif cmds.state == "listening":
                avatar.level = cmds.level
            else:
                avatar.level = 0.0
            avatar.update(dt, mode)
            if now >= due["portrait"]:
                t = clocks["portrait"](now - t0, mode)
                timers["portrait"].begin()
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
                holo.begin(size, size, t, calm=mode != "full")
                aim = np.array([0.0, 0.5, 0.0], np.float32)
                pitch = 0.1
                eye = aim + 1.05 * np.array([0, math.sin(pitch), math.cos(pitch)], np.float32)
                holo.camera(eye, aim, 32.0)
                base = hgl.rot_y(0.0)
                table = {"thinking": "amber", "error": "danger"}.get(avatar.state) or tuple(avatar.colour()[0])
                holo.draw_table(base @ hgl.rot_y(-t * 0.08), radius=0.3, cone_top=0.26, cone_h=0.55,
                                power=0.75 + 0.35 * avatar.level, colour=table)
                avatar.draw(holo, base, 1.0)
                holo.end(fbo, bg=(0.0, 0.0, 0.0, 0.0), bloom=1.2, fade=1.0)
                gpu = timers["portrait"].end()
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
                px = GL.glReadPixels(0, 0, size, size, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
                slot, seq = shared.write("portrait", np.frombuffer(px, np.uint8).reshape(size, size, 4))
                say(out, f"frame portrait {slot} {seq}")
                busy = cmds.state != "idle" or avatar.level > 0.01
                due["portrait"] = now + (1.0 if mode == "still" else 1.0 / (fps if busy else 10))
                tally["portrait"][0] += 1
                if gpu is not None:
                    tally["portrait"][1] += gpu
                    tally["portrait"][2] += 1
            if proj:
                proj.voice = voice_now(cmds, now)
            if proj and now >= due["projector"]:
                t = clocks["projector"](now - t0, mode)
                timers["projector"].begin()
                regions = proj.draw(pfbo, t, mode=mode, now=now)
                gpu = timers["projector"].end()
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, pfbo)
                px = GL.glReadPixels(0, 0, pw, ph, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
                slot, seq = shared.write("projector", np.frombuffer(px, np.uint8).reshape(ph, pw, 4))
                say(out, f"frame projector {slot} {seq}")
                if regions != sent_regions and now - regions_at >= 0.2:
                    sent_regions, regions_at = regions, now
                    say(out, "regions " + json.dumps(regions, separators=(",", ":")))
                if proj.meta and proj.meta != sent_meta:
                    sent_meta = dict(proj.meta)
                    say(out, "meta " + json.dumps(sent_meta, separators=(",", ":")))
                due["projector"] = now + (1.0 if mode == "still" else 1.0 / proj.rate(fps, now))
                kind = proj.spec.get("kind", "world")
                tally["projector"][0] += 1
                if gpu is not None:
                    tally["projector"][1] += gpu
                    tally["projector"][2] += 1
                    k = tally["kinds"].setdefault(kind, [0, 0.0])
                    k[0] += 1
                    k[1] += gpu
            if stats and now - tally["t"] >= 5.0:
                wall = now - tally["t"]
                cpu = time.process_time() - tally["cpu"]
                rep = {"seconds": round(wall, 2), "cpu_pct": round(100 * cpu / wall, 1)}
                for which in ("portrait", "projector"):
                    n, g, gn = tally[which]
                    rep[which] = {"fps": round(n / wall, 1), "gpu_ms": round(g / gn, 3) if gn else None}
                rep["kinds"] = {k: round(v[1] / v[0], 3) for k, v in tally["kinds"].items() if v[0]}
                say(out, "stats " + json.dumps(rep, separators=(",", ":")))
                tally = {"t": now, "cpu": time.process_time(), "portrait": [0, 0.0, 0], "projector": [0, 0.0, 0],
                         "kinds": {}}
            wait = min(due["portrait"], due["projector"] if proj else due["portrait"]) - time.monotonic()
            cmds.wake.wait(max(0.0, wait))
            cmds.wake.clear()
        avatar.release()
        if proj:
            proj.release()
        if hasattr(holo, "release"):
            holo.release()
    finally:
        if shared:
            shared.close()
        EGL.eglMakeCurrent(display, EGL.EGL_NO_SURFACE, EGL.EGL_NO_SURFACE, EGL.EGL_NO_CONTEXT)
        EGL.eglDestroyContext(display, context)


def target_rect(GL, w, h):
    fbo = GL.glGenFramebuffers(1)
    GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
    rb = GL.glGenRenderbuffers(1)
    GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, rb)
    GL.glRenderbufferStorage(GL.GL_RENDERBUFFER, GL.GL_RGBA8, w, h)
    GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_RENDERBUFFER, rb)
    return fbo, rb


def main(argv=None):
    p = argparse.ArgumentParser(description="VECTOR's portrait (and an app's holo projector) as a stream of RGBA frames")
    p.add_argument("--size", type=int, required=True, help="the portrait's side, in pixels")
    p.add_argument("--fps", type=int, default=30, help="frames a second while it moves")
    p.add_argument("--shm", help="version 2: frames in this shared-memory file, a line each on stdout")
    p.add_argument("--projector", type=int, nargs=2, metavar=("W", "H"),
                   help="with --shm: also draw the holo projector, W x H logical pixels")
    p.add_argument("--scale", type=float, default=1.0, help="the projector's pixels per logical pixel")
    p.add_argument("--stats", action="store_true", help="with --shm: frames, GPU and CPU time every 5 s")
    args = p.parse_args(argv)
    if not 16 <= args.size <= 2048 or not 1 <= args.fps <= 120:
        print("portrait: --size takes 16..2048 and --fps 1..120", file=sys.stderr)
        return 2
    if args.projector and (not args.shm or not all(16 <= v <= 2048 for v in args.projector)
                           or not 0.5 <= args.scale <= 4.0):
        print("portrait: --projector needs --shm, W and H in 16..2048 and --scale in 0.5..4", file=sys.stderr)
        return 2
    cmds = Commands()
    threading.Thread(target=cmds.read, args=(sys.stdin,), name="portrait-commands", daemon=True).start()
    try:
        if args.shm:
            stream_shm(args.size, args.fps, cmds, sys.stdout.buffer, args.shm,
                       tuple(args.projector) if args.projector else None, args.scale, args.stats)
        else:
            stream(args.size, args.fps, cmds, sys.stdout.buffer)
    except BrokenPipeError:
        return 0                       # nobody is watching any more
    except Exception as e:             # no GPU, no EGL, a driver refusing GL 3.3
        print(f"portrait: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    code = main()
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        pass
    os._exit(code)
