"""VECTOR's voice, desktop side (system python, inside the holo daemon).

Push-to-talk: SUPER+V down starts `pw-record` (16 kHz mono, raw to a pipe) and VECTOR
shows LISTENING with a live MIC indicator; key up stops it at once. Nothing records
unless the key is held, and a recording is cut at `max_record_seconds` in case a
release is missed.

VECTOR never hears itself (the operator has speakers, not headphones):
  * barge-in: SUPER+V down kills playback, clears the speech queue and interrupts the
    running turn, like talking over someone;
  * nothing is spoken while the mic is open, and the first `drain_ms` of a recording that
    starts right after playback is dropped (audio still draining from the speakers);
  * echo cancellation (voice.json "echo_cancel"): a session-only PipeWire webrtc AEC pair,
    vector_aec_sink -> the default speakers and the default mic -> vector_aec_source. VECTOR
    plays into the sink and records from the source, so its own voice is the reference
    that gets subtracted. Defaults are untouched; the nodes sit suspended (no CPU) when
    unused, and they are rebuilt if the default devices change;
  * a transcript that matches what VECTOR said in the last 30 s is dropped. Audio goes to the venv voice server for speech-to-text; the text
is asked like typed text. Replies are spoken sentence by sentence as they stream in
(TTS cached on disk); SUPER+SHIFT+V mutes the voice (persisted). There is no hotword.
"""
import json
import os
import re
import site
import socket
import subprocess
import signal
import tempfile
import threading
import time
import wave

import numpy as np
from gi.repository import GLib

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ..paths import conf  # noqa: E402
CONF = conf("voice.json")
VENV = os.path.expanduser("~/.local/share/bromigos/venv-tts")   # py3.12 + torch: Qwen3-TTS, whisper, kokoro
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
SOCK = os.environ.get("BROMIGOS_VOICE_SOCK") or os.path.join(RUNTIME, "bromigos-holo-voice.sock")   # tests use their own
STATE = os.path.expanduser("~/.local/state/bromigos")
MUTED = os.path.join(STATE, "vector-voice-muted")
MODE = os.path.join(STATE, "vector-voice-mode")          # auto (default) or a pinned role
ROLES = ("main", "robot", "scientist", "floor", "notify")
LOG = os.path.join(STATE, "holo-voice.log")
SENT_END = re.compile(r"(.+?[.!?…]+)(\s+|$)", re.S)


def _venv_env():
    env = dict(os.environ)
    sp = next(iter(__import__("glob").glob(os.path.join(VENV, "lib/python3*/site-packages"))), "")
    libs = [os.path.join(sp, "nvidia", d, "lib") for d in ("cublas", "cudnn", "cuda_runtime", "cuda_nvrtc")]
    env["LD_LIBRARY_PATH"] = ":".join([p for p in libs if os.path.isdir(p)] + [env.get("LD_LIBRARY_PATH", "")])
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    return env


