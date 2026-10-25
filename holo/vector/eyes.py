"""VECTOR's eyes: read-only sight of the host's screen, only when asked (or to check his own
builds), through the lab's own multimodal model. He never clicks or types into apps.

  look(question, target)      screen | monitor | window (the focused one) | region "x,y wxh"
  read_screen_text(target)    the exact text (errors, logs), transcribed by the same model
  active_window()             what "this" means: the focused window's app, title, workspace
  watch(on|off, every_s)      follow along: a glance at the focused window every N s, kept as
                              a few lines of text for his next answers; a ◉ WATCHING indicator
                              shows the whole time (bar pip + his console); off after 15 min

Privacy, in code:
  * Captures are taken with grim straight into memory (stdout), scaled and sent as one
    request, and dropped; nothing is written to disk, logged or kept (watch mode keeps only
    the model's words, never pixels).
  * The model is the lab's own (LiteLLM on the LAN, a local vLLM); nothing leaves the LAN.
  * The blocklist: when any window inside the capture area is a password manager, a
    banking, trading or wallet site, the Vault UI, or a private browsing window, there is
    no capture at all (watch mode pauses), and he says why.
"""
import base64
import io
import json
import os
import re
import ssl
import subprocess
import threading
import time
import urllib.request

HOME = os.path.expanduser("~")
from ..paths import conf  # noqa: E402
CONF = conf("eyes.json")
from . import llm  # noqa: E402
KEY = llm.key_file()
CA = os.path.join(HOME, ".config/homelab/homelab-ca.crt")
DEFAULT_BLOCK = {
    "classes": ["keepassxc", "org.keepassxc.keepassxc", "bitwarden", "1password", "kwalletmanager", "kwalletmanager5",
                "seahorse", "org.gnome.seahorse.application", "proton-pass", "enpass"],
    "titles": [r"\bbank", r"\bchase\b", r"wells fargo", r"\bfidelity\b", r"\bschwab\b", r"\bvanguard\b", r"robinhood",
               r"coinbase", r"\bkalshi\b", r"polymarket", r"\balpaca\b", r"\bkraken\b", r"paypal", r"venmo",
               r"\bwallet\b", r"metamask", r"phantom", r"\bledger live\b", r"hashicorp vault|\bvault ui\b|vault\.\S*homelab|\bvault\b.*\bsecrets?\b", r"private browsing",
               r"incognito", r"\bprivate window\b", r"password", r"/money/"],
}
WATCH_MAX_S = 15 * 60


def _conf():
    try:
        with open(CONF) as f:
            c = json.load(f)
    except (OSError, ValueError):
        c = {}
    block = c.get("block") or {}
    return {"model": c.get("model") or llm.model("vision"), "max_side": int(c.get("max_side", 1920)),
            "text_max_side": int(c.get("text_max_side", 2560)),
            "classes": [x.lower() for x in DEFAULT_BLOCK["classes"] + block.get("classes", [])],
            "titles": DEFAULT_BLOCK["titles"] + _money_titles() + block.get("titles", [])}


def _money_titles():
    """The user's real-money systems (vector.toml [real_money]: titles, and the consoles' hosts)."""
    import re
    from . import money
    return list(money.TITLES) + [re.escape(h) for h in money.HOSTS]


def _hypr(*args):
    from .. import hyprenv
    hyprenv.live()
    r = subprocess.run(["hyprctl", *args, "-j"], capture_output=True, text=True, timeout=5)
    return json.loads(r.stdout or "null")


def active_window():
    a = _hypr("activewindow") or {}
    if not a or not a.get("address"):
        return {"focused": None, "note": "no window is focused (the desktop itself)"}
    return {"app": a.get("class"), "title": a.get("title"), "workspace": (a.get("workspace") or {}).get("name"),
            "fullscreen": bool(a.get("fullscreen")), "size": a.get("size"), "at": a.get("at")}


