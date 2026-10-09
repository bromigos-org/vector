#!/usr/bin/env python3
"""Tests for VECTOR's eyes (holo/vector/eyes.py). Run in the brain venv:
    ~/.local/share/bromigos/venv-brain/bin/python tools/test-eyes.py

1. He reads a terminal error exactly: a terminal-style image with a known error is sent
   through the same model call his look() makes, and the error line must come back verbatim.
2. He identifies the focused app (hyprctl's answer, and a real look naming it).
3. The blocklist suppresses a capture: with a password manager or a banking page among the
   visible windows, nothing is captured (grim is never run) and he says why.
4. No image remains on disk: every place an image could land is listed before and after a
   real look and a read_screen_text; nothing new appears.
"""
import io
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from holo.vector import eyes  # noqa: E402

bad = 0
ERROR = "error[E0425]: cannot find value `relay_beam` in this scope"


def ok(cond, what, detail=""):
    global bad
    print(f"{'pass' if cond else 'FAIL'}  {what}" + (f" — {detail}" if detail else ""))
    bad += 0 if cond else 1


def terminal_image():
    from PIL import Image, ImageDraw, ImageFont
    lines = ["~/github.com/bromigos-org/wick-relay on main", "$ cargo build",
             "   Compiling wick-relay v0.4.1 (/home/user/github.com/bromigos-org/wick-relay)",
             ERROR, "  --> src/beam.rs:42:17", "   |", "42 |         let lit = relay_beam.is_lit();",
             "   |                   ^^^^^^^^^^ not found in this scope", "",
             "error: could not compile `wick-relay` (bin \"wick-relay\") due to 1 previous error", "$ "]
    try:
        font = ImageFont.truetype(os.path.expanduser("~/.local/share/fonts/GeistMono/GeistMono-Medium.ttf"), 18)
    except OSError:
        font = ImageFont.load_default()
    im = Image.new("RGB", (1100, 30 + 26 * len(lines)), (12, 14, 12))
    d = ImageDraw.Draw(im)
    for i, l in enumerate(lines):
        d.text((16, 14 + 26 * i), l, fill=(255, 110, 100) if l.startswith("error") else (200, 220, 200), font=font)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def image_files():
    """Every image file in the places a capture could land (tmpfs, temp, caches, home)."""
    roots = ["/tmp", "/dev/shm", os.environ.get("XDG_RUNTIME_DIR", ""), os.path.expanduser("~/.cache"),
             os.path.expanduser("~/.local/state/bromigos"), os.path.expanduser("~")]
    out = set()
    for r in roots:
        if not r or not os.path.isdir(r):
            continue
        depth = 0 if r == os.path.expanduser("~") else 3
        for root, dirs, files in os.walk(r):
            if root[len(r):].count(os.sep) >= depth:
                dirs[:] = []
            for f in files:
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".ppm", ".webp")):
                    p = os.path.join(root, f)
                    try:
                        if time.time() - os.path.getmtime(p) < 300:
                            out.add(p)
                    except OSError:
                        pass
    return out


def main():
    print("== reading a terminal error")
    t0 = time.monotonic()
    ans = eyes._ask(terminal_image(), "This is a screenshot of a terminal. What error is shown? Quote the error line "
                                      "exactly, then say which file and line it points to.")
    norm = lambda t: t.replace("`", "").replace("'", "").replace('"', "").replace("*", "")  # noqa: E731
    ok(norm(ERROR) in norm(ans), "the error line comes back verbatim (quote marks aside)", ans[:160])
    ok("beam.rs:42" in ans or ("beam.rs" in ans and "42" in ans), "and where it points (src/beam.rs:42)")
    print(f"      ({time.monotonic() - t0:.1f} s)")

    print("== the focused app")
    aw = eyes.active_window()
    raw = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True).stdout
    ok(aw.get("app") and aw["app"] in raw, "active_window matches hyprctl", f"{aw.get('app')} — {(aw.get('title') or '')[:40]}")

    print("== the blocklist (nothing is captured)")
    calls = []
    real_run, real_vis = subprocess.run, eyes._visible_windows

    def spy(argv, *a, **k):
        if argv and argv[0] == "grim":
            calls.append(argv)
        return real_run(argv, *a, **k)
    eyes.subprocess.run = spy
    try:
        for fake in ({"class": "org.keepassxc.KeePassXC", "title": "Passwords.kdbx - KeePassXC"},
                     {"class": "google-chrome", "title": "Chase Online Banking - Google Chrome"},
                     {"class": "firefox", "title": "Mozilla Firefox Private Browsing"},
                     {"class": "librewolf", "title": "Kalshi — Markets"}):
            eyes._visible_windows = lambda rect=None, f=fake: real_vis(rect) + [f]
            try:
                eyes.look("what's on screen?", "monitor")
                ok(False, f"blocked: {fake['title'][:40]}", "LOOKED")
            except PermissionError as e:
                ok(not calls, f"blocked: {fake['title'][:40]}", str(e)[:80])
    finally:
        eyes.subprocess.run, eyes._visible_windows = real_run, real_vis
    ok(not calls, "grim was never run for a blocked view")

    print("== no image left behind")
    before = image_files()
    why = eyes.blocked(eyes._visible_windows())
    if why:
        print(f"      (a real look is blocked right now: {why}; skipping the live look)")
    else:
        r = eyes.look("Which application is focused? One short sentence.", "window")
        ok(bool(r.get("answer")) and "not kept" in r.get("image", ""), "a real look", f"{r['answer'][:100]} ({r['seconds']} s)")
        r2 = eyes.read_screen_text("window")
        ok(len(r2.get("text", "")) > 0, "read_screen_text returns text", f"{len(r2['text'])} chars, {r2['seconds']} s")
    after = image_files()
    new = sorted(after - before)
    ok(not new, "no new image file anywhere (tmp, shm, runtime, caches, state, home)", ", ".join(new[:3]))

    print("FAILURES:", bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
