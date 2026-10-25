"""VECTOR's console: the construct on its emitter, an exhibit table beside it (a live
model hologram of whatever it is talking about), and the conversation as holographic
text. Scene logic only; the window and the brain live elsewhere.

It follows the user's motion setting (holo/motion.py): reduced slows the table, the
construct and the exhibit, and holds what pulses or blinks (the line's label, the
conversation and mic notices, a build's line) steady; still holds one frame, shows his
words whole, lands every transition at once, and says what the frame shows (still_sig)
so the window draws again only when that changes."""
import math
import time

import numpy as np

from .. import gl, motion
from ..render import T, Holo, approach, col, ease, lin, wick
from ..stage import Stage
from .avatar import Avatar
from .text import plain

STATE_WORD = {"idle": "AT HIS POST", "listening": "LISTENING", "thinking": "CONSULTING", "speaking": "SPEAKING",
              "error": "ANOMALY LOGGED", "sleep": "HUMMING"}


class Msg:
    __slots__ = ("role", "text", "shown", "t", "done")

    def __init__(self, role, text="", done=True):
        self.role, self.text, self.t, self.done = role, text, time.monotonic(), done
        self.shown = float(len(text)) if role != "vector" else 0.0


class VectorScene:
    def __init__(self, live, scale=1.0):
        self.live = live
        self.scale = scale
        self.avatar = Avatar()
        self.holo = None
        self.msgs = []
        self.stage = None          # current exhibit
        self.ex_parts = None
        self.ex_until = 0.0
        self.ex_amt = 0.0
        self.last_t = time.monotonic()
        self.fade = 0.0
        self.fade_to = 1.0
        self.subtitle = ""
        self.voice = "VOICE OFF"
        self.route = "qwen3.8-flash-next · homelab LiteLLM"
        self.audio_level = None    # set by the voice player while it speaks
        self.typing = False
        self.mic_live = False
        self.left_w = 540
        # the transcript side (chat box, header, buttons): 1 shown, 0 just VECTOR on his
        # table (conversation mode hides it until the pointer is over him; app.py drives it)
        self.panel = 1.0
        self.panel_to = 1.0
        # transcript scrollback: pixels scrolled up from the newest line (mouse wheel)
        self.scroll = 0.0
        self.scroll_max = 0.0
        self.motion = motion.current()     # full | reduced | still (holo/motion.py)
        self.clock = motion.Clock()        # the table's and the notes' seconds

    # ------------------------------------------------------------------ conversation
    def set_state(self, s):
        self.avatar.set_state(s)

    # ------------------------------------------------------------------ voice colour and mood
    def _voices(self):
        if getattr(self, "_vcfg", None) is None:
            import json
            import os
            try:
                with open(__import__("holo.paths", fromlist=["conf"]).conf("voice.json")) as f:
                    self._vcfg = json.load(f).get("voices", {})
            except (OSError, ValueError):
                self._vcfg = {}
        return self._vcfg

    def voice_hex(self, role):
        """A voice's colour as the construct shows it: voice.json's, in the theme's hues."""
        return T.retint(self._voices().get(role, {}).get("colour", "#39ff14"))

    def voices(self):
        """His voices in voice.json's order: role, name and colour as shown. Published in
        his state file, so a peripheral (a keyboard's dial, its lighting) names and colours
        them the way the hologram does."""
        return [{"role": r, "name": (v.get("name") if isinstance(v, dict) else None) or r, "colour": self.voice_hex(r)}
                for r, v in self._voices().items()]

    def set_voice(self, role, switch=True):
        """The voice now speaking: its colour crossfades in (with a tremor if it changed)."""
        col = self.voice_hex(role)
        changed = role != getattr(self, "voice_role", "main")
        self.voice_role = role
        self.avatar.set_voice_colour(col, switch and changed)

    def set_mood(self, mood):
        """calm | excited | concerned | alarmed, from the reply's marker or the facts."""
        self.mood_at = time.monotonic()
        order = {"calm": 0, "excited": 1, "concerned": 2, "alarmed": 3}
        cur = self.mood_name()
        # the facts win over a cheerful marker: never step down from alarmed/concerned mid-reply
        if mood in ("excited", "calm") and cur in ("concerned", "alarmed") and time.monotonic() - getattr(self, "fact_at", -99) < 20:
            return
        if mood in ("concerned", "alarmed") and order.get(mood, 0) < order.get(cur, 0):
            return
        if mood == "calm":
            self.avatar.calm_down()
        else:
            self.avatar.set_mood(mood)

    def set_mood_from_facts(self, mood):
        self.fact_at = time.monotonic()
        self.set_mood(mood)

    def mood_name(self):
        return self.avatar.mood_name()

    def memory_flash(self, what, n=1):
        """A brief glyph when VECTOR recalls (cyan-white), files (phosphor) or forgets (dim) something."""
        self.mem_flash = (what, n, time.monotonic())
        self.avatar.activity = 1.0
        self.avatar.glitch = max(self.avatar.glitch, 0.35)

    def add_user(self, text):
        self.msgs.append(Msg("you", text))
        self._trim()
        self.scroll = 0.0                    # a new turn: back to the newest line

    def add_attached(self, text):
        """A file or image Sir sent with his message: its line (attach.line), never its contents."""
        self.msgs.append(Msg("file", text))
        self._trim()
        self.scroll = 0.0

    def add_tool(self, text):
        self.end_reply()                 # whatever it said before the lookup is finished
        self.msgs.append(Msg("tool", text))
        self.avatar.activity = 1.0
        self._trim()

    def begin_reply(self):
        self.msgs.append(Msg("vector", "", done=False))
        self.scroll = 0.0

    def feed(self, delta):
        if not self.msgs or self.msgs[-1].role != "vector" or self.msgs[-1].done:
            self.begin_reply()
        self.msgs[-1].text += delta

    def end_reply(self):
        for m in self.msgs:
            if m.role == "vector":
                m.done = True

    def reroute(self, model, why):
        """A model didn't answer in time; the same turn goes to the next one. Say so."""
        self.end_reply()
        self.msgs.append(Msg("route", f"rerouting to {model} ({why})"))
        self.avatar.glance(-0.5, -0.1, 0.8)
        self._trim()

    def catch_up(self):
        """Opening after a minimize: replies that arrived while hidden show whole."""
        for m in self.msgs:
            if m.role == "vector" and m.done:
                m.shown = float(len(m.text))

    def streaming(self):
        return any(m.role == "vector" and not m.done and m.text for m in self.msgs[-1:])

    def note(self, text):
        self.msgs.append(Msg("sys", text))
        self._trim()

    def _trim(self):
        self.msgs = self.msgs[-200:]

    def scroll_by(self, dy):
        """Wheel over the transcript: dy > 0 goes back in time. Clamped to what's there."""
        self.scroll = max(0.0, min(self.scroll_max, self.scroll + dy))

    def scroll_home(self):
        self.scroll = 0.0

    def revealing(self):
        return any(m.role == "vector" and m.shown < len(m.text) for m in self.msgs)

    def exhibit(self, name, parts=None, hold=45.0):
        """Put a live model hologram on the side table, calling out `parts` (ids)."""
        if not self.stage or self.stage.name != name:
            if self.stage:
                self.stage.release()
            self.stage = Stage(name, self.live)
            self.stage.spin = 0.25
            self.stage.explode_to = 0.55
            self.ex_amt = 0.0
        ids = [p["id"] for p in self.stage.parts]
        self.ex_parts = [ids.index(p) for p in (parts or []) if p in ids][:3] or None
        self.stage.scan()
        self.ex_until = time.monotonic() + hold
        self.avatar.glance(0.75, -0.05, 1.4)

    def busy(self):
        a = self.avatar
        return (a.state in ("thinking", "speaking", "listening", "error") or self.revealing() or
                abs(self.fade - self.fade_to) > 0.01 or abs(self.panel - self.panel_to) > 0.01 or a.state_t < 1.5 or
                (self.stage is not None and abs(self.ex_amt - (1.0 if time.monotonic() < self.ex_until else 0.0)) > 0.01))

    # ------------------------------------------------------------------ animation
    def step(self):
        """Advance by the real time since the last step, under the motion setting in force;
        returns the real time. A still scene may step a long way at once (it draws nothing
        in between, and its springs land rather than swing)."""
        t = time.monotonic()
        self.motion = motion.current()
        dt = max(0.0, min(10.0 if self.motion == "still" else 0.1, t - self.last_t))
        self.last_t = t
        self.advance(dt)
        return t

    def advance(self, dt):
        a = self.avatar
        m_ = self.motion
        calm, still = m_ != "full", m_ == "still"
        # typewriter: VECTOR's words appear at a speaking pace; the iris pulses with them
        # (reduced: a steady level, no flicker with the letters; still: the words whole)
        lvl = 0.0
        for m in self.msgs:
            if m.role == "vector" and m.shown < len(m.text):
                if still:
                    m.shown = float(len(m.text))
                    continue
                backlog = len(m.text) - m.shown
                m.shown = min(len(m.text), m.shown + dt * (62 + backlog * 1.5))
                ch = m.text[int(m.shown) - 1:int(m.shown)].lower() if m.shown >= 1 else ""
                lvl = 0.55 if calm else \
                    0.55 + 0.35 * math.sin(time.monotonic() * 17) * math.sin(time.monotonic() * 5.3) + (0.15 if ch in "aeiou" else 0)
                a.activity = 1.0
                break
        if self.audio_level is not None:
            lvl = self.audio_level
        if still:                       # a held frame: the speaking level is a level, not a pulse
            a.level = 0.5 if a.state == "speaking" else 0.0
        else:                           # reduced: the voice's level smoothed, so nothing flickers with it
            a.level = approach(a.level, lvl, dt, 3.0 if calm else 18.0)
        if a.state == "speaking" and not self.revealing() and self.audio_level is None and a.state_t > 0.5:
            a.set_state("idle")
        a.update(dt, m_)
        on = self.stage is not None and time.monotonic() < self.ex_until
        if still:                       # transitions land at once
            self.fade, self.panel, self.ex_amt = self.fade_to, self.panel_to, 1.0 if on else 0.0
        else:
            self.fade = approach(self.fade, self.fade_to, dt, 6.0)
            self.panel = approach(self.panel, self.panel_to, dt, 7.0)
            self.ex_amt = approach(self.ex_amt, 1.0 if on else 0.0, dt, 3.5)
        # moods settle back to calm a few seconds after the reply ends (then fade over ~15 s)
        if a.state not in ("speaking", "thinking") and not self.revealing() and \
                time.monotonic() - getattr(self, "mood_at", 0) > 4 and any(a.mood_to.values()):
            a.calm_down()
        if self.stage:
            self.stage.motion = m_
            self.stage.update(dt)
            self.stage.fade = self.ex_amt
            if not on and self.ex_amt < 0.01:
                self.stage.release()
                self.stage = None

    def still_sig(self):
        """What a still frame shows (holo/motion.py): the window draws again when it changes.
        Steps the scene first (its logic: a reply ending, a mood settling, an exhibit leaving)."""
        self.step()
        a = self.avatar
        now = time.monotonic()
        last = self.msgs[-1] if self.msgs else None
        mf = getattr(self, "mem_flash", None)
        sh = getattr(self, "shell_cmd", None)
        bl = getattr(self, "build_line", None)
        wt = getattr(self, "watching", None) or {}
        cf = getattr(self, "claude_follow", None) or {}
        return (self.motion, T.version(), a.state, a.mood_name(), tuple(np.round(a.tint_to, 3)),
                a.state == "idle" and a.state_t > 6, self.subtitle, self.voice, self.route,
                len(self.msgs), last and (last.role, len(last.text), last.done),
                getattr(self, "history_open", False), getattr(self, "conversation", False), self.mic_live,
                self.typing, self.panel_to, self.fade_to, round(self.scroll, 1),
                self.stage.still_sig() if self.stage else None, self.ex_parts,
                bool(mf and now - mf[2] < 1.8) and mf[:2], sh and (sh[0], int(now - sh[1])),
                bl and (bl[0], bl[1], int(time.time() - bl[2])),
                wt and (int(wt.get("seconds_left") or 0), wt.get("paused")),
                cf and (cf.get("target"), cf.get("spoken"), tuple(cf.get("sessions") or ())))

    # ------------------------------------------------------------------ render
    def render(self, fbo, w, h):
        if self.holo is None:
            self.holo = Holo(self.scale)
        t = self.step()
        ta = self.clock(t, self.motion)       # the scene's seconds: the table turning, the humming notes
        H = self.holo
        sc = self.scale
        if T.version() != getattr(self, "_theme_seen", None):   # `bromigos theme set`: recolour
            if getattr(self, "_theme_seen", None) is not None:
                self.set_voice(getattr(self, "voice_role", "main"), switch=False)
            self._theme_seen = T.version()
        H.begin(w, h, ta, calm=motion.calm(self.motion))
        lw = self.left_w * sc
        ox = self.slide(w, lw)
        ex = ease(self.ex_amt)
        # camera frames the construct alone, or the construct plus the exhibit table
        tgt = np.array([0.3 * ex, 0.42 - 0.06 * ex, 0], np.float32)
        d = 1.75 + 1.05 * ex
        pitch = 0.13
        eye = tgt + d * np.array([0, math.sin(pitch), math.cos(pitch)], np.float32)
        band = 150 * sc * ex if self.ex_parts else 0.0     # room for the exhibit's readouts
        H.camera(eye, tgt, 32.0, viewport=(ox, 40 * sc * ex, lw, h - band - 40 * sc * ex))
        st = self.avatar.state
        table_col = {"thinking": "amber", "error": "danger"}.get(st) or tuple(self.avatar.colour()[0])
        base = gl.rot_y(-0.18 * ex)
        H.draw_table(base @ gl.rot_y(-ta * 0.08), radius=0.3, cone_top=0.26, cone_h=0.55,
                     power=(0.75 + 0.35 * self.avatar.level) * self.fade, colour=table_col)
        self.avatar.draw(H, base, self.fade)
        if self.stage and self.ex_amt > 0.01:
            eb = gl.translate(0.66, 0, -0.1) @ gl.rot_y(-0.3)
            self.stage.draw(H, t, base=eb, size=0.5, table_power=0.8, gain=0.5, width=1.1)
            which = self.ex_parts if self.ex_parts is not None else []
            self.stage.draw_callouts(H, (ox + 14 * sc, h - 150 * sc, lw - 28 * sc, 122 * sc), which=which,
                                     size=int(12 * sc), max_lines=3, reveal=self.ex_amt, row=True)
        self._text(H, w, h, lw, ta)
        # nearly clear behind the hologram (under the compositor's blur threshold) so the
        # window you click through to stays visible; the transcript gets its own backing
        H.end(fbo, bg=wick((0.0, 0.02, 0.0, 0.12 * self.panel)), bloom=1.2, fade=self.fade)

    def slide(self, w, lw):
        """How far right VECTOR's column sits: 0 with the transcript shown, flush with the
        window's right edge (where it is anchored) with it hidden."""
        p = ease(self.panel)
        return (w - lw) * (1.0 - p)

    def _text(self, H, w, h, lw, ta=0.0):
        sc = self.scale
        f = self.fade
        calm, still = self.motion != "full", self.motion == "still"
        now = time.monotonic()
        pf = f * self.panel                  # the transcript side's alpha
        ox = self.slide(w, lw)               # VECTOR's column, slid right while the transcript is hidden
        ph = col("phosphor", 1, f)
        # the frame closes in around VECTOR alone when the transcript side is hidden
        H.brackets(ox + 8 * sc, 8 * sc, (lw - 16 * sc) + (w - lw) * ease(self.panel), h - 16 * sc,
                   (ph[0], ph[1], ph[2], 0.75 * f), arm=18 * sc, width=1.5)
        x0 = lw + 14 * sc
        tw, th = H.label("VECTOR", ox + 26 * sc, 22 * sc, ph, int(24 * sc), "bold", spacing=7)
        st = self.avatar.state
        sc_col = {"thinking": "amber", "error": "danger", "listening": "white", "sleep": "dim"}.get(st, "soft")
        word = STATE_WORD.get(st, st.upper()) + (f" · {self.subtitle}" if self.subtitle else "")
        H.label(word, ox + 26 * sc + tw + 16 * sc, 22 * sc + th - 18 * sc, col(sc_col, 1, f), int(13 * sc), "bold", spacing=2.5)
        mf = getattr(self, "mem_flash", None)
        if mf and now - mf[2] < 1.8:
            # memory: a small ring of dots closing around the word, then fading (reduced: the
            # ring doesn't turn; still: shown whole for its time)
            age = 1.0 if still else now - mf[2]
            a = (1.0 if age < 1.2 else 1.0 - (age - 1.2) / 0.6) * f
            word = {"recall": f"◈ RECALLED {mf[1]}", "store": "◈ FILED", "forget": "◈ FORGOTTEN"}.get(mf[0], "◈ MEMORY")
            c = {"recall": "white", "store": "phosphor", "forget": "static"}.get(mf[0], "soft")
            cx, cy = ox + lw * 0.5, h * 0.17
            for k in range(12):
                ang = k * math.pi / 6 + (0.0 if calm else age * 2.0)
                r = 46 * sc * (1.0 - 0.25 * min(1.0, age * 2))
                H.label("·", cx + r * math.cos(ang), cy + 0.45 * r * math.sin(ang), col(c, 1, a * (0.4 + 0.6 * (k % 3 == 0))), int(14 * sc), anchor="cm")
            H.label(word, cx, cy, col(c, 1, a), int(12 * sc), "bold", anchor="cm", spacing=2.5)
        # the line: VECTOR answers from his post at the SpacePort arrivals pad, over the relays
        pulse = 0.7 if calm else 0.55 + 0.25 * math.sin(now * 1.3)          # calm: steady
        H.label("ARRIVALS · LINE OPEN", ox + 26 * sc, 22 * sc + th + 4 * sc, col("dim", 1.5, pulse * f), int(10 * sc), "bold", spacing=3)
        if st == "idle" and self.avatar.state_t > 6:
            # he hums between arrivals: a few drifting notes beside the construct (visual only;
            # on the scene's clock: slower under reduced motion, held under still)
            t = ta
            for k in range(3):
                ph_ = ((t * 0.35 + k / 3) % 1.0)
                if ph_ < 0.75:
                    a = math.sin(ph_ / 0.75 * math.pi) * 0.55 * f
                    H.label("♪", ox + lw * 0.62 + 24 * sc * k + 10 * sc * math.sin(t + k), h * 0.42 - ph_ * 90 * sc,
                            col("soft", 1, a), int((13 + 3 * k) * sc))
        H.label(self.route, w - 28 * sc, 26 * sc, col("static", 1, 0.9 * pf), int(12 * sc), anchor="rt")
        sh = getattr(self, "shell_cmd", None)
        if sh:   # a terminal command is running: show it, live, with how long it has been going
            cmd = " ".join(sh[0].split())
            cmd = cmd if len(cmd) <= 64 else cmd[:61] + "…"
            H.label(f"$ {cmd}   {now - sh[1]:.0f}s", lw + 14 * sc, h - 100 * sc,
                    col("amber", 1, pf), int(12 * sc), "bold")
        bl = getattr(self, "build_line", None)
        if bl:   # a background build: where it is, how far, how long (STOP is the panel button)
            note, pct, t0 = bl
            txt = f"BUILDING · {note}" + (f" · {pct}%" if pct else "") + f" · {time.time() - t0:.0f}s"
            H.label(txt if len(txt) <= 90 else txt[:89] + "…", lw + 14 * sc, h - 118 * sc,
                    col("phosphor", 1, (0.9 if calm else 0.75 + 0.25 * math.sin(now * 2.0)) * pf), int(12 * sc), "bold")
        wt = getattr(self, "watching", None)
        if wt:   # watch mode: always visible while it's on
            left = int(wt.get("seconds_left") or 0)
            H.label(f"◉ WATCHING YOUR SCREEN · {left // 60}:{left % 60:02d}" + (" · PAUSED" if wt.get("paused") else ""),
                    w - 28 * sc, 62 * sc, col("danger", 1, f), int(12 * sc), "bold", anchor="rt", spacing=1.5)
        cf = getattr(self, "claude_follow", None)
        if cf:   # follow mode: Claude Code's replies read aloud, for as long as it's on
            who = "ALL SESSIONS" if cf.get("target") == "all" else ((cf.get("sessions") or ["CLAUDE"])[0]).upper()
            txt = f"◉ READING CLAUDE · {who}"
            H.label(txt if len(txt) <= 60 else txt[:59] + "…", w - 28 * sc, (80 if wt else 62) * sc,
                    col("phosphor", 1, f), int(12 * sc), "bold", anchor="rt", spacing=1.5)
        if getattr(self, "conversation", False):   # hands-free: say so, all the time
            pulse = 1.0 if calm else 0.55 + 0.45 * math.sin(now * 3.2)     # calm: no blinking
            H.label("● CONVERSATION · LISTENING", w - 28 * sc, 44 * sc, col("danger", 1, pulse * pf), int(12 * sc), "bold",
                    anchor="rt", spacing=1.5)
            if self.panel < 0.99:            # transcript hidden: the mic notice moves under his name
                H.label("● CONVERSATION · LISTENING", ox + 26 * sc, 22 * sc + th + 22 * sc,
                        col("danger", 1, pulse * f * (1 - self.panel)), int(11 * sc), "bold", spacing=1.5)
        elif self.mic_live:   # the microphone is open only while SUPER+V is held, and it says so
            blink = 1.0 if calm else 0.65 + 0.35 * math.sin(now * 6)
            H.label("● MIC LIVE · RELEASE SUPER+V TO SEND", w - 28 * sc, 44 * sc, col("danger", 1, blink * f), int(12 * sc), "bold", anchor="rt", spacing=1.5)
        else:
            H.label(self.voice, w - 28 * sc, 44 * sc, col("dim", 1.3, 0.9 * pf), int(11 * sc), "bold", anchor="rt", spacing=1.5)
        # divider between the table and the transcript
        H.line2d(lw, 70 * sc, lw, h - 30 * sc, col("guard", 1.6, 0.9 * pf), 1.0)
        if pf < 0.01:
            return
        # transcript, newest at the bottom, above the input line
        width = w - x0 - 32 * sc
        # the input box grows with what's typed: the transcript ends above its top edge
        bottom = h - max(78 * sc, getattr(self, "entry_clear", 0.0))
        y = bottom + self.scroll * sc
        top = 72 * sc
        rows = []
        hidden_below = 0
        reached_oldest = True
        for m in ([] if getattr(self, "history_open", False) else reversed(self.msgs)):   # the history panel covers it
            if m.role == "vector":
                text = plain(m.text[:int(m.shown)], streaming=True).strip()
                if not text:
                    continue
                if m.shown < len(m.text) or not m.done:
                    text += "▌"
                c, size, weight, ind = col("phosphor", 1, f), 15, "normal", 0
            elif m.role == "you":
                text, c, size, weight, ind = "› " + m.text, col("white", 1, 0.95 * f), 14, "medium", 0
            elif m.role == "tool":
                text, c, size, weight, ind = "⟐ " + m.text, col("dim", 1.5, 0.95 * f), 12, "normal", 14
            elif m.role == "file":           # under Sir's words, in his colour: what he handed over
                text, c, size, weight, ind = m.text, col("white", 1, 0.7 * f), 12, "normal", 14
            elif m.role == "route":
                text, c, size, weight, ind = "↻ " + m.text, col("amber", 0.9, 0.95 * f), 12, "normal", 14
            else:
                text, c, size, weight, ind = m.text, col("amber", 1, f), 13, "normal", 0
            tw_, th_ = H.measure(text, int(size * sc), weight, width=(width - ind * sc) / sc)
            y -= th_ + (10 if m.role not in ("tool", "file") else 4) * sc
            if y < top:
                reached_oldest = False
                break
            if y + th_ > bottom + 4 * sc:     # scrolled past: below the box
                hidden_below += 1
                continue
            rows.append((text, x0 + ind * sc, y, c, int(size * sc), weight, (width - ind * sc) / sc))
        # how far back the wheel may go: no further once the oldest line is on screen
        self.scroll_max = self.scroll if reached_oldest else self.scroll + 1e6
        # a dark backing under the transcript and the entry, only as tall as the text
        y_top = min([r[2] for r in rows] + [bottom]) - 12 * sc
        H.rect(x0 - 14 * sc, y_top, w - x0 - 4 * sc, h - y_top - 10 * sc, wick((0.0, 0.035, 0.0, 0.8 * pf)))
        H.rect(x0 - 14 * sc, 12 * sc, w - x0 - 4 * sc, 84 * sc, wick((0.0, 0.035, 0.0, 0.62 * pf)))   # header backing
        H.rect(12 * sc, 12 * sc, 330 * sc, 52 * sc, wick((0.0, 0.035, 0.0, 0.5 * f)))
        for text, x, yy, c, size, weight, wd in rows:
            H.label(text, x, yy, (c[0], c[1], c[2], c[3] * self.panel), size, weight, width=wd)
        if hidden_below:                     # scrolled back: say there's more below
            H.label(f"▼ {hidden_below} NEWER · SCROLL DOWN", w - 32 * sc, bottom + 2 * sc, col("amber", 1, pf),
                    int(11 * sc), "bold", anchor="rt", spacing=1.5)
        hint = ("Enter sends · Esc hands the keyboard back · Shift+Esc minimizes" if self.typing else
                "click the box to type · clicks elsewhere go to your windows · SUPER+E minimizes")
        H.label(hint, w - 28 * sc, h - 14 * sc, col("static", 1, 0.8 * pf), int(11 * sc), anchor="rb")

    def demo_transcript(self):
        """For offscreen screenshots only."""
        self.add_user("how's the lab doing?")
        self.add_tool("lab status · 6 of 6 nodes, 34 of 34 services")
        self.feed("Right, so, good news first, which is also the only news: all six nodes are up and every one of "
                  "the thirty-four services is answering. The beam's lit. Nine alerts firing, but they're the usual "
                  "chatter, I checked twice. Three times. It's fine.")
        self.end_reply()
        for m in self.msgs:
            m.shown = len(m.text)