def _visible_windows(rect=None):
    """Windows on the shown workspaces (optionally intersecting rect x, y, w, h)."""
    mons = _hypr("monitors") or []
    shown = {m["activeWorkspace"]["id"] for m in mons}
    shown |= {m["specialWorkspace"]["id"] for m in mons if (m.get("specialWorkspace") or {}).get("id")}
    out = []
    for c in _hypr("clients") or []:
        if (c.get("workspace") or {}).get("id") not in shown or not c.get("mapped", True) or c.get("hidden"):
            continue
        if rect:
            x, y, w, h = rect
            cx, cy = c["at"]
            cw, ch = c["size"]
            if cx >= x + w or cx + cw <= x or cy >= y + h or cy + ch <= y:
                continue
        out.append(c)
    return out


def blocked(windows):
    """-> the reason the capture is refused, or None."""
    cf = _conf()
    for c in windows:
        cls = (c.get("class") or "").lower()
        title = (c.get("title") or "")
        if cls in cf["classes"]:
            return f"a password manager is on screen ({c.get('class')})"
        for rx in cf["titles"]:
            if re.search(rx, title, re.I):
                return "a private window is on screen (banking, trading, a wallet, Vault, a password or private browsing)"
    return None


def _target(target):
    """-> (grim args, rect or None, label)."""
    t = (target or "monitor").strip().lower()
    mons = _hypr("monitors") or []
    focused_mon = next((m for m in mons if m.get("focused")), mons[0] if mons else None)
    if t in ("monitor", "this monitor"):
        m = focused_mon
        return ["-o", m["name"]], (m["x"], m["y"], m["width"], m["height"]), f"monitor {m['name']}"
    if t in ("screen", "all", "screens"):
        return [], None, "every screen"
    if t in ("window", "this", "this window", "active"):
        a = _hypr("activewindow") or {}
        if not a.get("address"):
            m = focused_mon
            return ["-o", m["name"]], (m["x"], m["y"], m["width"], m["height"]), "the desktop (no window focused)"
        (x, y), (w, h) = a["at"], a["size"]
        return ["-g", f"{x},{y} {w}x{h}"], (x, y, w, h), f"the {a.get('class')} window"
    m = re.fullmatch(r"(\d+),(\d+)\s+(\d+)x(\d+)", t)
    if m:
        x, y, w, h = map(int, m.groups())
        return ["-g", f"{x},{y} {w}x{h}"], (x, y, w, h), "that region"
    raise ValueError("target: monitor (default), screen, window, or a region 'x,y wxh'")


def _capture(target, max_side):
    """grim into memory -> (JPEG bytes, label, size). Refuses when a blocked window is in view."""
    args, rect, label = _target(target)
    why = blocked(_visible_windows(rect))
    if why:
        raise PermissionError(f"not looking: {why}")
    r = subprocess.run(["grim", "-t", "png", *args, "-"], capture_output=True, timeout=15)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError("couldn't capture the screen (" + (r.stderr.decode(errors="replace").strip()[:120] or "grim failed") + ")")
    from PIL import Image
    im = Image.open(io.BytesIO(r.stdout)).convert("RGB")
    del r
    w, h = im.size
    scale = min(1.0, max_side / max(w, h))
    if scale < 1.0:                       # Lanczos keeps UI text legible at 3/4 size
        im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue(), label, im.size


def _ask(jpeg, prompt, max_tokens=500):
    cf = _conf()
    key = llm.key()
    ctx = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()
    body = {"model": cf["model"], "max_tokens": max_tokens, "temperature": 0.1, "no-log": True,   # LiteLLM: don't log this one
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}}]}]}
    req = urllib.request.Request(llm.url("/chat/completions"), data=json.dumps(body).encode(),   # under either brain
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90, context=ctx) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip()


