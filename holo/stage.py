"""A model on the projection table: suit-diagnostic explode/assemble, isolation, the
scan sweep, live-lit parts, callouts with leader lines, and picking.

Used full size by the gallery and small by VECTOR (its "exhibits"). Its scene sets
`motion` (holo/motion.py) every frame: reduced turns it slower, with no shimmer and no scan
band; still holds it (no auto-turn, transitions land at once)."""
import math
import time

import numpy as np

from . import bind, fmt, gl
from .motion import RATE
from .render import GpuModel, LEVEL_COL, approach, col, ease, lin, wick

LEVELS = ["off", "ok", "warn", "crit"]


class Stage:
    def __init__(self, name, live):
        self.name = name
        self.model = fmt.load(name)
        self.meta = self.model.meta
        self.parts = self.model.parts
        self.n = len(self.parts)
        self.live = live
        self.gpu = None
        self.yaw = -0.55
        self.pitch = 0.16
        self.spin = 0.10          # rad/s auto rotation while idle
        self.explode = 0.0
        self.explode_to = 0.0
        self.iso = None           # isolated part index
        self.iso_amt = np.zeros(self.n)
        self.hover = None
        self.born = time.monotonic()
        self.scan_t0 = self.born
        self.scan_dur = 2.4
        self.last_scan = self.born
        self.seen_version = -1
        self.readings = [("off", 0.2, ["reading…"], None)] * self.n
        self.read_at = 0.0
        self.callout_rects = []
        self.dragging = False
        self.anchor_px = None
        self.fade = 0.0           # materialise in / dematerialise out
        self.fade_to = 1.0
        self.motion = "full"      # set by the scene each frame (holo/motion.py)
        self.anim_t = 0.0         # the idle animation's clock (the bob, the table): RATE of real time

    # ------------------------------------------------------------------ data
    def poll(self):
        srcs = bind.sources(self.name, self.parts)
        self.live.want(*srcs)
        now = time.monotonic()
        if now - self.read_at > 0.5:
            self.readings = [bind.reading(p.get("bind") or "", self.live) for p in self.parts]
            self.read_at = now
        v = sum(self.live.sources[s].version for s in srcs)
        if v != self.seen_version:
            if self.seen_version >= 0 and now - self.last_scan > 9.0:
                self.scan()            # fresh readings landed: sweep them in
            self.seen_version = v

    def scan(self):
        self.scan_t0 = self.last_scan = time.monotonic()

    # ------------------------------------------------------------------ animation
    def update(self, dt, interacting=False):
        self.poll()
        rate = RATE.get(self.motion, 1.0)
        self.anim_t += dt * rate
        if not self.dragging and self.iso is None:
            self.yaw += self.spin * dt * rate          # the idle turn: slower, or none when still
        tgt = np.zeros(self.n)
        if self.iso is not None:
            tgt[self.iso] = 1.0
        if self.motion == "still":                     # a held frame: transitions land at once
            self.explode, self.iso_amt, self.fade = self.explode_to, tgt, self.fade_to
            return
        self.explode = approach(self.explode, self.explode_to, dt, 5.0)
        self.iso_amt = tgt + (self.iso_amt - tgt) * math.exp(-7.0 * dt)
        self.fade = approach(self.fade, self.fade_to, dt, 4.0)

    def still_sig(self):
        """What a still frame of this model shows (holo/motion.py): when it changes, draw again."""
        self.poll()
        rd = tuple((lv, round(float(v), 2), tuple(lines)) for lv, v, lines, _ in self.readings)
        return (self.name, round(self.yaw, 4), round(self.pitch, 4), self.explode_to, self.iso, self.hover,
                self.fade_to, rd)

    def model_matrix(self, base=None, size=1.0):
        h = self.meta["height"]
        s = size / max(h, 1.6 * self.meta["radius"], 0.6)
        lift = 0.06 + 0.012 * math.sin(self.anim_t * 0.8)
        m = gl.translate(0, lift, 0) @ gl.rot_y(self.yaw) @ gl.scale(s)
        return (base if base is not None else np.eye(4, dtype=np.float32)) @ m, s

    def part_state(self, t):
        """Per-part matrices, colours and widths for this frame."""
        age = t - self.born
        pm = np.tile(np.eye(4, dtype=np.float32), (self.n, 1, 1))
        pc = np.zeros((self.n, 4), np.float32)
        pw = np.ones(self.n, np.float32)
        any_iso = self.iso_amt.max()
        calm = self.motion != "full"
        for i, p in enumerate(self.parts):
            # materialise: parts fly in from twice their explode offset, staggered (still: there)
            arrive = 1.0 if self.motion == "still" else ease((age - 0.08 * i) / 0.9)
            e = self.explode * (0.7 + 0.3 * ease(self.explode * 1.4 - i * 0.04)) + (1 - arrive) * 1.6
            e += 0.45 * self.iso_amt[i]
            off = np.asarray(p["explode"], np.float32) * e
            # exploded parts tip slightly, like a diagnostic pull-apart
            m = gl.translate(*off)
            if self.explode > 0.01:
                c = np.asarray(p["centroid"], np.float32)
                tip = 0.06 * self.explode * (1 if i % 2 else -1)
                m = m @ gl.translate(*c) @ gl.rot_x(tip * 0.4) @ gl.rot_z(tip) @ gl.translate(*(-c))
            pm[i] = m
            level, v, lines, _ = self.readings[i]
            k = (0.5 + 0.75 * v) * (0.97 if calm else 0.94 + 0.06 * math.sin(t * 9.0 + i * 1.7))   # calm: no shimmer
            if self.hover == i:
                k *= 1.35
            rgb = lin(LEVEL_COL.get(level, "phosphor"), 1.0)
            alpha = arrive * self.fade * (1.0 - 0.86 * any_iso * (1 - self.iso_amt[i]))
            pc[i] = (rgb[0] * k, rgb[1] * k, rgb[2] * k, alpha)
            pw[i] = 1.0 + 0.7 * self.iso_amt[i] + (0.35 if self.hover == i else 0)
        return pm, pc, pw

    def scan_uniform(self, t):
        if self.motion != "full":               # reduced motion: no band sweeping the model
            return (0.0, 0.02, 0.0)
        h = self.meta["height"]
        x = (t - self.scan_t0) / self.scan_dur
        if x > 1.15:
            return (0.0, 0.02, 0.0)
        return (-0.05 + x * (h + 0.1), 0.006 + 0.006 * h, 0.9 * (1 - max(0, x - 1) / 0.15))

    # ------------------------------------------------------------------ drawing
    def draw(self, holo, t, base=None, size=1.0, table=True, table_power=1.0, width=1.3, gain=1.0):
        if self.gpu is None:
            self.gpu = GpuModel.from_holo(self.model)
        mm, s = self.model_matrix(base, size)
        if table:
            r = max(self.meta["radius"] * s * 1.15, 0.32 * size)
            tb = (base if base is not None else np.eye(4, dtype=np.float32)) @ gl.rot_y(-self.anim_t * 0.05)
            holo.draw_table(tb, radius=r, cone_top=r * 1.12, cone_h=self.meta["height"] * s * 1.05,
                            power=table_power * self.fade)
        pm, pc, pw = self.part_state(t)
        pc[:, :3] *= gain
        holo.draw_model(self.gpu, mm, pm, pc, pw, scan=self.scan_uniform(t), fill=0.55, width=width, ghost=0.12)
        self._mm, self._pm = mm, pm
        # where each anchor lands on screen (for callouts) and the model's screen centre
        anchors = []
        for i, p in enumerate(self.parts):
            a = mm @ pm[i] @ np.r_[np.asarray(p["anchor"], np.float32), 1.0]
            anchors.append(a[:3])
        self.anchor_px, _ = holo.project(np.array(anchors))
        c = mm @ np.array([0, self.meta["height"] * 0.5, 0, 1], np.float32)
        self.center_px = holo.project(c[:3][None])[0][0]
        top = mm @ np.array([0, self.meta["height"], 0, 1], np.float32)
        self.top_px = holo.project(top[:3][None])[0][0]

    def draw_callouts(self, holo, rect, which=None, size=13, gap=None, max_lines=4, reveal=None, edges=False, row=False):
        """Leader lines + label boxes for parts. rect = (x, y, w, h) the area labels may use.
        which: part indices (default: all when exploded, else the isolated one)."""
        if self.anchor_px is None:
            return
        x0, y0, w, h = rect
        rev = ease((self.explode - 0.15) / 0.5) if reveal is None else reveal
        if which is None:
            which = list(range(self.n)) if rev > 0.01 else []
            if self.iso is not None:
                which = [self.iso]
        self.callout_rects = []
        if not which:
            return
        cx = self.center_px[0]
        gap = gap if gap is not None else w * 0.16
        items = []
        for i in which:
            ax, ay = self.anchor_px[i]
            items.append((i, ax, ay, "l" if ax < cx else "r"))
        if row:
            items = [(i, ax, ay, "row") for i, ax, ay, _ in items]
        for side in (("row",) if row else ("l", "r")):
            col_items = sorted([it for it in items if it[3] == side], key=lambda it: (it[1] if row else it[2]))
            boxes = []
            for i, ax, ay, _ in col_items:
                level, v, lines, age = self.readings[i]
                iso = self.iso == i
                body = lines[: (8 if iso else max_lines)]
                head = self.parts[i]["label"]
                tw = max([holo.measure(head, size, "bold", spacing=1.5)[0]] +
                         [holo.measure(l, size - 1)[0] for l in body]) + 22
                if iso and self.parts[i].get("hint"):
                    tw = max(tw, 300)
                lh = holo.measure("Ag", size - 1)[1]
                th = 10 + holo.measure(head, size, "bold")[1] + 4 + lh * len(body) + 8
                if iso and self.parts[i].get("hint"):
                    hw, hh = holo.measure(self.parts[i]["hint"], size - 2, width=tw - 22)
                    th += hh + 6
                boxes.append([i, ax, ay, tw, th, level, head, body, iso])
            if row:   # a row of readouts along the bottom of rect, leader lines rising to the parts
                tot = sum(b[3] for b in boxes) + 12 * (len(boxes) - 1)
                xx = x0 + max(0, (w - tot) / 2)
                for b in boxes:
                    b.append(y0 + h - b[4])
                    b.append(xx)
                    xx += b[3] + 12
            # vertical layout: keep near the anchor, no overlaps, inside the rect
            y = y0
            if not row:
                for b in boxes:
                    b.append(max(y, min(b[2] - b[4] / 2, y0 + h - b[4])))
                    y = b[-1] + b[4] + 10
                over = (boxes[-1][-1] + boxes[-1][4]) - (y0 + h) if boxes else 0
                if over > 0:
                    for b in boxes:
                        b[-1] -= over
            for b in boxes:
                i, ax, ay, tw, th, level, head, body, iso, by = b[:10]
                c = col(LEVEL_COL.get(level, "phosphor"))
                if row:
                    bx = b[10]
                elif edges:
                    bx = (x0 + 12) if side == "l" else (x0 + w - 12 - tw)
                else:
                    bx = (cx - gap - tw) if side == "l" else (cx + gap)
                bx = max(x0, min(bx, x0 + w - tw))
                a = rev if not iso else max(rev, float(self.iso_amt[i]))
                a *= self.fade
                if a < 0.02:
                    continue
                # leader: anchor dot -> elbow -> box edge, drawn in as it reveals
                if row:   # rise from the box's top edge, then straight to the anchor
                    ex, ey = bx + tw / 2, by
                    elbow_x = ex
                else:
                    ex = bx + tw if side == "l" else bx
                    ey = by + 16
                    elbow_x = ex + (18 if side == "l" else -18)
                seg = ease(a * 1.3)
                lx = ax + (elbow_x - ax) * seg
                ly = ay + (ey - ay) * seg
                holo.line2d(ax, ay, lx, ly, (c[0], c[1], c[2], 0.85 * a), 1.3)
                if seg > 0.99:
                    holo.line2d(elbow_x, ey, ex, ey, (c[0], c[1], c[2], 0.85 * a), 1.3)
                for k in range(8):
                    an = k * math.pi / 4
                    holo.line2d(ax + 3.5 * math.cos(an), ay + 3.5 * math.sin(an),
                                ax + 3.5 * math.cos(an + math.pi / 4), ay + 3.5 * math.sin(an + math.pi / 4),
                                (c[0], c[1], c[2], a), 1.4)
                holo.rect(bx, by, tw, th, wick((0.0, 0.03, 0.0, 0.78 * a)))
                holo.brackets(bx, by, tw, th, (c[0], c[1], c[2], 0.9 * a), arm=9)
                yy = by + 8
                _, hh = holo.label(head, bx + 11, yy, (c[0], c[1], c[2], a), size, "bold", spacing=1.5)
                yy += hh + 4
                for l in body:
                    _, lh2 = holo.label(l, bx + 11, yy, col("text", 1.0, a), size - 1)
                    yy += lh2
                if iso and self.parts[i].get("hint"):
                    holo.label(self.parts[i]["hint"], bx + 11, yy + 6, col("static", 1.0, a), size - 2, width=tw - 22)
                self.callout_rects.append((i, bx, by, tw, th))

    # ------------------------------------------------------------------ input
    def pick(self, holo, x, y):
        """Part under window pixel (x, y), or None. Callout boxes count as their part."""
        for i, bx, by, tw, th in self.callout_rects:
            if bx <= x <= bx + tw and by <= y <= by + th:
                return i
        if getattr(self, "_mm", None) is None:
            return None
        m = self.model
        pos = np.c_[m.pos, np.ones(len(m.pos), np.float32)]
        world = np.empty((len(m.pos), 3), np.float32)
        for i in range(self.n):
            sel = m.part == i
            world[sel] = (pos[sel] @ (self._mm @ self._pm[i]).T)[:, :3]
        px, depth = holo.project(world)
        tri = m.tri
        a, b, c = px[tri[:, 0]], px[tri[:, 1]], px[tri[:, 2]]
        v0, v1 = b - a, c - a
        v2 = np.array([x, y], np.float32) - a
        den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
        ok = np.abs(den) > 1e-6
        den = np.where(ok, den, 1)
        u = (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / den
        v = (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / den
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1)
        if self.iso is not None:
            hit &= m.part[tri[:, 0]] == self.iso
        if not hit.any():
            return None
        d = (depth[tri[:, 0]] + depth[tri[:, 1]] + depth[tri[:, 2]]) / 3
        d = np.where(hit, d, np.inf)
        return int(m.part[tri[int(np.argmin(d)), 0]])

    def toggle_explode(self, step=None):
        if step is None:
            self.explode_to = 0.0 if self.explode_to > 0.5 else 1.0
        else:
            self.explode_to = min(1.0, max(0.0, self.explode_to + step))

    def isolate(self, i):
        self.iso = None if (i is None or self.iso == i) else i

    def release(self):
        if self.gpu:
            self.gpu.delete()
            self.gpu = None
