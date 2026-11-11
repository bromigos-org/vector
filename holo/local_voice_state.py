"""The local voice's setting and status, readable without starting VECTOR (standard library only).

The setting: ~/.local/state/bromigos/vector-local-voice holds "on" or "off"; no file means auto.
Auto is on when this machine has an NVIDIA card with room for the voice and a game beside it
(at least `min_vram_mib` of memory, 8 GiB by default), and off elsewhere. bromigos-holo
local-voice on|off|auto writes it, and Settings › VECTOR flips it.

The status: the voice server writes $XDG_RUNTIME_DIR/bromigos-vector-voice.json whenever where
he speaks from changes: {"speaking_from", "local": {"state", "why"}, "updated"}. `status` turns
it into one plain line, the same one Settings shows.

Run: python3 -I holo/local_voice_state.py status [--json] | on | off | auto
"""
import json
import os
import subprocess
import sys
import time

STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "bromigos")
SETTING = os.path.join(STATE, "vector-local-voice")
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
STATUS = os.environ.get("BROMIGOS_VOICE_STATUS") or os.path.join(RUNTIME, "bromigos-vector-voice.json")
MIN_VRAM_MIB = 8192

# where he speaks from, in words (the voice server's engine names)
WHERE = {
    "local": "this computer (Qwen3-TTS 0.6B on the graphics card)",
    "lab": "the lab",
    "qwen": "this computer (Qwen3-TTS 1.7B on the graphics card)",
    "kokoro": "this computer's processor (Kokoro, a stand-in voice)",
    "breeze": "the lab (Breeze)",
    "fish": "Fish Audio",
}
LOCAL_STATE = {
    "ready": "ready on the graphics card",
    "starting": "starting",
    "stopped": "not loaded (it starts when he next speaks)",
    "away": "stepped aside",
    "preparing": "getting ready",
    "failed": "failed",
    "off": "off",
    "unavailable": "not available here",
}


def setting():
    """"on", "off" or "auto"."""
    try:
        with open(SETTING) as f:
            v = f.read().strip().lower()
        return v if v in ("on", "off", "auto") else "auto"
    except OSError:
        return "auto"


def set_setting(v):
    os.makedirs(STATE, exist_ok=True)
    if v == "auto":
        try:
            os.unlink(SETTING)
        except FileNotFoundError:
            pass
        return
    tmp = SETTING + ".part"
    with open(tmp, "w") as f:
        f.write(v + "\n")
    os.replace(tmp, SETTING)


def nvidia_cards(run=subprocess.run):
    """[(name, total MiB)] from nvidia-smi; [] without an NVIDIA card or its driver."""
    try:
        r = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    out = []
    for line in r.stdout.splitlines():
        name, _, mib = line.rpartition(",")
        try:
            out.append((name.strip(), int(mib.strip())))
        except ValueError:
            continue
    return out


def _gb(mib):
    return round(mib / 1024)


def enabled(min_vram_mib=MIN_VRAM_MIB, run=subprocess.run):
    """(on, why) for the setting as it applies to this machine."""
    v = setting()
    if v == "off":
        return False, "off (your setting)"
    cards = nvidia_cards(run)
    roomy = [c for c in cards if c[1] >= min_vram_mib]
    if v == "on":
        if not cards:
            return False, "on, but this machine has no NVIDIA card"
        return True, f"on (your setting, {cards[0][0]})"
    if roomy:
        return True, f"on (auto: {roomy[0][0]}, {_gb(roomy[0][1])} GB)"
    if cards:
        return False, f"off (auto: {cards[0][0]} has {_gb(cards[0][1])} GB; it needs {_gb(min_vram_mib)})"
    return False, "off (auto: no NVIDIA card)"


def read_status():
    try:
        with open(STATUS) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_status(doc):
    doc = dict(doc, updated=time.time())
    tmp = STATUS + ".part"
    with open(tmp, "w") as f:
        json.dump(doc, f)
    os.replace(tmp, STATUS)


def where(st=None, on=None):
    """Where he's speaking from right now, in words, with why the local voice isn't speaking."""
    on = enabled() if on is None else on
    st = read_status() if st is None else st
    if not st:
        return "nowhere yet (his voice isn't running)"
    loc = st.get("local") or {}
    out = WHERE.get(st.get("speaking_from"), st.get("speaking_from") or "nowhere yet")
    why = loc.get("why")
    if st.get("speaking_from") != "local" and on[0] and loc.get("state") not in (None, "ready"):
        out += f" (local voice {LOCAL_STATE.get(loc.get('state'), loc.get('state'))}{': ' + why if why else ''})"
    return out


def line(st=None, on=None):
    """The one-line status: the setting, then where he's speaking from right now."""
    on = enabled() if on is None else on
    return f"Local voice: {on[1]}. Speaking from: {where(st, on)}."


def main(argv):
    what = (argv[:1] or ["status"])[0]
    if what in ("on", "off", "auto"):
        set_setting(what)
        print(f"local voice: {what}" + (" (on with an NVIDIA card with room, off elsewhere)" if what == "auto" else ""))
        return 0
    if what == "status":
        on = enabled()
        st = read_status()
        if "--json" in argv:
            print(json.dumps({"setting": setting(), "enabled": on[0], "why": on[1], "status": st,
                              "where": where(st, on), "line": line(st, on)}))
        else:
            print(line(st, on))
        return 0
    print("local-voice [on|off|auto|status [--json]]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
