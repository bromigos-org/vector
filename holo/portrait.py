#!/usr/bin/env python3
"""VECTOR's portrait as a stream of frames, so another app can show him live without running
his renderer inside it (BRODEC's desktop panel does).

  python3 holo/portrait.py --size N [--fps F]

It draws him the way his console's portrait does (holo.render.Holo and
holo.vector.avatar.Avatar, OpenGL 3.3 core) on a surfaceless EGL context of its own, so it
needs no window and no display: framed close on his aperture and core, over his table.

stdout  one line, "VECTOR-PORTRAIT 1 N", once he's ready; then frames of N*N*4 bytes each,
        premultiplied RGBA, top row first, until it stops.
stdin   one command per line:
          state idle|listening|thinking|speaking
          level 0.42      the microphone's loudness while he listens, 0..1
          pause 1|0       hidden: draw nothing until unpaused
        At the end of its input it stops.

He follows his state as in the console: listening moves with `level`, speaking pulses with
a talking cadence, and the motion setting (holo.motion) calms or stills him. When it can't
draw (no GPU, a driver refusing GL 3.3), it says why in one line on stderr and exits 1.
"""
import argparse
import ctypes
import math
import os
import sys
import threading
import time

os.environ.setdefault("PYOPENGL_PLATFORM", "egl")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

STATES = ("idle", "listening", "thinking", "speaking")


class Commands:
    """What the reader of stdin last heard: his state, the loudness, paused, stopped."""

    def __init__(self):
        self.state = "idle"
        self.level = 0.0
        self.paused = False
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
        elif word == "pause":
            self.paused = value == "1"
        else:
            return
        self.wake.set()

    def read(self, stream):
        for line in stream:
            self.take(line)
        self.stop.set()
        self.wake.set()


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
                avatar.level = 0.35 + 0.35 * abs(math.sin(speak_phase * 9.0)) * (0.6 + 0.4 * math.sin(speak_phase * 1.7))
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


def main(argv=None):
    p = argparse.ArgumentParser(description="VECTOR's portrait as a stream of RGBA frames on stdout")
    p.add_argument("--size", type=int, required=True, help="the frame's side, in pixels")
    p.add_argument("--fps", type=int, default=30, help="frames a second while he moves")
    args = p.parse_args(argv)
    if not 16 <= args.size <= 2048 or not 1 <= args.fps <= 120:
        print("portrait: --size takes 16..2048 and --fps 1..120", file=sys.stderr)
        return 2
    cmds = Commands()
    threading.Thread(target=cmds.read, args=(sys.stdin,), name="portrait-commands", daemon=True).start()
    try:
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
