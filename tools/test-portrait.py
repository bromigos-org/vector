#!/usr/bin/env python3
"""Offscreen checks for VECTOR's portrait stream (holo/portrait.py), the way another app reads
it: a process of its own, frames on stdout, commands on stdin.

Runs headless (EGL: an NVIDIA device, else Mesa; never a window, never the running desktop),
in a throwaway HOME and runtime dir, and checks:

  - it says it's ready ("VECTOR-PORTRAIT 1 N") before the first frame;
  - frames are N*N*4 bytes of premultiplied RGBA with him in them (lit pixels, no colour
    brighter than its alpha), and they move;
  - commands change him: listening with a loud microphone, then thinking, draw different
    frames from idle;
  - paused, it sends nothing; unpaused, it goes on;
  - at the end of its input it stops by itself, with exit 0;
  - bad arguments are refused with exit 2.

    python3 tools/test-portrait.py        # exit 1 on a failure
"""
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
PORTRAIT = os.path.join(HERE, "holo", "portrait.py")
SIZE = 96
FRAME = SIZE * SIZE * 4

lines, failures = [], []


def check(name, ok, detail=""):
    lines.append(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def env():
    sandbox = tempfile.mkdtemp(prefix="vector-portrait-")
    e = {k: v for k, v in os.environ.items()
         if k not in ("WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS")}
    for k, sub in (("HOME", ""), ("XDG_CONFIG_HOME", ".config"), ("XDG_STATE_HOME", ".local/state"),
                   ("XDG_DATA_HOME", ".local/share"), ("XDG_CACHE_HOME", ".cache"), ("XDG_RUNTIME_DIR", "run")):
        e[k] = os.path.join(sandbox, sub)
        os.makedirs(e[k], exist_ok=True)
    os.chmod(e["XDG_RUNTIME_DIR"], 0o700)
    return sandbox, e


def read_exact(stream, n, seconds=20.0):
    buf, end = b"", time.monotonic() + seconds
    while len(buf) < n:
        left = end - time.monotonic()
        if left <= 0 or not select.select([stream], [], [], left)[0]:
            return None
        chunk = os.read(stream.fileno(), n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def frames(proc, n):
    """The newest of the next n frames."""
    got = None
    for _ in range(n):
        got = read_exact(proc.stdout, FRAME) or got
    return got


def lit(frame):
    return sum(1 for a in frame[3::4] if a > 8)


def premultiplied(frame):
    return all(frame[i] <= frame[i + 3] + 1 and frame[i + 1] <= frame[i + 3] + 1 and frame[i + 2] <= frame[i + 3] + 1
               for i in range(0, len(frame), 4 * 7))


def differ(a, b):
    return sum(1 for x, y in zip(a, b) if abs(x - y) > 6)


def send(proc, line):
    proc.stdin.write((line + "\n").encode())
    proc.stdin.flush()


def main():
    sandbox, e = env()
    try:
        bad = subprocess.run([sys.executable, PORTRAIT, "--size", "4"], env=e, capture_output=True, timeout=60)
        check("bad arguments are refused", bad.returncode == 2, f"exit {bad.returncode}")

        proc = subprocess.Popen([sys.executable, PORTRAIT, "--size", str(SIZE), "--fps", "30"], env=e,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        header = b""
        end = time.monotonic() + 60
        while not header.endswith(b"\n") and time.monotonic() < end:
            got = read_exact(proc.stdout, 1, 60)
            if got is None:
                break
            header += got
        if not header:
            err = proc.stderr.read().decode(errors="replace").strip()
            print(f"no frames: {err or 'it stopped'} (an EGL device is needed)")
            return 1
        check("it says it's ready first", header == f"VECTOR-PORTRAIT 1 {SIZE}\n".encode(), repr(header))
        first = frames(proc, 1)
        check("a frame is N*N*4 bytes", first is not None and len(first) == FRAME)
        idle = frames(proc, 20)
        check("he's in the frame", idle is not None and lit(idle) > SIZE * SIZE // 20, f"{lit(idle or b'')} lit")
        check("the frame is premultiplied", idle is not None and premultiplied(idle))
        check("he moves", first is not None and idle is not None and differ(first, idle) > 50)

        send(proc, "state listening")
        for _ in range(10):
            send(proc, "level 1.0")
        listening = frames(proc, 20)
        check("listening draws him differently", listening is not None and differ(idle, listening) > 200)
        send(proc, "state thinking")
        thinking = frames(proc, 20)
        check("thinking draws him differently", thinking is not None and differ(listening, thinking) > 200)

        send(proc, "pause 1")
        time.sleep(0.3)
        while read_exact(proc.stdout, FRAME, 0.3):     # what was already on the way
            pass
        check("paused, nothing comes", read_exact(proc.stdout, FRAME, 1.0) is None)
        send(proc, "pause 0")
        check("unpaused, he goes on", frames(proc, 3) is not None)

        proc.stdin.close()
        try:
            while proc.stdout.read(65536):
                pass
            code = proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = None
        check("at the end of input it stops, exit 0", code == 0, f"exit {code}")
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
    print("\n".join(lines))
    print(f"\n{len(lines) - len(failures)}/{len(lines)} checks pass" + (f"; failed: {', '.join(failures)}" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
