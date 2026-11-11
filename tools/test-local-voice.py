#!/usr/bin/env python3
"""The local voice without a GPU, a game or the network: everything outside is faked.

  1. game signs (holo/gamewatch.py): GameMode with clients (and never starting gamemoded),
     a Steam game, a fullscreen window, a GPU process not on the allowlist; the desktop's own
     GPU users, VECTOR's own processes and tiny allocations don't count;
  2. the setting (holo/local_voice_state.py): on, off, auto with a roomy NVIDIA card, a small
     one and none; the status line;
  3. the models (holo/local_models.py): a file is kept only when its sha256 matches, a bad one
     is deleted;
  4. the worker's life (holo/local_voice.py, the fake worker: a test tone, no models): started
     on the first line, which another engine speaks; ready for the next, which it speaks; it
     hears; a game makes it leave at once and keeps it away until the game has been gone for
     game_clear_s; it leaves by itself after idle_exit_s;
  5. the voice server's order: local first while it's on, `qwen` left out; nothing on the card
     while a game runs.

Nothing is spoken and no window opens. State, sockets and caches live in a temporary folder.

Run: python3 tools/test-local-voice.py
"""
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="vector-local-voice-")
os.environ.update(XDG_STATE_HOME=os.path.join(TMP, "state"), XDG_RUNTIME_DIR=os.path.join(TMP, "run"),
                  XDG_CACHE_HOME=os.path.join(TMP, "cache"), BROMIGOS_LOCAL_VOICE_FAKE="1",
                  BROMIGOS_LOCAL_VOICE_SOCK=os.path.join(TMP, "run", "lv.sock"),
                  BROMIGOS_VOICE_STATUS=os.path.join(TMP, "run", "voice.json"))
for d in ("state", "run", "cache"):
    os.makedirs(os.path.join(TMP, d), exist_ok=True)
sys.path.insert(0, HERE)

from holo import gamewatch, local_models, local_voice, local_voice_state  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"  ({detail})"))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------------- 1. game signs
def fake_run(table, calls=None):
    def run(argv, timeout=3):
        if calls is not None:
            calls.append(argv)
        for key, (rc, out) in table.items():
            if " ".join(argv).startswith(key):
                return rc, out
        return 127, ""
    return run


def proc_tree(cmds):
    root = os.path.join(TMP, f"proc{len(os.listdir(TMP))}")
    for pid, cmd in cmds.items():
        os.makedirs(os.path.join(root, str(pid)))
        with open(os.path.join(root, str(pid), "cmdline"), "wb") as f:
            f.write(cmd.replace(" ", "\0").encode() + b"\0")
    return root


SMI = """+-----------------------------------------------------------------------------------------+
| Processes:                                                                              |
|  GPU   GI   CI              PID   Type   Process name                        GPU Memory |
|=========================================================================================|
{rows}+-----------------------------------------------------------------------------------------+
"""


def smi(rows):
    return SMI.format(rows="".join(f"|    0   N/A  N/A         {pid:>8}      {t}   {name:<32}  {mib:>6}MiB |\n"
                                   for pid, t, name, mib in rows))


DESKTOP = {1678: "Hyprland --watchdog-fd 4", 1976: "Xwayland :0 -rootless", 3916: "kitty",
           2500: "/opt/google/chrome/chrome --type=gpu-process --foo", 2600: "/usr/bin/python3 -m live.app",
           2700: "python3 /usr/lib/bromigos/bin/bromigos-sound popup"}
DESK_ROWS = [(1678, "G", "Hyprland", 152), (1976, "G", "Xwayland", 3), (3916, "G", "kitty", 52),
             (2500, "C+G", "...chrome", 377), (2600, "C+G", "/usr/bin/python3", 653), (2700, "C+G", "python3", 5)]
NO_GAMEMODE = {"busctl --user call org.freedesktop.DBus": (0, "b false\n")}

calls = []
w = gamewatch.GameWatch(run=fake_run({**NO_GAMEMODE, "nvidia-smi": (0, smi(DESK_ROWS))}, calls),
                        proc=proc_tree(DESKTOP), env={})