class Voice:
    def __init__(self, app):
        self.app = app
        with open(CONF) as f:
            self.cfg = json.load(f)
        self.cfg["sink"] = os.environ.get("BROMIGOS_HOLO_SINK") or self.cfg.get("sink")   # e.g. a headset node
        self.muted = os.path.exists(MUTED)
        self.rec = None
        self.rec_buf = bytearray()
        self.rec_t0 = 0.0
        self.player = None
        self.queue = []            # sentences waiting to be spoken
        self.pending = ""          # streamed text not yet a full sentence
        self.speaking = False
        self.qlock = threading.Lock()
        self.gen = 0               # bumps on stop(): stale audio is dropped
        self.last_stats = {}
        self.server_proc = None
        self.play_end = 0.0        # when playback last stopped (monotonic)
        self.skip_until = 0.0      # mic audio before this is speaker drain, dropped
        self.said = []             # (monotonic, sentence) VECTOR spoke recently
        self.aec = None            # (module id, sink master, source master)
        # the AEC pair's names and masters; tests set their own (a null sink standing in for the speakers)
        pre = os.environ.get("BROMIGOS_VOICE_AEC") or "vector_aec"
        self.aec_sink, self.aec_source = pre + "_sink", pre + "_source"
        masters = os.environ.get("BROMIGOS_VOICE_AEC_MASTERS", "")
        self.aec_masters = tuple(masters.split(",", 1)) if "," in masters else None
        self.play_start = 0.0      # when the current pw-play started (monotonic)
        self.reroutes = 0          # playback found off the AEC sink and moved back
        self.roles = tuple(self.cfg.get("voices", {}).keys()) or ROLES
        self.mode = self._read_mode()
        from .text import VoiceSplitter
        self.splitter = VoiceSplitter(self.mode, self.roles)
        self.voice_now = "main"    # the voice that spoke last (a change plays the dial scratch)
        if app is not None:
            threading.Thread(target=self._aec_keeper, daemon=True, name="vector-aec").start()

    # ------------------------------------------------------------------ server
    def _req(self, obj, timeout=30.0):
        for attempt in range(2):
            try:
                s = socket.socket(socket.AF_UNIX)
                s.settimeout(timeout)
                s.connect(SOCK)
                s.sendall((json.dumps(obj) + "\n").encode())
                buf = b""
                while not buf.endswith(b"\n"):
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                s.close()
                out = json.loads(buf.decode() or "{}")
                if out.get("error"):
                    raise RuntimeError(out["error"])
                return out
            except (FileNotFoundError, ConnectionRefusedError):
                if attempt:
                    raise
                self._spawn()

    _spawn_lock = threading.Lock()

    def _spawn(self):
        with self._spawn_lock:             # concurrent callers share one spawn
            self._spawn_locked()

    def _spawn_locked(self):
        os.makedirs(STATE, exist_ok=True)
        try:                               # already up (another caller won the race)
            s = socket.socket(socket.AF_UNIX)
            s.connect(SOCK)
            s.close()
            return
        except OSError:
            pass
        if not (self.server_proc and self.server_proc.poll() is None):
            self.server_proc = subprocess.Popen([os.path.join(VENV, "bin/python"), "-m", "holo.voice_server"], cwd=HERE,
                                                env=_venv_env(), stdin=subprocess.DEVNULL, stdout=open(LOG, "a"),
                                                stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(100):
            if os.path.exists(SOCK):
                try:
                    s = socket.socket(socket.AF_UNIX)
                    s.connect(SOCK)
                    s.close()
                    return
                except OSError:
                    pass
            time.sleep(0.1)

    # ------------------------------------------------------------------ listening
    # ------------------------------------------------------------------ echo cancellation
    def _pactl(self, *args):
        return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=5).stdout.strip()

    def ensure_aec(self):
        """The AEC pair on the current default devices; None when off or unavailable."""
        if not self.cfg.get("echo_cancel", True) or self.cfg.get("sink"):
            return None
        try:
            sink, src = self.aec_masters or (self._pactl("get-default-sink"), self._pactl("get-default-source"))
            if not sink or not src or sink.startswith(self.aec_sink) or src.startswith(self.aec_source):
                return None
            mods = self._pactl("list", "short", "modules")
            if self.aec and self.aec[1:] == (sink, src) and f"{self.aec[0]}\t" in mods + "\t":
                return self.aec
            for line in mods.splitlines():          # ours from an earlier run, or on old devices
                if f"source_name={self.aec_source} " in line + " ":
                    self._pactl("unload-module", line.split()[0])
            mid = self._pactl("load-module", "module-echo-cancel", "aec_method=webrtc",
                              f"source_master={src}", f"sink_master={sink}",
                              f"source_name={self.aec_source}", f"sink_name={self.aec_sink}",
                              "rate=16000", "channels=1")
            self.aec = (mid, sink, src) if mid.isdigit() else None
        except (OSError, subprocess.TimeoutExpired):
            self.aec = None
        return self.aec

    def _aec_fresh(self):
        return bool(self.aec) and time.monotonic() - getattr(self, "_aec_checked", 0) < 60

    def _aec_keeper(self):
        """Keep the echo-cancel pair ready (and on the current default devices) off the hot path."""
        while True:
            try:
                conv = getattr(self, "conv", None)
                if not (self.speaking or self.rec or (conv and conv.on)) or not self.aec:
                    self.ensure_aec()
                self._aec_checked = time.monotonic()
            except Exception:
                pass
            time.sleep(30)

    def drop_aec(self):
        if self.aec:
            try:
                self._pactl("unload-module", self.aec[0])
            except (OSError, subprocess.TimeoutExpired):
                pass
            self.aec = None

    # ------------------------------------------------------------------ listening
    def mic_source(self):
        """The capture node: voice.json "mic_source" if set, else the echo-cancelled mic."""
        if self.cfg.get("mic_source"):
            return self.cfg["mic_source"]
        aec = self.aec if self._aec_fresh() else self.ensure_aec()
        return self.aec_source if aec else None

    def open_mic(self):
        src = self.mic_source()
        return subprocess.Popen(["pw-record", "--raw", "--rate", "16000", "--channels", "1", "--format", "s16",
                                 "--latency", "20ms"] + (["--target", src] if src else []) + ["-"],
                                stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @staticmethod
    def close_mic(proc, reader=None):
        """SIGINT so pw-record flushes what it holds, then wait for the reader to drain."""
        try:
            proc.send_signal(signal.SIGINT)
            proc.wait(1.0)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()
        if reader is not None:
            reader.join(1.0)

    def ptt_down(self):
        if self.rec:
            return
        was_speaking = self.speaking or (self.player and self.player.poll() is None)
        self.stop()                                   # barge-in: talking over VECTOR stops it
        if self.app.brain:
            self.app.brain.interrupt()                # ... and the turn it was answering
        if getattr(self.app, "memory", None):
            self.app.memory.warm()                    # recall will be ready when the transcript is
        self.app.pscene.end_reply()
        # speaker drain: VECTOR's last syllable is still in the air for a moment after the
        # player stops. With echo cancellation that tail is cancelled too, so drop far less.
        drain = self.cfg.get("drain_ms", 350) / 1000
        if self.mic_source() == self.aec_source:
            drain = min(drain, 0.12)
        self.skip_until = time.monotonic() + drain if (was_speaking or time.monotonic() - self.play_end < drain) else 0.0
        self.app.show_vector(focus=False, greet=False)
        sc = self.app.pscene
        sc.set_state("listening")
        sc.mic_live = True
        self.rec_buf = bytearray()
        self.rec_t0 = time.monotonic()
        self.rec = self.open_mic()
        self.rec_reader = threading.Thread(target=self._read_mic, args=(self.rec,), daemon=True)
        self.rec_reader.start()
        threading.Thread(target=lambda: self._safe(lambda: self._req({"op": "warm"}, 60)), daemon=True).start()
        GLib.timeout_add(int(self.cfg.get("max_record_seconds", 30) * 1000), self._watchdog, self.rec)

    def _watchdog(self, proc):
        if self.rec is proc:
            self.ptt_up()
        return False

    def _read_mic(self, proc):
        sc = self.app.pscene
        first = True
        while True:
            chunk = proc.stdout.read(800)           # 25 ms
            if not chunk:
                break
            if first:
                self.last_stats["mic_start_ms"] = int((time.monotonic() - self.rec_t0) * 1000)
                first = False
            if time.monotonic() < self.skip_until:
                continue                            # VECTOR's voice still leaving the speakers
            self.rec_buf += chunk
            a = np.frombuffer(chunk[: len(chunk) // 2 * 2], np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(a * a))) if len(a) else 0.0
            db = 20 * np.log10(max(rms, 1e-5))
            sc.avatar.level = max(0.0, min(1.0, (db + 55) / 40))

    def ptt_up(self):
        proc, self.rec = self.rec, None
        if not proc:
            return
        sc = self.app.pscene
        sc.mic_live = False
        # people let go of the key on their last syllable: keep listening a beat longer
        tail = self.cfg.get("release_tail_ms", 250) / 1000
        reader = getattr(self, "rec_reader", None)

        def finish():
            self.close_mic(proc, reader)
            secs = time.monotonic() - self.rec_t0
            data = bytes(self.rec_buf)
            self.rec_buf = bytearray()
            if secs < 0.35 or len(data) < 16000 * 2 * 0.3:
                GLib.idle_add(sc.set_state, "idle")
                return
            GLib.idle_add(sc.set_state, "thinking")
            sc.subtitle = "HEARING YOU"
            self.last_stats["ptt_release_t"] = time.monotonic()
            self._transcribe(data)
        threading.Timer(tail, lambda: threading.Thread(target=finish, daemon=True).start()).start()

    def _transcribe(self, data):
        fd, path = tempfile.mkstemp(prefix="vector-ptt-", suffix=".pcm", dir=RUNTIME)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            t0 = time.monotonic()
            out = self._req({"op": "stt", "pcm": path}, 60)
            self.last_stats.update(stt_ms=out.get("ms"), stt_roundtrip_ms=int((time.monotonic() - t0) * 1000),
                                   audio_s=out.get("seconds"), heard=(out.get("text") or "")[:60])
            from . import events
            events.emit("voice.stt", ms=out.get("ms"), audio_s=out.get("seconds"))   # health exporter
            text = (out.get("text") or "").strip()
            if len(re.sub(r"\W", "", text)) < 2:
                GLib.idle_add(self._heard_nothing)
                return
            if self._echo_of_self(text):
                self.last_stats["echo_rejected"] = text[:80]
                GLib.idle_add(self._heard_echo)
                return
            GLib.idle_add(self.app.ask, text)
        except Exception as e:
            GLib.idle_add(self.app._vector_error, f"voice: {e}")
        finally:
            os.unlink(path)

    def _echo_of_self(self, text):
        """True when the transcript is VECTOR's own recent speech coming back through the mic."""
        now = time.monotonic()
        recent = " ".join(s for t, s in self.said if now - t < 30)
        if not recent:
            return False
        words = lambda x: re.findall(r"[a-z0-9']+", x.lower())
        heard, spoke = words(text), words(recent)
        if len(heard) < 2:
            return False
        sset = set(spoke)
        contained = sum(w in sset for w in heard) / len(heard)
        import difflib
        run = difflib.SequenceMatcher(None, heard, spoke, autojunk=False).find_longest_match(0, len(heard), 0, len(spoke)).size
        # a verbatim stretch of its own words; loose word overlap is not enough, because the
        # operator often asks about exactly what VECTOR just said
        return run >= 4 or (run >= 3 and run >= 0.75 * len(heard)) or (contained == 1.0 and len(heard) >= 7)

    def _heard_echo(self):
        self.app.pscene.note("That was me talking, I think. Sorry! Hold SUPER+V and go ahead.")
        self.app.pscene.set_state("idle")

    def _heard_nothing(self):
        self.app.pscene.note("Sorry, I didn't catch that. Hold SUPER+V while you talk.")
        self.app.pscene.set_state("idle")

    # ------------------------------------------------------------------ speaking
    # ------------------------------------------------------------------ voice mode
    def _read_mode(self):
        try:
            with open(MODE) as f:
                m = f.read().strip()
            return m if m == "auto" or m in self.roles else "auto"
        except OSError:
            return "auto"

    def set_mode(self, mode):
        """auto | a role | cycle. Persisted; the next sentence uses it."""
        order = ["auto"] + [r for r in self.roles if r != "notify"]
        if mode == "cycle":
            mode = order[(order.index(self.mode) + 1) % len(order)] if self.mode in order else "auto"
        if mode != "auto" and mode not in self.roles:
            raise ValueError(f"voice must be auto or one of {list(self.roles)}")
        self.mode = mode
        self.splitter.mode = mode
        os.makedirs(STATE, exist_ok=True)
        with open(MODE, "w") as f:
            f.write(mode + "\n")
        return mode

    # ------------------------------------------------------------------ speaking
    def feed(self, delta):
        """Streamed reply text: speak each sentence, in its voice, as soon as it is complete."""
        for unit in self.splitter.feed(delta):
            self._unit(unit)

    def flush(self):
        for unit in self.splitter.flush():
            self._unit(unit)
        self.splitter.reset()

    def _unit(self, unit):
        role, text, mood = unit
        if mood and self.app and getattr(self.app, "pscene", None):
            GLib.idle_add(self.app.pscene.set_mood, mood)
        if not self.muted:
            self._enqueue(text, role)

    def say(self, text, role="main"):
        """A whole line (the greeting, a fixed phrase). Never while the mic is open."""
        if not self.muted and not self.rec:
            for m in SENT_END.finditer(text.strip() + " "):
                self._enqueue(m.group(1), role)

    def _enqueue(self, sentence, role="main"):
        from .text import spoken
        s = re.sub(r"\s+", " ", spoken(sentence)).strip()
        if not re.search(r"\w", s):
            return
        with self.qlock:
            self.queue.append((self.gen, role, s))
            if not self.speaking:
                self.speaking = True
                threading.Thread(target=self._speak_loop, daemon=True).start()

    # ------------------------------------------------------------------ playback
    # One reply plays as ONE continuous output stream. A producer streams each sentence's
    # PCM from the server, back to back (the next sentence starts generating the moment the
    # last one finishes, ~2x faster than it plays), into a buffer; a player feeds a single
    # pw-play from that buffer after a small prebuffer, paced to stay ~0.25 s ahead, so
    # there is no gap between chunks or sentences and barge-in stops within ~0.25 s.
    PREBUFFER_S = 0.3
    AHEAD_S = 0.25

    def _speak_loop(self):
        buf = _PcmBuffer()
        player = threading.Thread(target=self._player, args=(buf, self.gen), daemon=True, name="vector-player")
        started = False
        try:
            while True:
                with self.qlock:
                    if not self.queue:
                        break
                    gen, role, sentence = self.queue.pop(0)
                if gen != self.gen or self.muted:
                    continue
                cue = role != self.voice_now and self.cfg.get("cue", {}).get("enabled", True)
                if not self.rec:                      # never speak into an open mic
                    sr = self._produce(sentence, gen, role, cue, buf)
                    if sr and not started:
                        buf.sr = sr
                        player.start()
                        started = True
                self.voice_now = role
                for _ in range(60):                   # the next sentence of this reply is usually close behind
                    if self.queue or gen != self.gen:
                        break
                    time.sleep(0.01)
        except Exception as e:
            import traceback
            traceback.print_exc()
            GLib.idle_add(self.app.pscene.note, f"(voice trouble: {str(e)[:80]})")
        finally:
            buf.close()
            if started:
                player.join(30)
            with self.qlock:
                self.speaking = False
            GLib.idle_add(self._done_speaking)

    def _open_stream(self, sentence, role="main", cue=False):
        for attempt in range(2):
            try:
                s = socket.socket(socket.AF_UNIX)
                s.settimeout(30)
                s.connect(SOCK)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if attempt:
                    raise
                self._spawn()
        add = 0.0
        sc = getattr(self.app, "pscene", None) if self.app else None
        if sc is not None:
            add = float(self.cfg.get("mood_shimmer", {}).get(sc.mood_name(), 0.0))
        s.sendall((json.dumps({"op": "tts_stream", "text": sentence, "role": role, "cue": cue,
                               "shimmer_add": add}) + "\n").encode())
        f = s.makefile("rb")
        head = json.loads(f.readline().decode() or "{}")
        if head.get("error") or not head.get("sr"):
            s.close()
            raise RuntimeError(head.get("error", "no audio"))
        return s, f, head

    def _produce(self, sentence, gen, role, cue, buf):
        """Stream one sentence's PCM into the playback buffer; returns its sample rate."""
        t0 = time.monotonic()
        s, f, head = self._open_stream(sentence, role, cue)
        sr = int(head["sr"])
        self.last_stats.update(tts_engine=head.get("engine"), tts_cached=head.get("cached"))
        now = time.monotonic()
        self.said = [(t, x) for t, x in self.said if now - t < 30] + [(now, sentence)]
        first = True
        try:
            while gen == self.gen:
                b = f.read1(9600) if hasattr(f, "read1") else f.read(9600)
                if not b:
                    break
                if first:
                    self.last_stats["tts_first_audio_ms"] = int((time.monotonic() - t0) * 1000)
                    first = False
                    from . import events
                    events.emit("voice.first_audio", ms=self.last_stats["tts_first_audio_ms"], role=role,
                                cached=bool(head.get("cached")))
                buf.put(b, role)
        except OSError:
            pass
        finally:
            s.close()
        return sr

    def _player(self, buf, gen):
        """Feed one pw-play for the whole reply, paced against a playback clock."""
        sr = buf.sr
        bps = sr * 2
        if not buf.wait(int(self.PREBUFFER_S * bps), timeout=10):    # prebuffer (or the whole reply if shorter)
            if not buf.size():
                return
        sink = self.playback_sink()
        sc = self.app.pscene
        GLib.idle_add(sc.set_state, "speaking")
        self._plays = getattr(self, "_plays", 0) + 1
        self.player_node = f"vector-voice-{os.getpid()}-{self._plays}"     # finds this stream in PipeWire
        self.player = subprocess.Popen(["pw-play"] + (["--target", sink] if sink else []) +
                                       ["-P", f"{{ node.name = {self.player_node} }}",
                                        "--raw", "--rate", str(sr), "--channels", "1", "--format", "s16",
                                        "--latency", "60ms", "-"],
                                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.play_start = time.monotonic()
        if sink == self.aec_sink:
            threading.Thread(target=self._keep_routed, args=(self.player, self.player_node, sink), daemon=True,
                             name="vector-route").start()
        t_start = time.monotonic()
        sent = 0                     # bytes written
        underrun = 0.0
        piece = int(0.04 * bps) // 2 * 2
        shown = None
        try:
            while gen == self.gen:
                played = (time.monotonic() - t_start - underrun) * bps
                if sent - played > self.AHEAD_S * bps:
                    time.sleep(0.01)
                    continue
                data, role = buf.take(piece, timeout=0.05)
                if not data:
                    if buf.closed and not buf.size():
                        break
                    if sent < played:                 # we fell behind: the clock pauses with the sound
                        underrun += 0.05
                        self.last_stats["underruns"] = self.last_stats.get("underruns", 0) + 1
                    continue
                if role != shown:                     # the tint follows what is actually playing
                    GLib.idle_add(sc.set_voice, role, shown is not None)
                    shown = role
                a = np.frombuffer(data, np.int16).astype(np.float32) / 32768.0
                rms = float(np.sqrt(np.mean(a * a))) if len(a) else 0.0
                sc.audio_level = float(np.clip((20 * np.log10(max(rms, 1e-5)) + 50) / 38, 0, 1))
                try:
                    self.player.stdin.write(data)
                    self.player.stdin.flush()
                except (BrokenPipeError, ValueError, OSError):
                    break
                sent += len(data)
            if gen == self.gen:
                try:
                    self.player.stdin.close()
                except OSError:
                    pass
                while self.player.poll() is None and gen == self.gen:
                    time.sleep(0.02)
            if self.player.poll() is None:
                self.player.kill()
        finally:
            sc.audio_level = None
            self.play_end = time.monotonic()
            self.last_stats["played_s"] = round(sent / bps, 2)

    def playback_sink(self):
        """Where his voice plays: voice.json "sink" if set; else the AEC sink whenever echo
        cancellation is on (the canceller needs his voice as its reference, or his own voice
        reaches the mic and sounds like the host talking over him); else the default sink.
        The default sink itself is never changed."""
        if self.cfg.get("sink"):
            return self.cfg["sink"]
        if self.cfg.get("echo_cancel", True) and not self.cfg.get("mic_source"):
            if self.aec or self.ensure_aec():
                return self.aec_sink
        return None

    def _stream_sink(self, node):
        """(sink-input id, sink name) of the playback stream with this node name, or None."""
        try:
            ins = json.loads(subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"], capture_output=True,
                                            text=True, timeout=3).stdout or "[]")
            sinks = {str(x.get("index")): x.get("name") for x in json.loads(
                subprocess.run(["pactl", "-f", "json", "list", "short", "sinks"], capture_output=True, text=True,
                               timeout=3).stdout or "[]")}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return None
        for x in ins:
            if (x.get("properties") or {}).get("node.name") == node:
                return str(x.get("index")), sinks.get(str(x.get("sink")))
        return None

    def _keep_routed(self, proc, node, sink):
        """While this reply plays, keep it on the AEC sink: if PipeWire linked it elsewhere (the
        AEC pair was rebuilt, or the target wasn't ready), move it back. Default sink untouched."""
        while proc.poll() is None:
            got = self._stream_sink(node)
            if got and got[1] and got[1] != sink:
                if self.aec or self.ensure_aec():
                    self._pactl("move-sink-input", got[0], sink)
                    self.reroutes += 1
                    print(time.strftime("%H:%M:%S"), "voice: playback was on", got[1], "- moved to", sink, flush=True)
            time.sleep(0.2)

    def warm_tts(self):
        """Load the voice in the background (when the window opens), so the first reply is quick;
        then pre-render the hand-off acknowledgements in every voice (once; they're cached)."""
        def work():
            self._safe(lambda: self._req({"op": "warm_tts"}, 10))
            lanes = self.cfg.get("lanes", {})
            if getattr(self, "_acks_done", False) or lanes.get("voice") == lanes.get("deep"):
                return
            from .brain_pai import ACKS
            for _ in range(240):                        # wait for the voice (the lab's, or the GPU's), then render
                p = self._req({"op": "ping"}, 5)
                if p.get("qwen") or (p.get("engine") == "lab" and p.get("lab")):
                    break
                time.sleep(0.5)
            for role in self.roles:
                for line in ACKS:
                    self._safe(lambda: self._req({"op": "tts", "text": line, "role": role}, 90))
            self._acks_done = True
        threading.Thread(target=work, daemon=True).start()

    def _done_speaking(self):
        sc = self.app.pscene
        self.voice_now = "main"
        sc.set_voice("main", False)
        sc.audio_level = None
        if sc.avatar.state == "speaking" and not sc.revealing():
            sc.set_state("idle")
        self.app.last_activity = time.monotonic()

    def stop(self):
        self.gen += 1
        with self.qlock:
            self.queue.clear()
        self.pending = ""
        self.splitter.reset()
        self.voice_now = "main"
        if self.player and self.player.poll() is None:
            self.player.kill()                       # SIGKILL: no fade-out tail into the mic
            try:
                self.player.wait(0.5)
            except subprocess.TimeoutExpired:
                pass
            self.play_end = time.monotonic()

    # ------------------------------------------------------------------ misc
    def set_mute(self, on):
        """Muted or not, explicitly (bromigos mute: the desktop mute keeps his voice in step
        with the theme's sounds); a no-op when already so."""
        if bool(on) != self.muted:
            self.toggle_mute()
        return self.muted

    def toggle_mute(self):
        self.muted = not self.muted
        os.makedirs(STATE, exist_ok=True)
        if self.muted:
            open(MUTED, "w").close()
            self.stop()
        elif os.path.exists(MUTED):
            os.unlink(MUTED)

    def active(self):
        return bool(self.rec) or self.speaking

    def status(self):
        return {"muted": self.muted, "recording": bool(self.rec), "speaking": self.speaking,
                "mode": self.mode, "voice": self.voice_now,
                "engine": self.cfg.get("engine"), **self.last_stats}

    @staticmethod
    def _safe(fn):
        try:
            fn()
        except Exception:
            pass


class _PcmBuffer:
    """Bytes handed from the producer to the player, with the role that spoke them."""

    def __init__(self):
        self.parts = []          # [(bytes, role)]
        self.n = 0
        self.closed = False
        self.sr = 24000
        self.cv = threading.Condition()

    def put(self, b, role):
        with self.cv:
            if len(b) % 2 and self.parts:                  # keep samples whole across reads
                pb, pr = self.parts[-1]
                self.parts[-1] = (pb + b[:1], pr)
                b = b[1:]
            self.parts.append((b, role))
            self.n += len(b)
            self.cv.notify_all()

    def size(self):
        with self.cv:
            return self.n

    def wait(self, nbytes, timeout):
        end = time.monotonic() + timeout
        with self.cv:
            while self.n < nbytes and not self.closed:
                left = end - time.monotonic()
                if left <= 0:
                    return False
                self.cv.wait(left)
            return self.n >= nbytes or self.closed

    def take(self, nbytes, timeout=0.05):
        with self.cv:
            if not self.parts:
                self.cv.wait(timeout)
            out, role = bytearray(), None
            while self.parts and len(out) < nbytes:
                b, r = self.parts[0]
                role = role or r
                k = nbytes - len(out)
                out += b[:k]
                if len(b) > k:
                    self.parts[0] = (b[k:], r)
                else:
                    self.parts.pop(0)
            if len(out) % 2:                               # never split a sample
                self.parts.insert(0, (bytes(out[-1:]), role))
                out = out[:-1]
            self.n -= len(out)
            return bytes(out), role

    def close(self):
        with self.cv:
            self.closed = True
            self.cv.notify_all()
