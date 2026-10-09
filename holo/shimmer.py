"""The projector shimmer: a light, streamable DSP chain that gives a clean synthetic voice
the sound of a small holographic projector. One knob, `amount` 0..1 (voice.json
"shimmer"); 0 is the dry voice, 0.35 is the default, 1 is very obviously a machine.

    band      high-pass 110 Hz, gentle low-pass ~9 kHz: a small emitter, not a hi-fi speaker
    comb      a short feed-forward comb (3.1 ms, slowly swept): the metallic, hollow edge
    ring      ring modulation by a 160 Hz sine, blended in quietly: the machine shimmer
    room      a tiny Schroeder reverb (four feedback combs, two all-passes): the room it is in
    level     makeup gain and a soft clip so the chain never spikes

Stateful across chunks (filter memories, delay lines, oscillator phase), so streamed
audio comes out seamless. numpy + scipy only; well under 1% of a core at 24 kHz.
"""
import numpy as np
from scipy.signal import butter, lfilter, lfilter_zi


class Shimmer:
    def __init__(self, sr, amount=0.35):
        self.sr = sr
        self.amount = float(max(0.0, min(1.0, amount)))
        a = self.amount
        self.hp = butter(2, 110 / (sr / 2), "high")
        self.lp = butter(2, min(0.95, (9500 - 4000 * a) / (sr / 2)), "low")
        self.zhp = lfilter_zi(*self.hp) * 0
        self.zlp = lfilter_zi(*self.lp) * 0
        self.comb_d = int(0.0031 * sr)
        self.comb_g = 0.45 * a
        self.ring_f = 160.0
        self.ring_mix = 0.16 * a
        self.phase = 0.0
        self.lfo = 0.0
        self.hist = np.zeros(self.comb_d + 64, np.float32)      # past input for the comb
        # Schroeder room: delays in ms, all tiny (a small room / a projector housing)
        self.wet = 0.14 * a
        self.combs = []
        for ms, g in ((23.1, 0.62), (27.7, 0.6), (31.3, 0.58), (36.9, 0.55)):
            d = int(ms / 1000 * sr)
            b, den = [1.0], np.zeros(d + 1)
            den[0], den[-1] = 1.0, -g
            self.combs.append((np.array(b), den, np.zeros(d)))
        self.aps = []
        for ms, g in ((5.0, 0.7), (1.7, 0.7)):
            d = int(ms / 1000 * sr)
            b, den = np.zeros(d + 1), np.zeros(d + 1)
            b[0], b[-1] = -g, 1.0
            den[0], den[-1] = 1.0, -g
            self.aps.append((b, den, np.zeros(d)))
        self.gain = 1.0 + 0.25 * a

    def process(self, x):
        """x: float32 mono chunk -> processed chunk of the same length."""
        x = np.asarray(x, np.float32)
        if self.amount <= 0 or len(x) == 0:
            return x
        y, self.zhp = lfilter(*self.hp, x, zi=self.zhp)
        y, self.zlp = lfilter(*self.lp, y, zi=self.zlp)
        n = len(y)
        # swept comb: the delay drifts +-0.4 ms at 0.3 Hz
        buf = np.concatenate([self.hist, y])
        t = np.arange(n)
        sweep = self.comb_d + 0.4e-3 * self.sr * np.sin(2 * np.pi * 0.3 * (self.lfo + t) / self.sr)
        idx = len(self.hist) + t - sweep
        i0 = np.floor(idx).astype(int)
        frac = idx - i0
        delayed = buf[i0] * (1 - frac) + buf[np.minimum(i0 + 1, len(buf) - 1)] * frac
        self.lfo += n
        self.hist = buf[-len(self.hist):]
        y = y + self.comb_g * delayed
        # ring modulation, blended
        ph = self.phase + 2 * np.pi * self.ring_f * np.arange(n) / self.sr
        self.phase = float((ph[-1] + 2 * np.pi * self.ring_f / self.sr) % (2 * np.pi)) if n else self.phase
        y = (1 - self.ring_mix) * y + self.ring_mix * y * np.sin(ph) * 1.8
        # small room
        if self.wet > 0:
            acc = np.zeros(n)
            new = []
            for b, den, z in self.combs:
                o, z = lfilter(b, den, y, zi=z)
                acc += o
                new.append((b, den, z))
            self.combs = new
            r = acc / len(new)
            new = []
            for b, den, z in self.aps:
                r, z = lfilter(b, den, r, zi=z)
                new.append((b, den, z))
            self.aps = new
            y = (1 - self.wet) * y + self.wet * r
        y = np.tanh(self.gain * y * 1.1) / 1.1
        return y.astype(np.float32)


def apply(audio, sr, amount):
    return Shimmer(sr, amount).process(audio)