check("the desktop alone is no game", w.check() is None, w.check())
check("gamemoded isn't started to ask it", not any("get-property" in a for a in calls), calls)

w = gamewatch.GameWatch(run=fake_run({"busctl --user call org.freedesktop.DBus": (0, "b true\n"),
                                      "busctl --user --auto-start=no get-property": (0, "i 1\n")}),
                        proc=proc_tree({}), env={})
check("GameMode with a client is a game", (w.check() or "").startswith("GameMode is on"), w.check())
w = gamewatch.GameWatch(run=fake_run({"busctl --user call org.freedesktop.DBus": (0, "b true\n"),
                                      "busctl --user --auto-start=no get-property": (0, "i 0\n")}),
                        proc=proc_tree({}), env={})
check("GameMode with no clients is no game", w.check() is None, w.check())

w = gamewatch.GameWatch(run=fake_run(NO_GAMEMODE), env={},
                        proc=proc_tree({**DESKTOP, 5000: "/home/u/.steam/ubuntu12_32/reaper SteamLaunch AppId=1245620 "
                                                         "-- /home/u/.steam/steamapps/common/game/run.sh"}))
check("Steam's launcher is a game", "AppId 1245620" in (w.check() or ""), w.check())
w = gamewatch.GameWatch(run=fake_run(NO_GAMEMODE), env={},
                        proc=proc_tree({**DESKTOP, 5001: "/home/u/.steam/steam/steam -silent"}))
check("Steam itself, with no game, isn't", w.check() is None, w.check())

w = gamewatch.GameWatch(run=fake_run({**NO_GAMEMODE, "hyprctl -j activewindow":
                                      (0, json.dumps({"class": "mpv", "fullscreen": 2}))}),
                        proc=proc_tree({}), env={"HYPRLAND_INSTANCE_SIGNATURE": "x"})
check("a fullscreen window counts", "fullscreen window (mpv)" in (w.check() or ""), w.check())
w = gamewatch.GameWatch(run=fake_run({**NO_GAMEMODE, "hyprctl -j activewindow":
                                      (0, json.dumps({"class": "kitty", "fullscreen": 0}))}),
                        proc=proc_tree({}), env={"HYPRLAND_INSTANCE_SIGNATURE": "x"})
check("a window that isn't fullscreen doesn't", w.check() is None, w.check())

game_rows = DESK_ROWS + [(7777, "G", "Z:\\games\\Game.exe", 4200)]
w = gamewatch.GameWatch(run=fake_run({**NO_GAMEMODE, "nvidia-smi": (0, smi(game_rows))}), env={},
                        proc=proc_tree({**DESKTOP, 7777: "Z:\\games\\Game.exe -dx12"}))
check("a GPU process not on the allowlist counts", "Game.exe" in (w.check() or ""), w.check())
w.own.add(7777)
check("VECTOR's own processes don't", w.check() is None, w.check())
w = gamewatch.GameWatch({"gpu_min_mib": 64},
                        run=fake_run({**NO_GAMEMODE, "nvidia-smi": (0, smi(DESK_ROWS + [(8888, "C", "tool", 20)]))}),
                        env={}, proc=proc_tree({**DESKTOP, 8888: "/usr/bin/sometool"}))
check("a tiny GPU user doesn't", w.check() is None, w.check())
w = gamewatch.GameWatch({"gpu_allow": [r"blender"]},
                        run=fake_run({**NO_GAMEMODE, "nvidia-smi": (0, smi(DESK_ROWS + [(8889, "C+G", "blender", 900)]))}),
                        env={}, proc=proc_tree({**DESKTOP, 8889: "/usr/bin/blender"}))
check("voice.json's gpu_allow adds to the allowlist", w.check() is None, w.check())
w = gamewatch.GameWatch(run=fake_run({}), proc=os.path.join(TMP, "nowhere"), env={})
check("nothing readable (no NVIDIA, no busctl, no Hyprland) is no game", w.check() is None, w.check())


# ---------------------------------------------------------------------- 2. the setting
class R:
    def __init__(self, out, rc=0):
        self.stdout, self.returncode = out, rc


