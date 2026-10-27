#!/usr/bin/env python3
"""Checks that VECTOR speaks with his real voice, and that a projector contact talks, without
a GPU (no GL context is made; the parts checked are plain Python):

  - the portrait stream's `voice` command is read and clamped, and bad values are ignored;
  - while the voice keeps coming, his speaking level follows it; once it stops (0.3 s), he
    falls back to the talking cadence;
  - the projector passes the voice on only while it's fresh;
  - a contact with `talk` speaking talks for its `for` seconds, all the time without one,
    never without `talk`; and a talking contact draws at a moving rate.

    python3 tools/test-talk.py        # exit 1 on a failure
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "holo"))

import portrait  # noqa: E402

failures = []


def check(name, ok):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        failures.append(name)


c = portrait.Commands()
c.take("voice 0.8")
check("voice is read", abs(c.voice - 0.8) < 1e-9 and c.voice_at > 0)
c.take("voice 7")
check("voice is clamped to 1", c.voice == 1.0)
c.take("voice loud")
check("a bad voice is ignored", c.voice == 1.0)

c = portrait.Commands()
c.take("state speaking")
now = time.monotonic()
c.voice, c.voice_at = 0.0, now
quiet = portrait.speaking_level(c, 0.3, now)
c.voice, c.voice_at = 1.0, now
loud = portrait.speaking_level(c, 0.3, now)
check("speaking follows a fresh voice", quiet < 0.2 and loud > 0.95)
stale = portrait.speaking_level(c, 0.3, now + 1.0)
cadence = 0.35 + 0.35 * abs(__import__("math").sin(0.3 * 9.0)) * (0.6 + 0.4 * __import__("math").sin(0.3 * 1.7))
check("a stale voice falls back to the cadence", abs(stale - cadence) < 1e-9)
check("the projector gets the voice while fresh", portrait.voice_now(c, now) == 1.0)
check("and nothing once it's stale", portrait.voice_now(c, now + 1.0) is None)

try:
    from holo.projector import Projector  # needs OpenGL's Python bindings, not a context
except Exception as e:  # noqa: BLE001
    print(f"skip projector checks ({e})")
else:
    p = Projector.__new__(Projector)
    p.since = time.monotonic()
    p.voice = None
    p.spec = {"kind": "contact", "who": "Sam"}
    check("a contact without talk doesn't talk", not p.talking())
    p.spec = {"kind": "contact", "who": "Sam", "talk": {"state": "speaking"}}
    check("a speaking contact talks", p.talking())
    p.spec = {"kind": "contact", "who": "Sam", "talk": {"state": "speaking", "for": 2.0}}
    check("for its seconds", p.talking(p.since + 1.0) and not p.talking(p.since + 2.5))
    p.since = time.monotonic() - 10
    p.spec = {"kind": "contact", "who": "Sam", "talk": {"state": "speaking"}}
    check("a talking contact draws at a moving rate", p.rate(30) == 15)
    p.voice = 0.7
    check("the projector's voice is the caller's", abs(p.voice_level(1.0) - 0.7) < 1e-9)
    p.voice = None
    check("else a cadence", 0.3 <= p.voice_level(1.0) <= 0.75)

sys.exit(1 if failures else 0)
