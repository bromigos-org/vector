"""Reduced motion for the holograms (bromigOS docs/accessibility.md): how much VECTOR's
construct, his console and the model gallery move.

The setting is the user's, `motion` in ~/.config/bromigos/accessibility.toml (`bromigos
accessibility motion full|reduced|still`; $XDG_CONFIG_HOME is honoured). When that file has
no valid `motion`, the theme's palette.json says it (`bromigos theme set` renders it there
for the apps that animate: bromigos_theme.motion()); with neither, full. The same file and
words as bromigOS's live layer (live/motion.py), so the desktop moves as one.

  full     everything moves as designed.
  reduced  calm: the same holograms, their clock at RATE (0.4) of real time, so spins,
           sways and drifts go slower; no flicker (the light cone, the spark, the parts'
           shimmer, the alarm's crackle), no blinking (his iris, pulsing notices), no scan
           band sweeping a model or the table, no glitch at a voice change, no random
           twitches; springs settle without overshoot. What you drive (a drag, an
           explode, a click) moves as designed, and fades stay fades.
  still    all of reduced, and each window holds one frame: the clock stands, every
           transition lands at once, text shows whole, and the picture is drawn again only
           when what it shows changes (his state or mood, a message, the model, a reading,
           the theme). No frame timer runs: a window asks its scene four times a second
           whether its picture changed (still_sig()), which costs no drawing.

The setting is followed at run time: current() stats the file at most once a second.
"""
import os
import time
import tomllib

MODES = ("full", "reduced", "still")
RATE = {"full": 1.0, "reduced": 0.4, "still": 0.0}
STILL_CHECK_MS = 250        # how often a still window asks whether its picture changed


def path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "bromigos", "accessibility.toml")


def _palette():
    """The theme's palette.json's `motion` (None when it has none, or no theme lib)."""
    try:
        from .render import T
        T.version(0.0)                       # palette.json as it is now
        m = T.motion() if hasattr(T, "motion") else None
    except Exception:
        return None
    return m if m in MODES else None


def read(file=None):
    """The motion setting now: accessibility.toml's, else palette.json's, else full."""
    try:
        with open(file or path(), "rb") as f:
            m = tomllib.load(f).get("motion")
        if m in MODES:
            return m
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        pass
    return _palette() or "full"


_cur = {"mode": None, "sig": None, "at": 0.0}


def _sig():
    try:
        st = os.stat(path())
        a = (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        a = None
    try:
        from .render import T
        v = T.version()
    except Exception:
        v = None
    return a, v


def current(every=1.0):
    """The mode in force, followed at run time (the file stat'ed at most every `every` s)."""
    now = time.monotonic()
    if _cur["mode"] is None or now - _cur["at"] >= every:
        _cur["at"] = now
        s = _sig()
        if s != _cur["sig"] or _cur["mode"] is None:
            _cur["sig"] = s
            _cur["mode"] = read()
    return _cur["mode"]


def forget():
    """Read the setting again on the next current() (tests; a socket command)."""
    _cur["mode"] = None


def calm(mode):
    """Anything but full motion: no flicker, blinking, scan bands or glitches."""
    return mode != "full"


class Clock:
    """A scene's animation clock: real seconds in, the scene's seconds out. Full: the same;
    reduced: RATE times slower; still: standing. Changing the mode keeps the time where it
    is (no jump), so a spin slows down rather than skipping."""

    def __init__(self):
        self.t = None
        self.last = None

    def __call__(self, real, mode="full"):
        rate = RATE.get(mode, 1.0)
        if self.t is None:
            self.t = real * rate if rate else 0.0
        else:
            self.t += max(0.0, real - self.last) * rate
        self.last = real
        return self.t
