#!/usr/bin/env python3
"""Tests for what VECTOR publishes about his voices in his state file
($XDG_RUNTIME_DIR/bromigos-vector.json: `voices`, `voice_colour`), which bromigOS's
keyboard module reads (its dial steps through the voices, its lighting takes the colour).
No GL, no desktop: the scene's methods run on a stand-in, with a throwaway config.

    python3 tools/test-state.py

1. `voices` keeps voice.json's order and gives each voice a role, a name (its own, else
   the role) and a #rrggbb colour.
2. The colours are as the construct shows them: the theme's retint of voice.json's.
3. An unknown role falls back to the default phosphor, retinted, as set_voice() does.
"""
import json
import os
import sys
import tempfile
import types

tmp = tempfile.mkdtemp(prefix="vector-state-")
os.environ.update(HOME=tmp, XDG_CONFIG_HOME=os.path.join(tmp, "config"),
                  XDG_STATE_HOME=os.path.join(tmp, "state"), XDG_RUNTIME_DIR=os.path.join(tmp, "run"))
for k in ("WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "DISPLAY"):
    os.environ.pop(k, None)
os.makedirs(os.path.join(tmp, "config/bromigos/vector"))
with open(os.path.join(tmp, "config/bromigos/vector/voice.json"), "w") as f:
    json.dump({"voices": {"main": {"name": "caretaker", "colour": "#39ff14"},
                          "robot": {"colour": "#ff766f"},
                          "notify": {"name": "bell", "colour": "#d4af37"}}}, f)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from holo.vector import scene  # noqa: E402

bad = 0


def ok(cond, what, detail=""):
    global bad
    print(f"{'pass' if cond else 'FAIL'}  {what}" + (f" — {detail}" if detail else ""))
    bad += 0 if cond else 1


s = types.SimpleNamespace()
for name in ("_voices", "voice_hex", "voices"):
    setattr(s, name, types.MethodType(getattr(scene.VectorScene, name), s))
v = s.voices()
ok([x["role"] for x in v] == ["main", "robot", "notify"], "voices keep voice.json's order", str(v))
ok([x["name"] for x in v] == ["caretaker", "robot", "bell"], "each voice is named (the role when it has no name)")
ok(all(isinstance(x["colour"], str) and len(x["colour"]) == 7 and x["colour"][0] == "#" for x in v),
   "each colour is #rrggbb")
ok(v[1]["colour"] == scene.T.retint("#ff766f"), "colours are the theme's retint, as the construct shows them")
ok(s.voice_hex("nobody") == scene.T.retint("#39ff14"), "an unknown voice gets the default phosphor, retinted")
print("all passed" if not bad else f"{bad} failed")
sys.exit(1 if bad else 0)