def cards(out, rc=0):
    return lambda *a, **k: R(out, rc)


S = local_voice_state
check("no file: auto", S.setting() == "auto")
on = S.enabled(run=cards("NVIDIA GeForce RTX 5070, 12227\n"))
check("auto with a 12 GB NVIDIA card: on", on[0] and "RTX 5070, 12 GB" in on[1], on)
on = S.enabled(run=cards("NVIDIA GeForce GTX 1050, 4096\n"))
check("auto with a 4 GB card: off", not on[0] and "needs 8" in on[1], on)
on = S.enabled(run=cards("", 127))
check("auto without an NVIDIA card: off", not on[0] and "no NVIDIA card" in on[1], on)
S.set_setting("off")
check("off is off, whatever the card", not S.enabled(run=cards("NVIDIA RTX 5090, 32607\n"))[0])
S.set_setting("on")
check("on with a small card: on", S.enabled(run=cards("NVIDIA GeForce GTX 1050, 4096\n"))[0])
check("on without a card: off", not S.enabled(run=cards("", 127))[0])
S.set_setting("auto")
check("auto removes the file", not os.path.exists(S.SETTING))
ln = S.line({"speaking_from": "lab", "local": {"state": "away", "why": "a Steam game is running (AppId 7)"}},
            (True, "on (auto: NVIDIA GeForce RTX 5070, 12 GB)"))
check("the status line says where and why", ln == "Local voice: on (auto: NVIDIA GeForce RTX 5070, 12 GB). Speaking "
      "from: the lab (local voice stepped aside: a Steam game is running (AppId 7)).", ln)
ln = S.line({"speaking_from": "local", "local": {"state": "ready"}}, (True, "on (your setting, RTX)"))
check("the status line when it speaks here", "this computer (Qwen3-TTS 0.6B on the graphics card)" in ln, ln)
check("the status line before he runs", "isn't running" in S.line(None, (False, "off (your setting)")))

# ---------------------------------------------------------------------- 3. the models
good, bad = b"model bytes " * 1000, b"tampered " * 1000
local_models.MODELS = {"tts": {"name": "test", "repo": "x/y", "revision": "0" * 40,
                               "files": {"m.gguf": (hashlib.sha256(good).hexdigest(), len(good))}}}
root = os.path.join(TMP, "models")


