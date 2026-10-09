"""VECTOR's hologram: an original construct of relay light, drawn by the same renderer
as the models (a part-indexed mesh; every moving piece is a part with its own matrix).

    bezel     a twelve-sided dial bezel with notches, a little depth to it
    blades    six aperture blades, each pivoting on the bezel: the iris opens wide to
              listen, narrows to think, pulses when it talks, and blinks
    core      a small faceted diamond behind the aperture: the lamp at the heart of the line
    gimbals   two tilted tuning-dial rings that spin up when it is busy
    whips     three thin whip antennae out of the back, on springs: they perk up,
              droop, twitch and shiver; most of the personality lives here
    spark     a tiny mote above that flickers with activity

Gaze is a spring with low damping, so every glance overshoots a little and settles:
eager and twitchy. State colours: idle phosphor, listening soft, thinking amber,
speaking bright phosphor, error danger.

Reduced motion (holo/motion.py; the scene passes the mode to update()): reduced runs his
idle animation at RATE of real time, with no blinks, no spark flicker, no random twitches,
saccade jitter or alarm crackle, no glitch at a voice change, and springs damped so
nothing overshoots; still holds him in his state's pose (every spring lands at once, no
saccades), redrawn only when his state, mood or voice changes. His colours always follow
his state at once: they say what he is doing.
"""
import math
import random

import numpy as np

from .. import gl
from ..motion import RATE
from ..render import GpuModel, T, lin

R = 0.2          # bezel radius (world units; the construct floats about 0.55 above the table)
NBLADE = 6
P_BEZEL, P_CORE, P_GIM_A, P_GIM_B, P_SPARK = 0, 1, 2, 3, 4
P_BLADE0 = 5
P_WHIP0 = P_BLADE0 + NBLADE            # 11, 12, 13
NPARTS = P_WHIP0 + 3


class _Build:
    def __init__(self):
        self.pos, self.nrm, self.part, self.tri, self.edge = [], [], [], [], []

    def v(self, p, part, n=(0, 0, 1)):
        self.pos.append(p)
        self.nrm.append(n)
        self.part.append(part)
        return len(self.pos) - 1

    def poly(self, pts, part, closed=True, z=0.0):
        ids = [self.v((x, y, z), part) for x, y in pts]
        for a, b in zip(ids, ids[1:] + ([ids[0]] if closed else [])):
            self.edge.append((a, b))
        return ids

    def fan(self, ids):
        for k in range(1, len(ids) - 1):
            self.tri.append((ids[0], ids[k], ids[k + 1]))

    def seg(self, a, b, part):
        i, j = self.v(a, part), self.v(b, part)
        self.edge.append((i, j))

    def solid(self, verts, faces, part, centre=(0, 0, 0)):
        c = np.asarray(centre, np.float32)
        base = len(self.pos)
        for p in verts:
            n = np.asarray(p, np.float32) - c
            self.v(tuple(p), part, tuple(n / (np.linalg.norm(n) + 1e-9)))
        es = set()
        for f in faces:
            self.tri.append(tuple(base + k for k in f))
            for a, b in zip(f, f[1:] + f[:1]):
                es.add((min(a, b) + base, max(a, b) + base))
        self.edge += list(es)


def _octa(c, rx, ry, rz):
    cx, cy, cz = c
    v = [(cx + rx, cy, cz), (cx - rx, cy, cz), (cx, cy + ry, cz), (cx, cy - ry, cz), (cx, cy, cz + rz), (cx, cy, cz - rz)]
    f = [[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4], [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]]
    return v, f


