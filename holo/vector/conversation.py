"""Conversation mode: hands-free, local. While it is on, VECTOR listens on the echo-
cancelled mic, finds where you stop talking with Silero VAD (onnxruntime, CPU), sends the
utterance to the same local speech-to-text as push-to-talk, replies, and listens again.
Talking over him stops him (barge-in). Off after `auto_off_s` without speech.

Nothing records while it is off: the mic process exists only between start() and stop().
With the voice muted he still listens, and replies in text only.
voice.json "conversation": end_of_turn_ms, vad_threshold, min_speech_ms, auto_off_s, max_turn_s.
"""
import os
import threading
import time

import numpy as np

def log(*a):
    print(time.strftime("%H:%M:%S"), "conversation:", *a, flush=True)
from gi.repository import GLib

VAD_MODEL = os.path.expanduser("~/.local/share/bromigos/voice/silero_vad.onnx")
SR = 16000
FRAME = 512                      # 32 ms, Silero's frame at 16 kHz
CUE = next((p for p in (os.path.expanduser("~/.config/bromigos-live/sounds/blip.wav"),
                         "/usr/lib/bromigos/live/sounds/blip.wav") if os.path.exists(p)),   # packaged: bromigos-live
           os.path.expanduser("~/.config/bromigos-live/sounds/blip.wav"))


class Vad:
    """Silero VAD v5 (2 MB ONNX). Falls back to a plain energy gate if it can't load."""

    def __init__(self):
        self.sess = None
        try:
            import onnxruntime as ort
            o = ort.SessionOptions()
            o.inter_op_num_threads = o.intra_op_num_threads = 1
            self.sess = ort.InferenceSession(VAD_MODEL, sess_options=o, providers=["CPUExecutionProvider"])
        except Exception:
            self.sess = None
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), np.float32)
        self.ctx = np.zeros(64, np.float32)

    def prob(self, frame):
        if self.sess is None:
            rms = float(np.sqrt(np.mean(frame * frame)))
            return float(np.clip((20 * np.log10(max(rms, 1e-6)) + 48) / 18, 0, 1))
        x = np.concatenate([self.ctx, frame])[None, :].astype(np.float32)
        out, self.state = self.sess.run(None, {"input": x, "state": self.state, "sr": np.array(SR, np.int64)})
        self.ctx = frame[-64:]
        return float(out[0][0])


class BargeIn:
    """Is this the host talking over VECTOR, or VECTOR's own voice leaking past the echo
    canceller? While he plays:
      * the first `barge_in_grace_ms` after playback starts never count (the canceller is
        still converging); that leak is the first measure of the echo residual;
      * after that it takes `barge_in_ms` of sustained speech: 80% of the frames in that
        window at `barge_in_threshold` or more, with the level (a short envelope; the
        canceller gates single frames during double talk) at least `barge_in_over_residual`
        times the residual, which keeps learning from frames that are clearly not speech."""

    def __init__(self, cfg):
        self.thr = float(cfg("barge_in_threshold", 0.85))
        self.need = cfg("barge_in_ms", 350) / 1000
        self.grace = cfg("barge_in_grace_ms", 300) / 1000
        self.ratio = float(cfg("barge_in_over_residual", 3.0))
        self.min_rms = float(cfg("barge_in_min_rms", 0.004))
        self.reset()

    def reset(self):
        self.hist = []
        self.resid = None
        self.level = 0.0

    def _learn(self, rms):
        self.resid = rms if self.resid is None else 0.9 * self.resid + 0.1 * rms

    def feed(self, p, rms, since_play, fdur):
        self.level = max(rms, 0.7 * self.level)
        if since_play < self.grace:
            self.hist = []
            self._learn(rms)
            return False
        if p < 0.3:
            self._learn(rms)
        n = max(1, round(self.need / fdur))
        self.hist = (self.hist + [p >= self.thr and self.level >= max(self.min_rms, (self.resid or 0.0) * self.ratio)])[-n:]
        return len(self.hist) == n and sum(self.hist) >= 0.8 * n