def dial_scratch(sr, variant=0, seconds=0.24):
    """The cue between voices: a radio dial swept across a dead band. Band-passed noise whose
    centre sweeps up and back down, a faint heterodyne whistle gliding the other way, and a
    few crackles; ~240 ms, peak about -12 dBFS. Made here, from noise; no samples."""
    rng = np.random.default_rng(1234 + variant)
    n = int(sr * seconds)
    t = np.arange(n) / sr
    x = rng.normal(0, 1, n)
    # resonant band-pass with a swept centre (2-pole, per sample)
    f = 700 + 2100 * np.sin(np.pi * np.clip(t / seconds, 0, 1)) ** 1.5 + 120 * variant
    r = 0.985
    y = np.zeros(n)
    y1 = y2 = 0.0
    for i in range(n):
        c = 2 * r * np.cos(2 * np.pi * f[i] / sr)
        v = x[i] * (1 - r) + c * y1 - r * r * y2
        y2, y1 = y1, v
        y[i] = v
    y /= np.abs(y).max() + 1e-9
    whistle = 0.18 * np.sin(2 * np.pi * np.cumsum(2400 - 1900 * t / seconds) / sr)
    crackle = np.zeros(n)
    for k in rng.integers(0, n - 40, 7):
        crackle[k:k + 40] += rng.normal(0, 0.6, 40) * np.exp(-np.arange(40) / 6)
    env = np.minimum(1, t / 0.012) * np.minimum(1, (seconds - t) / 0.06)
    out = (0.75 * y + whistle + crackle) * env
    return (0.25 * out / (np.abs(out).max() + 1e-9)).astype(np.float32)


class TimeStretch:
    """Streaming WSOLA: speed > 1 talks faster at the same pitch. Stateful across chunks,
    so a streamed sentence stays seamless. ~25 ms frames, 50% overlap, ±10 ms search."""

    def __init__(self, sr, speed=1.0):
        self.speed = float(speed)
        self.n = int(0.025 * sr) // 2 * 2          # frame
        self.hs = self.n // 2                       # synthesis hop
        self.ha = self.hs * self.speed              # analysis hop
        self.tol = int(0.010 * sr)
        self.win = np.hanning(self.n).astype(np.float32)
        self.buf = np.zeros(0, np.float32)          # input not yet consumed
        self.base = 0.0                             # analysis position of the next frame, in buf
        self.tail = np.zeros(self.hs, np.float32)   # overlap carried to the next output
        self.prev = None                            # the natural continuation of the last frame
        self.started = False

    def process(self, x, final=False):
        x = np.asarray(x, np.float32)
        if abs(self.speed - 1.0) < 1e-3:
            return x
        self.buf = np.concatenate([self.buf, x])
        out = []
        n, hs, tol = self.n, self.hs, self.tol
        while True:
            pos = int(round(self.base))
            lo, hi = max(0, pos - tol), pos + tol
            if hi + n + hs > len(self.buf):
                break
            if self.prev is None:
                best = pos
            else:   # the offset whose frame lines up best with the natural continuation
                seg = self.buf[lo:hi + n]
                ref = self.prev
                c = np.correlate(seg, ref, mode="valid")
                best = lo + int(np.argmax(c))
            frame = self.buf[best:best + n] * self.win
            out.append(self.tail + frame[:hs])
            self.tail = frame[hs:].copy()
            self.prev = self.buf[best + hs:best + hs + n]
            self.base += self.ha
            if self.base > n + tol * 2:             # drop consumed input, keep positions consistent
                cut = int(self.base) - tol - 1
                self.buf = self.buf[cut:]
                self.base -= cut
        if final:   # the overlap tail, then every remaining input sample (the last word must not be lost)
            rest = self.buf[int(round(self.base)):]
            k = min(hs, len(rest))
            ramp = np.linspace(0.0, 1.0, k, dtype=np.float32) if k else np.zeros(0, np.float32)
            head = self.tail.copy()
            head[:k] = self.tail[:k] * (1 - ramp) + rest[:k] * ramp
            out.append(head[:max(k, 1)] if k < hs else head)
            out.append(rest[k:])
            self.buf = np.zeros(0, np.float32)
        return np.concatenate(out) if out else np.zeros(0, np.float32)


class SilenceShaper:
    """Tighten a streamed sentence's pacing without touching the speech: drop the silence
    the model puts before the first word, cap every pause inside the sentence at `max_gap`
    (keeping its two edges so onsets and releases stay soft), and keep `tail` after the
    last word. Works on 10 ms frames; holds only silent frames, so speech is never delayed
    more than the pause it is shortening."""

    def __init__(self, sr, max_gap=0.12, tail=0.09, lead=0.015, floor_db=-46.0):
        self.f = sr // 100
        self.max_gap, self.tail, self.lead = int(max_gap * 100), int(tail * 100), int(lead * 100)
        self.thr = 10 ** (floor_db / 20)
        self.rest = np.zeros(0, np.float32)
        self.held = []              # silent frames waiting
        self.spoken = False         # has the first word started?

    def _flush_gap(self, keep):
        k = min(len(self.held), keep)
        out = self.held[:k // 2 + k % 2] + (self.held[len(self.held) - k // 2:] if k // 2 else [])
        self.held = []
        return out

    def process(self, x, final=False):
        x = np.concatenate([self.rest, np.asarray(x, np.float32)])
        n = len(x) // self.f
        self.rest = x[n * self.f:]
        out = []
        for i in range(n):
            fr = x[i * self.f:(i + 1) * self.f]
            if float(np.sqrt(np.mean(fr * fr))) < self.thr:
                self.held.append(fr)
                continue
            if self.held:
                out += self._flush_gap(self.max_gap if self.spoken else self.lead)
            self.spoken = True
            out.append(fr)
        if final:
            if self.spoken:
                out += self.held[:self.tail]
            self.held = []
            if len(self.rest) and self.spoken:
                out.append(self.rest)
            self.rest = np.zeros(0, np.float32)
        return np.concatenate(out) if out else np.zeros(0, np.float32)