def look(question="What's on the screen?", target="monitor"):
    t0 = time.monotonic()
    jpeg, label, size = _capture(target, _conf()["max_side"])
    aw = active_window()
    prompt = (f"This is a screenshot of {label} on the user's Linux desktop (Hyprland). The focused window is "
              f"{aw.get('app')} — {aw.get('title')}. Answer the question precisely and briefly, from what is visible "
              f"only; quote any error text exactly; say so if something isn't visible.\n\nQuestion: {question}")
    try:
        answer = _ask(jpeg, prompt)
    finally:
        del jpeg
    return {"looked_at": label, "focused": aw, "answer": answer, "seconds": round(time.monotonic() - t0, 2),
            "image": f"{size[0]}x{size[1]}, not kept"}


def read_screen_text(target="window"):
    t0 = time.monotonic()
    jpeg, label, size = _capture(target, _conf()["text_max_side"])
    try:
        text = _ask(jpeg, "Transcribe all readable text in this screenshot exactly, line by line, keeping the "
                          "original wording, numbers and punctuation. No commentary.", max_tokens=1500)
    finally:
        del jpeg
    return {"read": label, "text": text, "seconds": round(time.monotonic() - t0, 2), "image": "not kept"}


# ------------------------------------------------------------------ watch mode
class Watch:
    def __init__(self):
        self.on = False
        self.until = 0.0
        self.every = 20
        self.notes = []            # (time, text) — words only, never pixels
        self.paused = None
        self.thread = None
        self.on_change = None      # callback(state dict) for the indicator

    def state(self):
        return {"on": self.on, "seconds_left": max(0, int(self.until - time.time())) if self.on else 0,
                "every_s": self.every, "paused": self.paused}

    def start(self, every_s=20):
        self.every = max(10, min(int(every_s or 20), 120))
        self.until = time.time() + WATCH_MAX_S
        self.notes = []
        if not self.on:
            self.on = True
            self.thread = threading.Thread(target=self._loop, daemon=True, name="eyes-watch")
            self.thread.start()
        self._changed()
        return self.state()

    def stop(self):
        self.on = False
        self.notes = []
        self.paused = None
        self._changed()
        return {"on": False}

    def _changed(self):
        if self.on_change:
            try:
                self.on_change(self.state())
            except Exception:
                pass

    def _loop(self):
        last_title = None
        while self.on:
            if time.time() > self.until:
                self.stop()
                break
            try:
                aw = active_window()
                key = (aw.get("app"), aw.get("title"))
                jpeg, label, _ = _capture("window", 1600)
                self.paused = None
                if key != last_title or not self.notes or time.time() - self.notes[-1][0] > 60:
                    text = _ask(jpeg, "In two short sentences: what is the user doing in this window right now, "
                                      "and is there an error or problem visible (quote it exactly)?", max_tokens=160)
                    self.notes = (self.notes + [(time.time(), f"{aw.get('app')}: {text}")])[-6:]
                    last_title = key
                del jpeg
            except PermissionError as e:
                self.paused = str(e)[13:]
            except Exception as e:
                self.paused = f"couldn't look ({type(e).__name__})"
            self._changed()
            for _ in range(self.every):
                if not self.on:
                    break
                time.sleep(1)

    def context(self):
        if not self.on or not self.notes:
            return ""
        lines = "\n".join(f"- {time.strftime('%H:%M:%S', time.localtime(t))} {x}" for t, x in self.notes[-4:])
        return ("\nWHAT YOU'VE SEEN (watch mode is on: Sir asked you to follow along; glance notes, newest last)\n"
                + lines + "\n")


WATCH = Watch()


def watch(mode="on", every_s=20):
    m = (mode or "on").lower()
    if m in ("on", "start"):
        return {**WATCH.start(every_s), "note": "a ◉ WATCHING indicator shows on the bar while it's on; it switches "
                                                 "itself off after 15 minutes"}
    if m in ("off", "stop"):
        return WATCH.stop()
    return WATCH.state()