class Conversation:
    def __init__(self, voice):
        self.voice = voice                       # holo.vector.voice.Voice
        self.app = voice.app
        self.on = False
        self.proc = None
        self.thread = None
        self.barge_ins = 0                       # counted (tests read it)
        self.barge = BargeIn(self.cfg)
        self.vad = None
        self.last_speech = 0.0
        self.turns = 0

    def cfg(self, k, d):
        return self.voice.cfg.get("conversation", {}).get(k, d)

    # ------------------------------------------------------------------ control
    def toggle(self):
        (self.stop if self.on else self.start)()
        return self.on

    def start(self):
        if self.on:
            return
        if self.vad is None:
            self.vad = Vad()
        self.vad.reset()
        self.on = True
        self.last_speech = time.monotonic()
        self.proc = self.voice.open_mic()
        self.thread = threading.Thread(target=self._listen, args=(self.proc,), daemon=True, name="vector-conversation")
        self.thread.start()
        threading.Thread(target=lambda: self.voice._safe(lambda: self.voice._req({"op": "warm"}, 60)), daemon=True).start()
        if getattr(self.app, "memory", None):
            self.app.memory.warm()
        GLib.idle_add(self._ui, True)

    def stop(self, reason=""):
        if not self.on:
            return
        self.on = False
        proc, self.proc = self.proc, None
        if proc:
            self.voice.close_mic(proc)
        GLib.idle_add(self._ui, False, reason)

    def _ui(self, on, reason=""):
        sc = self.app.pscene
        sc.conversation = on
        sc.mic_live = on
        if on:
            self.app.show_vector(focus=False, greet=False)
            sc.note("Conversation mode: I'm listening. Just talk; SUPER+SHIFT+E ends it.")
        else:
            if reason:
                sc.note(reason)
            if sc.avatar.state == "listening":
                sc.set_state("idle")
            self._cue()
        self.app._publish_state()
        return False

    def _cue(self):
        if os.path.exists(CUE) and not self.voice.muted:
            import subprocess
            sink = self.voice.cfg.get("sink")
            subprocess.Popen(["pw-play", "--volume", "0.35"] + (["--target", sink] if sink else []) + [CUE],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ------------------------------------------------------------------ listening
    def _listen(self, proc):
        thr = float(self.cfg("vad_threshold", 0.5))
        eot = self.cfg("end_of_turn_ms", 700) / 1000
        min_speech = self.cfg("min_speech_ms", 250) / 1000
        max_turn = self.cfg("max_turn_s", 30)
        auto_off = self.cfg("auto_off_s", 120)
        fdur = FRAME / SR
        pre = []                                   # frames just before speech starts
        utt = bytearray()
        speaking_run = silence_run = 0.0
        in_speech = was_playing = False
        rest = b""
        sc = self.app.pscene
        while self.on:
            b = proc.stdout.read(FRAME * 2 - len(rest))
            if not b:
                break
            rest += b
            if len(rest) < FRAME * 2:
                continue
            raw, rest = rest[:FRAME * 2], b""
            x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
            playing = self.voice.speaking
            busy = playing or bool(self.app.brain and self.app.brain.busy)
            p = self.vad.prob(x)
            rms = float(np.sqrt(np.mean(x * x)))
            if not busy or in_speech:
                sc.avatar.level = max(0.0, min(1.0, (20 * np.log10(max(rms, 1e-5)) + 55) / 40))
            if not in_speech:
                pre = (pre + [raw])[-16:]      # ~0.5 s: covers the 0.35 s a barge-in must last
                if playing:                    # his own voice may leak past the canceller: BargeIn decides
                    if not was_playing:
                        self.barge.reset()
                    started = self.barge.feed(p, rms, time.monotonic() - self.voice.play_start, fdur)
                else:
                    speaking_run = speaking_run + fdur if p >= thr else 0.0
                    started = speaking_run >= min_speech
                was_playing = playing
                if started:
                    in_speech = True
                    silence_run = 0.0
                    utt = bytearray(b"".join(pre))
                    self.last_speech = time.monotonic()
                    log("speech start", "(barge-in)" if busy else "")
                    if busy:                        # barge-in: he stops, the turn is cancelled
                        self.barge_ins += 1
                        self.voice.stop()
                        if self.app.brain:
                            self.app.brain.interrupt()
                        GLib.idle_add(self.app.pscene.end_reply)
                    GLib.idle_add(sc.set_state, "listening")
                elif not busy and time.monotonic() - self.last_speech > auto_off:
                    self.stop(f"Conversation mode off after {int(auto_off)} quiet seconds.")
                    break
                continue
            utt += raw
            silence_run = silence_run + fdur if p < thr * 0.7 else 0.0
            if p >= thr:
                self.last_speech = time.monotonic()
            if silence_run >= eot or len(utt) / (SR * 2) >= max_turn:
                data = bytes(utt[: len(utt) - int(max(0.0, silence_run - 0.2) * SR) * 2])
                in_speech = False
                speaking_run = silence_run = 0.0
                pre = []
                self.vad.reset()
                log(f"end of turn: {len(data) / (SR * 2):.2f}s")
                if len(data) / (SR * 2) >= 0.4:
                    self.turns += 1
                    GLib.idle_add(sc.set_state, "thinking")
                    self.voice.last_stats["conv_turn_t"] = time.monotonic()
                    threading.Thread(target=self.voice._transcribe, args=(data,), daemon=True).start()
        if self.on:                                 # the mic went away underneath us
            self.stop("Conversation mode off: the microphone stopped.")