class Body(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


served = []
check("nothing fetched: not present", not local_models.present(root=root))
local_models.fetch(root=root, log=lambda *a: None, opener=lambda req, timeout: served.append(req.full_url) or Body(good))
check("a matching file is kept and recorded", local_models.present(root=root))
check("it came from the pinned revision", served and "/resolve/" + "0" * 40 + "/m.gguf" in served[0], served)
served.clear()
local_models.fetch(root=root, log=lambda *a: None, opener=lambda req, timeout: served.append(1) or Body(good))
check("a verified file isn't fetched again", not served)
os.unlink(local_models.path("tts", "m.gguf", root))
try:
    local_models.fetch(root=root, log=lambda *a: None, opener=lambda req, timeout: Body(bad))
    raised = False
except RuntimeError:
    raised = True
check("a file that doesn't match is refused", raised)
check("and deleted, never used", not os.path.exists(local_models.path("tts", "m.gguf", root)) and
      not os.path.exists(local_models.path("tts", "m.gguf", root) + ".part") and not local_models.present(root=root))


# ---------------------------------------------------------------------- 4. the worker's life
class Watch:
    def __init__(self):
        self.why, self.own = None, set()

    def check(self):
        return self.why


cfg = {"local": {"idle_exit_s": 600, "game_clear_s": 1, "watch_s": 0.2}}
watch = Watch()
logs = []
m = local_voice.Manager(cfg, log=lambda *a: logs.append(" ".join(map(str, a))), watch=watch,
                        sock=os.environ["BROMIGOS_LOCAL_VOICE_SOCK"])
m._on, m._on_at = (True, "on (test)"), 1e18          # the setting: on, without nvidia-smi


def wait(pred, s=20):
    t0 = time.monotonic()
    while time.monotonic() - t0 < s:
        if pred():
            return True
        time.sleep(0.05)
    return False


REF = os.path.join(HERE, "voices", "vector-ref-A.wav")
try:
    m.tts("Hello there.", REF, "ref text")
    first_raised = False
except RuntimeError:
    first_raised = True
check("the first line: the local voice isn't ready, so another engine speaks it", first_raised)
check("and it starts", m.proc is not None)
check("it becomes ready", wait(m.ready), m.state)
audio = list(m.tts("Two words.", REF, "ref text"))
check("the next line is spoken by it", len(audio) > 0 and audio[0][1] == 24000, audio[:1])
pcm = os.path.join(TMP, "heard.pcm")
open(pcm, "wb").write(b"\0\0" * 1600)
check("it hears", wait(lambda: (m.stt(pcm) or "").startswith("fake heard 1600")), m.stt(pcm))
pid = m.proc.pid
watch.why = "a Steam game is running (AppId 7)"
m.tick()
gone = wait(lambda: not os.path.exists(f"/proc/{pid}") or open(f"/proc/{pid}/stat").read().split()[2] == "Z", 5)
check("a game: it leaves at once", gone and m.proc is None and m.state == "away", m.state)
m.ensure()
check("and doesn't come back while the game runs", m.proc is None)
watch.why = None
m.tick()
check("just after the game it stays away", m.state == "away", m.state)
time.sleep(1.1)
m.tick()
check(f"{cfg['local']['game_clear_s']} s after the game it may come back", m.state == "stopped", m.state)
m.ensure()
check("and it starts again on the next speech", wait(m.ready), m.state)
st = json.load(open(os.environ["BROMIGOS_VOICE_STATUS"])) if os.path.exists(os.environ["BROMIGOS_VOICE_STATUS"]) else None
m.stop("test done")
check("told to leave, it leaves", m.proc is None)

cfg2 = {"local": {"idle_exit_s": 1, "game_clear_s": 1, "watch_s": 0.2}}
m2 = local_voice.Manager(cfg2, log=lambda *a: None, watch=Watch(), sock=os.environ["BROMIGOS_LOCAL_VOICE_SOCK"])
m2._on, m2._on_at = (True, "on (test)"), 1e18
m2.ensure()
check("(a second manager's worker starts)", wait(m2.ready), m2.state)
p2 = m2.proc


def idle_gone():
    m2.tick()
    return p2.poll() is not None and m2.state == "stopped"


check("idle, it leaves by itself", wait(idle_gone, 10), m2.state)
check("and says why", m2.why == "idle", m2.why)

# ---------------------------------------------------------------------- 5. the voice server's order
sys.modules.setdefault("bromigos_private", types.SimpleNamespace(url=lambda *a: None, get=lambda *a: None))
try:
    import scipy  # noqa: F401
    have_scipy = True
except ImportError:
    have_scipy = False
if have_scipy:
    from holo import voice_server
    srv = voice_server.Server.__new__(voice_server.Server)
    srv.cfg = {"engine": "lab", "fallback": ["qwen", "kokoro"]}
    srv.qwen = None
    srv.qwen_loading = types.SimpleNamespace(is_set=lambda: False)
    srv.order = lambda: ("lab", ["qwen", "kokoro"])
    srv.lab_url = lambda: "https://lab.example"
    srv.warm_tts = lambda local=False: None

    class L:
        def __init__(self, wanted, state):
            self._w, self.state = wanted, state

        def wanted(self):
            return self._w
    srv.local = L(True, "stopped")
    check("on: local first, then the lab, then Kokoro; no qwen", srv._engines() == ["local", "lab", "kokoro"],
          srv._engines())
    srv.local = L(False, "away")
    check("a game: nothing on the card (no qwen)", srv._engines() == ["lab", "kokoro"], srv._engines())
    srv.local = L(False, "off")
    srv.qwen = object()
    check("off: the old order", srv._engines() == ["lab", "qwen", "kokoro"], srv._engines())
else:
    print("skip the voice server's order (no scipy here)")

print("all good" if not failures else f"{len(failures)} failed: {failures}")
sys.exit(1 if failures else 0)