def build():
    b = _Build()
    # bezel: front and back dodecagons, joined at the corners, with notches between
    for z in (0.012, -0.03):
        out = [(R * math.cos(a), R * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 13)[:-1] + math.pi / 12]
        ids = b.poly(out, P_BEZEL, z=z)
        inner = [(0.86 * R * math.cos(a), 0.86 * R * math.sin(a)) for a in np.linspace(0, 2 * math.pi, 13)[:-1] + math.pi / 12]
        ids2 = b.poly(inner, P_BEZEL, z=z)
        if z > 0:
            for k in range(12):   # face fill between rings, for depth and the shell
                a0, a1 = ids[k], ids[(k + 1) % 12]
                c0, c1 = ids2[k], ids2[(k + 1) % 12]
                b.tri += [(a0, a1, c1), (a0, c1, c0)]
    for k in range(12):
        a = k * math.pi / 6 + math.pi / 12
        b.seg((R * math.cos(a), R * math.sin(a), 0.012), (R * math.cos(a), R * math.sin(a), -0.03), P_BEZEL)
        a2 = a + math.pi / 12
        L = 0.06 if k % 3 == 0 else 0.035
        b.seg((R * 1.02 * math.cos(a2), R * 1.02 * math.sin(a2), 0.0), ((R * 1.02 + L * R) * math.cos(a2), (R * 1.02 + L * R) * math.sin(a2), 0.0), P_BEZEL)
    # aperture blades, drawn open; each pivots about its own outer corner
    for k in range(NBLADE):
        a = k * 2 * math.pi / NBLADE
        pts = []
        for t in np.linspace(-0.62, 0.62, 7):          # outer arc
            pts.append((0.84 * R * math.cos(a + t), 0.84 * R * math.sin(a + t)))
        for t in np.linspace(0.62, -0.62, 7):          # inner edge: a shallow arc
            r = 0.52 * R + 0.06 * R * (t * t)
            pts.append((r * math.cos(a + t * 0.85), r * math.sin(a + t * 0.85)))
        ids = b.poly(pts, P_BLADE0 + k, z=0.004 + 0.0015 * k)
        b.fan(ids)
    # core: a flattened diamond, faceted
    v, f = _octa((0, 0, -0.012), 0.05, 0.05, 0.03)
    b.solid(v, f, P_CORE, (0, 0, -0.012))
    # gimbals: tilted rings with eleven ticks (a tuning dial)
    for p, rr in ((P_GIM_A, 1.32), (P_GIM_B, 1.5)):
        n = 72
        ring = [(rr * R * math.cos(a), rr * R * math.sin(a)) for a in np.linspace(0, 2 * math.pi, n + 1)[:-1]]
        ids = [b.v((x, 0.0, y), p, (0, 1, 0)) for x, y in ring]    # in the XZ plane, tilted later
        for k in range(n):
            if (k // 6) % 4 != 3:                                  # dashed: three on, one off
                b.edge.append((ids[k], ids[(k + 1) % n]))
        for k in range(11):
            a = math.pi / 2 + (k - 5) * 0.09
            L = 0.07 if k == 5 else 0.035
            b.seg((rr * R * math.cos(a), 0.0, rr * R * math.sin(a)),
                  ((rr * R + L * R) * math.cos(a), 0.0, (rr * R + L * R) * math.sin(a)), p)
    # spark: a tiny mote
    v, f = _octa((0, 0, 0), 0.016, 0.026, 0.016)
    b.solid(v, f, P_SPARK)
    # whips: a thin stalk, a kink, a bead; built pointing up from the origin (pivot)
    for w in range(3):
        p = P_WHIP0 + w
        L = 0.19 + 0.04 * (w == 1)
        b.seg((0, 0, 0), (0, L * 0.55, 0), p)
        b.seg((0, L * 0.55, 0), (0.012 * (1 if w != 1 else -1), L, 0), p)
        v, f = _octa((0.012 * (1 if w != 1 else -1), L + 0.012, 0), 0.01, 0.014, 0.01)
        b.solid(v, f, p, (0.012 * (1 if w != 1 else -1), L + 0.012, 0))
    parts = [{"id": n, "label": n.upper()} for n in
             ["bezel", "core", "gimbal_a", "gimbal_b", "spark"] + [f"blade{k}" for k in range(NBLADE)] + ["whip0", "whip1", "whip2"]]
    return (np.array(b.pos, np.float32), np.array(b.nrm, np.float32), np.array(b.part, np.uint8),
            np.array(b.tri, np.uint32).reshape(-1, 3), np.array(b.edge, np.uint32).reshape(-1, 2), parts)


class Spring:
    """Under-damped spring toward a target (per component). zeta is set per frame under
    reduced motion (no overshoot); land() puts it at its target (still)."""

    def __init__(self, x=0.0, k=60.0, zeta=0.42):
        self.x = np.atleast_1d(np.asarray(x, np.float64)).copy()
        self.v = np.zeros_like(self.x)
        self.t = self.x.copy()
        self.k, self.zeta = k, zeta

    def step(self, dt):
        w = math.sqrt(self.k)
        n = max(1, int(dt / 0.008) + 1)
        h = dt / n
        for _ in range(n):
            a = self.k * (self.t - self.x) - 2 * self.zeta * w * self.v
            self.v += a * h
            self.x += self.v * h
        return self.x

    def land(self):
        self.x[:] = self.t
        self.v[:] = 0.0
        return self.x


# where he looks when still (no saccades): straight out, or down when asleep
REST_GAZE = {"sleep": (0.0, 0.35), "listening": (0.0, -0.04)}
ZETA = {"gaze": 0.38, "lean": 0.5, "aperture": 0.55, "whip": 0.25}      # full motion's
CALM_ZETA = 0.95                                                         # reduced: no overshoot

STATE_COL = {"idle": "phosphor", "listening": "soft", "thinking": "amber", "speaking": "phosphor",
             "error": "danger", "sleep": "dim"}
VOICE_STATES = ("idle", "speaking")       # states that wear the speaking voice's colour


def hex_lin(h, k=1.0):
    """A Wick #rrggbb, in the theme in force, in linear light."""
    h = T.retint_hex(h).lstrip("#")
    return np.array([((int(h[i:i + 2], 16) / 255.0) ** 2.2) * k for i in (0, 2, 4)], np.float32)


def signals():
    """danger, rust and amber (the moods' colours) in the theme in force."""
    return lin("danger"), lin("rust"), lin("amber")


class Avatar:
    def __init__(self):
        self.geom = build()
        self.gpu = None
        self.state = "idle"
        self.state_t = 0.0
        self.t = 0.0
        self.gaze = Spring([0.0, 0.0], k=90, zeta=0.38)        # yaw, pitch
        self.lean = Spring([0.0, 0.0, 0.0], k=30, zeta=0.5)    # x, y, roll
        self.aperture = Spring([0.7], k=140, zeta=0.55)
        self.whips = [Spring([0.0, 0.0], k=55, zeta=0.25) for _ in range(3)]
        self.spin_a = self.spin_b = 0.0
        self.next_saccade = 0.0
        self.next_blink = 2.0
        self.blink = -1.0
        self.level = 0.0          # mic level or voice amplitude, 0..1
        self.activity = 0.0       # recent typing/streaming, 0..1
        self.look_at = None       # (yaw, pitch) to hold, e.g. toward an exhibit
        self.look_until = 0.0
        self.flinch = 0.0
        # colour: the voice tint crossfades (~300 ms) on a voice change; the mood blends over it
        self.tint = hex_lin("#39ff14")
        self.tint_to = self.tint.copy()
        self.glitch = 0.0                    # 1 at a voice switch, gone in ~0.3 s
        self.mood = {"excited": 0.0, "concerned": 0.0, "alarmed": 0.0}
        self.mood_to = dict(self.mood)
        self.crackle = np.ones(32, np.float32)
        self.next_crackle = 0.0
        self.mode = "full"                   # the motion mode of the last update() (holo/motion.py)

    def set_voice_colour(self, hexcol, switch=True):
        self.tint_to = hex_lin(hexcol)
        if switch:
            self.glitch = 1.0

    def set_mood(self, mood):
        """calm | excited | concerned | alarmed: eases in over ~400 ms."""
        for k in self.mood_to:
            self.mood_to[k] = 1.0 if k == mood else 0.0
        if mood == "alarmed":
            self.flinch = max(self.flinch, 0.6)

    def calm_down(self):
        for k in self.mood_to:
            self.mood_to[k] = 0.0

    def mood_name(self):
        k = max(self.mood, key=self.mood.get)
        return k if self.mood[k] > 0.25 else "calm"

    def colour(self):
        """The construct's current colour (linear rgb) and the edge colour."""
        base = self.tint if self.state in VOICE_STATES else lin(STATE_COL.get(self.state, "phosphor"))
        c = np.asarray(base, np.float32)
        DANGER, RUST, AMBER = (np.asarray(v, np.float32) for v in signals())
        e, cn, a = self.mood["excited"], self.mood["concerned"], self.mood["alarmed"]
        if cn > 0:   # desaturate toward amber, keeping a trace of the voice
            grey = np.full(3, c.mean(), np.float32)
            c = c * (1 - 0.5 * cn) + (0.4 * grey + 0.6 * AMBER) * 0.5 * cn
        edge = c.copy()
        if a > 0:    # flush toward danger, edges to deep rust; the voice hue stays visible
            c = c * (1 - 0.8 * a) + DANGER * 0.8 * a      # a fifth of the voice hue survives
            edge = edge * (1 - 0.85 * a) + (RUST * 4.0) * 0.85 * a
        c = c * (1 + 0.3 * e)
        edge = edge * (1 + 0.3 * e)
        return c, edge

    def set_state(self, s):
        if s != self.state:
            self.state, self.state_t = s, 0.0
            full = self.mode == "full"           # reduced motion: no jolts, no shakes
            if s == "error":
                self.flinch = 1.0 if full else 0.0
                if full:
                    for w in self.whips:
                        w.v += np.array([random.uniform(-9, 9), random.uniform(-6, 6)])
            if s == "listening":       # perk up: a quick double-take toward the operator
                self.gaze.t[:] = (0.0, -0.05)
                if full:
                    for k, w in enumerate(self.whips):
                        w.v += np.array([(k - 1) * 4.0, 7.0])

    def glance(self, yaw, pitch, hold=1.6):
        self.look_at = (yaw, pitch)
        self.look_until = self.t + hold

    def update(self, dt, mode="full"):
        """Advance dt real seconds under the motion mode (holo/motion.py)."""
        self.mode = mode
        calm, still = mode != "full", mode == "still"
        adt = dt * RATE.get(mode, 1.0)       # the animation's seconds (reduced: slower; still: none)
        self.t += adt
        self.state_t += dt                   # time in this state stays real: the scene's timing reads it
        st, t = self.state, self.t
        r = random.random
        for sp, z in ((self.gaze, ZETA["gaze"]), (self.lean, ZETA["lean"]), (self.aperture, ZETA["aperture"])):
            sp.zeta = CALM_ZETA if calm else z
        for w in self.whips:
            w.zeta = CALM_ZETA if calm else ZETA["whip"]
        # gaze: saccades, with a held glance taking priority (still: his state's resting gaze)
        if still:
            self.gaze.t[:] = REST_GAZE.get(st, (0.0, 0.0))
        elif self.look_at and t < self.look_until:
            self.gaze.t[:] = self.look_at
        elif t >= self.next_saccade:
            if st == "thinking":
                self.gaze.t[:] = (r() * 0.9 - 0.45, r() * 0.5 - 0.3)
                self.next_saccade = t + 0.12 + r() * 0.35
            elif st == "listening":
                self.gaze.t[:] = (r() * 0.12 - 0.06, -0.04 + r() * 0.06)
                self.next_saccade = t + 0.5 + r() * 0.9
            elif st == "speaking":
                self.gaze.t[:] = (r() * 0.3 - 0.15, r() * 0.14 - 0.08)
                self.next_saccade = t + 0.4 + r() * 1.0
            elif st == "sleep":
                self.gaze.t[:] = (0.0, 0.35)
                self.next_saccade = t + 4.0
            else:
                big = r() < 0.25
                self.gaze.t[:] = ((r() - 0.5) * (1.3 if big else 0.5), (r() - 0.5) * (0.6 if big else 0.25))
                self.next_saccade = t + 0.5 + r() * 2.2
        self.gaze.land() if still else self.gaze.step(adt)
        # colour and mood easing: tint ~300 ms, mood in ~400 ms, out slowly (decay set by the scene)
        fdt = dt * (0.4 if calm else 1.0)   # reduced: colours fade in more gently (still lands below)
        self.tint = self.tint_to + (self.tint - self.tint_to) * math.exp(-11.0 * fdt)
        for k in self.mood:
            rate = 7.5 if self.mood_to[k] > self.mood[k] else 0.22
            self.mood[k] = self.mood_to[k] + (self.mood[k] - self.mood_to[k]) * math.exp(-rate * fdt)
        if still:                            # a held frame: colours and moods where they're going
            self.tint = self.tint_to.copy()
            self.mood = dict(self.mood_to)
        self.glitch = 0.0 if calm else max(0.0, self.glitch - dt / 0.3)
        alarm = self.mood["alarmed"]
        if calm:                             # no crackle: the alarm shows in colour and pace
            self.crackle[:] = 1.0
        elif alarm > 0.05 and t >= self.next_crackle:   # static crackle: steps at ~4 Hz, never a strobe
            self.crackle = (1.0 - 0.35 * alarm * np.random.random(32) ** 3).astype(np.float32)
            self.next_crackle = t + 0.22 + 0.12 * r()
        elif alarm <= 0.05:
            self.crackle[:] = 1.0
        # aperture
        if st == "listening":
            ap = 0.95 + 0.05 * self.level
        elif st == "thinking":
            ap = 0.32 + (0.03 * math.sin(t * 13) if calm else 0.06 * math.sin(t * 13) + 0.04 * r())
        elif st == "speaking":
            ap = 0.5 + 0.45 * self.level
        elif st == "error":
            ap = 0.15
        elif st == "sleep":
            ap = 0.12
        else:
            ap = 0.68 + 0.05 * math.sin(t * 0.7)
        if not calm and st not in ("sleep",) and t >= self.next_blink:   # reduced motion: no blinking
            self.blink, self.next_blink = t, t + 2.5 + r() * 5.5
            if r() < 0.2:
                self.next_blink = t + 0.28          # a nervous double blink
        if calm:
            self.blink = -1
        if self.blink >= 0:
            b = (t - self.blink) / 0.22
            if b < 1:
                ap *= abs(2 * b - 1) ** 0.6
            else:
                self.blink = -1
        if alarm > 0.05:                       # harder, faster iris (calm: narrower, no flutter)
            ap = ap * (1 - 0.35 * alarm) + (0.0 if calm else 0.08 * alarm * math.sin(t * 9.0))
        self.aperture.k = 140 * (1 + 1.6 * alarm)
        self.aperture.t[0] = ap
        self.aperture.land() if still else self.aperture.step(adt)
        # gimbals
        rate = {"thinking": 3.4, "listening": 1.1, "speaking": 1.0 + 2.0 * self.level, "error": 0.2, "sleep": 0.08}.get(st, 0.35)
        rate *= 1 + 0.6 * self.mood["excited"] + 1.2 * alarm
        self.spin_a += rate * adt
        self.spin_b -= rate * 0.73 * adt
        # whips: idle sway, listening perk, thinking twitch, speaking bob, error shiver
        for k, w in enumerate(self.whips):
            spread = (k - 1) * 0.42
            if st == "listening":
                w.t[:] = (spread * 0.8, 0.35)
            elif st == "thinking":
                w.t[:] = (spread + 0.12 * math.sin(t * 5 + k), 0.1)
                if not calm and r() < dt * 2.2:
                    w.v += np.array([(r() - 0.5) * 14, (r() - 0.5) * 8])
            elif st == "speaking":
                w.t[:] = (spread + 0.08 * math.sin(t * 3 + k), 0.18 + 0.25 * self.level)
            elif st == "error":
                w.t[:] = (spread * 1.7, -0.25)
                if not calm:
                    w.v += np.array([(r() - 0.5) * 4, (r() - 0.5) * 4]) * min(1, self.flinch * 3)
            elif st == "sleep":
                w.t[:] = (spread * 1.3, -0.6)
            else:
                w.t[:] = (spread + 0.1 * math.sin(t * 0.9 + k * 2), -0.08 + 0.06 * math.sin(t * 0.6 + k))
            w.land() if still else w.step(adt)
        # body: bob, nervous jitter, puzzled roll on errors
        jit = 0.004 if st == "thinking" and not calm else 0.0
        self.lean.t[:] = (jit * (r() - 0.5) if jit else 0.0, 0.012 * math.sin(t * 1.3) + (-0.04 if st == "sleep" else 0.0),
                          (0.0 if calm else 0.22 * self.flinch * math.sin(t * 2.0)) + (0.12 if st == "error" else 0.0))
        self.lean.land() if still else self.lean.step(adt)
        self.flinch = max(0.0, self.flinch - dt * 0.6)
        self.activity = max(0.0, self.activity - dt * 1.5)

    def part_state(self, hover_y=0.55):
        """Matrices (part -> local), colours, widths. Local frame: lens at origin, facing +z."""
        n = NPARTS
        pm = np.tile(np.eye(4, dtype=np.float32), (n, 1, 1))
        yaw, pitch = self.gaze.x
        lx, ly, roll = self.lean.x
        head = gl.translate(lx, hover_y + ly, 0) @ gl.rot_z(roll) @ gl.rot_y(yaw) @ gl.rot_x(pitch)
        ap = float(np.clip(self.aperture.x[0], 0.02, 1.1))
        pm[P_BEZEL] = head
        pm[P_CORE] = head @ gl.rot_z(self.t * 0.6) @ gl.scale(0.8 + 0.4 * ap)
        for k in range(NBLADE):
            a = k * 2 * math.pi / NBLADE
            piv = np.array([0.84 * R * math.cos(a + 0.62), 0.84 * R * math.sin(a + 0.62), 0.0], np.float32)
            close = (1.0 - ap) * 0.62
            pm[P_BLADE0 + k] = head @ gl.rot_z(self.spin_a * 0.15 if self.state == "thinking" else 0.0) @ \
                gl.translate(*piv) @ gl.rot_z(close) @ gl.translate(*(-piv))
        body = gl.translate(lx, hover_y + ly, -0.01)
        pm[P_GIM_A] = body @ gl.rot_x(1.1) @ gl.rot_z(0.3) @ gl.rot_y(self.spin_a)
        pm[P_GIM_B] = body @ gl.rot_x(-0.55) @ gl.rot_z(-0.5) @ gl.rot_y(self.spin_b)
        calm = self.mode != "full"
        act = 0.0 if self.mode == "still" else self.activity     # still: no decaying glow between frames
        fl = 0.85 if calm else 0.85 + 0.15 * math.sin(self.t * 23) * math.sin(self.t * 7.3)   # calm: the spark holds
        pm[P_SPARK] = gl.translate(lx, hover_y + ly + R * 1.75 + 0.01 * math.sin(self.t * 2.1), 0) @ \
            gl.rot_y(self.t * 1.7) @ gl.scale(fl * (1.0 + 0.6 * max(act, self.level)))
        for k, w in enumerate(self.whips):
            side, up = w.x
            base = np.array([(k - 1) * 0.06, R * 0.62, -0.035], np.float32)
            pm[P_WHIP0 + k] = head @ gl.translate(*base) @ gl.rot_z(-side) @ gl.rot_x(-0.35 - up * 0.6)
        c, edge = self.colour()
        g = 0.0 if calm else self.glitch
        if g > 0:   # the voice switch: a brief tremor of the iris, timed with the scratch
            j = 0.06 * g * math.sin(self.t * 60.0)
            for k in range(NBLADE):
                pm[P_BLADE0 + k] = pm[P_BLADE0 + k] @ gl.rot_z(j * (1 if k % 2 else -1))
        boost = {"speaking": 1.25 + 0.5 * self.level, "listening": 1.15 + 0.4 * self.level,
                 "thinking": 1.1, "sleep": 0.45}.get(self.state, 1.0)
        pc = np.zeros((n, 4), np.float32)
        pw = np.ones(n, np.float32)
        for p in range(n):
            k = boost
            if p == P_CORE:
                k *= 2.4 + 1.5 * self.level
                pw[p] = 1.6
            elif p == P_SPARK:
                k *= 1.8 * fl + 1.5 * act
            elif p in (P_GIM_A, P_GIM_B):
                k *= 0.55 if self.state != "thinking" else 0.9
                pw[p] = 0.9
            elif p >= P_BLADE0 and p < P_WHIP0:
                k *= 0.9
            elif p >= P_WHIP0:
                k *= 0.95
                pw[p] = 1.1
            else:
                pw[p] = 1.35
            col = edge if p in (P_BEZEL, P_GIM_A, P_GIM_B) or p >= P_WHIP0 else c
            k *= self.crackle[p % 32] * (1.0 - 0.3 * g * (0.5 + 0.5 * math.sin(self.t * 75.0 + p)))
            pc[p] = (col[0] * k, col[1] * k, col[2] * k, 1.0)
        return pm, pc, pw

    def draw(self, holo, base, fade=1.0):
        if self.gpu is None:
            pos, nrm, part, tri, edge, parts = self.geom
            self.gpu = GpuModel(pos, nrm, part, tri, edge, parts)
        pm, pc, pw = self.part_state()
        pc[:, 3] *= fade
        holo.draw_model(self.gpu, base, pm, pc, pw, scan=(0, 0.02, 0), fill=0.8, width=1.5, slices=0.0, ghost=0.18)
        # a thin beam from the emitter up to the construct
        c = tuple(self.colour()[0] * 0.35 * fade)
        ys = np.linspace(0.02, 0.4, 2)
        holo.draw_lines3d(np.array([[0, ys[0], 0], [0, ys[1], 0]], np.float32),
                          np.array([[*c, 0.6], [*c, 0.0]], np.float32), base, width=2.0)

    def release(self):
        if self.gpu:
            self.gpu.delete()
            self.gpu = None
