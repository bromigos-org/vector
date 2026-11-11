"""Is the user playing a game (or running other GPU work)? The local voice steps aside if so.

Four signs, any one is enough (check() returns the first one it finds, in plain words, or None):

  1. GameMode: Feral's gamemoded has clients (its D-Bus ClientCount). Asked without starting
     gamemoded: if it isn't running, there's no answer and no sign.
  2. Steam's launcher: a `reaper SteamLaunch AppId=…` process, which Steam starts for every
     game it runs, native or Proton.
  3. A fullscreen window: Hyprland's active window is fullscreen (any app: a game, a film).
  4. A GPU process not on the allowlist: something other than the desktop (Hyprland, Xwayland,
     the bar, terminals, browsers, bromigOS's own pieces, VECTOR himself) holds at least
     `gpu_min_mib` of the NVIDIA card's memory.

This only looks. It never changes, pauses, slows or limits anything of the user's: the local
voice is the one that leaves (holo/local_voice.py). Standard library only; every outside call
(a command, /proc) goes through a hook the tests replace (tools/test-local-voice.py).
"""
import json
import os
import re
import subprocess

ALLOW = [
    r"(^|/)(Hyprland|Xwayland|hyprpolkitagent|hyprlock|hyprpaper|hypridle|hyprsunset|waybar|swayosd-server|"
    r"swaync|dunst|mako|walker|elephant|quickshell|kitty|alacritty|foot|ghostty|wezterm(-gui)?|"
    r"chrome|chromium|google-chrome(-stable)?|firefox|librewolf|zen|brave|vivaldi|opera|epiphany|"
    r"xdg-desktop-portal(-hyprland|-gtk)?|nautilus|thunar|gnome-shell|plasmashell|kwin_wayland)(\s|$)",
    r"/usr/lib/bromigos/|/usr/share/bromigos/|(^|/)bromigos-[\w-]+",
    r"-m (live\.app|holo\.app|holo\.voice_server|holo\.local_voice)\b",
    r"--type=(gpu-process|renderer|utility)",                 # a browser's or Electron app's GPU helper
]
GAMEMODE = ("com.feralinteractive.GameMode", "/com/feralinteractive/GameMode", "com.feralinteractive.GameMode")


def _run(argv, timeout=3):
    """(returncode, stdout); (127, "") when the command isn't there."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return 127, ""


class GameWatch:
    def __init__(self, cfg=None, run=_run, proc="/proc", env=None):
        c = cfg or {}
        self.run = run
        self.proc = proc
        self.env = os.environ if env is None else env
        self.allow = [re.compile(p) for p in ALLOW + list(c.get("gpu_allow") or [])]
        self.min_mib = int(c.get("gpu_min_mib", 64))
        self.own = set()                           # pids that are VECTOR's own (the voice server, the worker)

    # ------------------------------------------------------------------ the signs
    def gamemode(self):
        rc, out = self.run(["busctl", "--user", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus",
                            "org.freedesktop.DBus", "NameHasOwner", "s", GAMEMODE[0]])
        if rc != 0 or out.split()[-1:] != ["true"]:
            return None                            # gamemoded isn't running: no clients, and we don't start it
        rc, out = self.run(["busctl", "--user", "--auto-start=no", "get-property", *GAMEMODE, "ClientCount"])
        m = re.match(r"\s*i\s+(\d+)", out or "")
        if rc == 0 and m and int(m.group(1)) > 0:
            return f"GameMode is on ({m.group(1)} {'game' if m.group(1) == '1' else 'games'})"
        return None

    def _cmdline(self, pid):
        try:
            with open(os.path.join(self.proc, str(pid), "cmdline"), "rb") as f:
                return f.read().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            return ""

    def steam(self):
        try:
            pids = [p for p in os.listdir(self.proc) if p.isdigit()]
        except OSError:
            return None
        for p in pids:
            c = self._cmdline(p)
            if "SteamLaunch" in c:
                m = re.search(r"AppId=(\d+)", c)
                if m and m.group(1) != "0":
                    return f"a Steam game is running (AppId {m.group(1)})"
        return None

    def fullscreen(self):
        if not self.env.get("HYPRLAND_INSTANCE_SIGNATURE"):
            return None
        rc, out = self.run(["hyprctl", "-j", "activewindow"])
        if rc != 0:
            return None
        try:
            w = json.loads(out or "{}")
        except ValueError:
            return None
        if isinstance(w, dict) and w.get("fullscreen") not in (None, 0, False):
            return f"a fullscreen window ({w.get('class') or w.get('title') or 'unnamed'})"
        return None

    def gpu_procs(self):
        """[(pid, MiB)] of every process on the NVIDIA card, compute and graphics."""
        rc, out = self.run(["nvidia-smi"])
        if rc != 0:
            return []
        got = []
        for m in re.finditer(r"^\|\s+\d+\s+\S+\s+\S+\s+(\d+)\s+(?:C|G|C\+G)\s+.+?\s+(\d+)MiB\s*\|", out, re.M):
            got.append((int(m.group(1)), int(m.group(2))))
        return got

    def gpu(self):
        for pid, mib in self.gpu_procs():
            if pid in self.own or mib < self.min_mib:
                continue
            c = self._cmdline(pid)
            if not c:
                continue                           # gone, or not ours to read
            if not any(rx.search(c) for rx in self.allow):
                name = os.path.basename(c.split(" ", 1)[0]) or str(pid)
                return f"{name} is using the graphics card ({mib} MiB)"
        return None

    def check(self):
        """The first sign found, as a short phrase, or None."""
        for sign in (self.gamemode, self.steam, self.fullscreen, self.gpu):
            try:
                why = sign()
            except Exception:                      # a sign that can't be read is no sign
                why = None
            if why:
                return why
        return None


if __name__ == "__main__":
    print(GameWatch().check() or "no game")
