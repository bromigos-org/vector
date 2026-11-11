"""VECTOR's local voice: Qwen3-TTS 0.6B (8-bit GGUF, qwentts.cpp) on this machine's NVIDIA card
for speech, and NVIDIA Parakeet TDT 0.6B v2 (int8 ONNX) on the processor for hearing, in a
process of its own so that leaving frees every byte of the card.

Two halves:

  Worker   `python -m holo.local_voice` in the voice venv. Loads both models (about 2.5 GB of
           the card, about 4 s), answers on its own unix socket, and exits after `idle_exit_s`
           (600 s) with nothing to do, when told to, or when the voice server that started it
           is gone. One JSON request per connection:
               {"op": "ping"}                                  -> {"ok", "tts", "stt"}
               {"op": "tts_stream", "text", "ref", "ref_text"}  -> a JSON header line {"sr"},
                                                                  then mono s16le PCM
               {"op": "stt", "pcm": path}                       -> {"text", "ms"}
               {"op": "quit"}
  Manager  inside the voice server (holo/voice_server.py). Starts the worker on his first
           speech (or the first time you talk to him), watches for games (holo/gamewatch.py),
           stops the worker at once when one starts and keeps it away until the game has been
           gone for `game_clear_s` (30 s). While the worker isn't ready, the voice server's
           next engine speaks (the lab, then Kokoro) and the lab hears.

The setting is holo/local_voice_state.py's (on, off, auto). The models are holo/local_models.py's,
fetched once into ~/.cache/bromigos/local-voice and checked by sha256. With
BROMIGOS_LOCAL_VOICE_FAKE=1 the worker loads nothing and makes a test tone, for the offline
tests (tools/test-local-voice.py).
"""
import glob
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

import numpy as np

from . import gamewatch, local_models, local_voice_state

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
SOCK = os.environ.get("BROMIGOS_LOCAL_VOICE_SOCK") or os.path.join(RUNTIME, "bromigos-holo-local-voice.sock")
STATE = local_voice_state.STATE
LOG = os.path.join(STATE, "holo-local-voice.log")
TTS_MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
DEFAULTS = {
    "idle_exit_s": 600,          # the worker leaves after ten idle minutes
    "game_clear_s": 30,          # a game gone this long: the local voice may come back
    "watch_s": 3,                # how often the game signs are read
    "first_audio_timeout_s": 2.5,
    "start_timeout_s": 120,
    "temperature": 0.7,
    "chunk_size": 4,
    "stt_threads": 8,
    "min_vram_mib": local_voice_state.MIN_VRAM_MIB,
    "gpu_min_mib": 64,
    "gpu_allow": [],
}
FAKE = os.environ.get("BROMIGOS_LOCAL_VOICE_FAKE") == "1"


def log(*a):
    print(time.strftime("%H:%M:%S"), "local-voice:", *a, flush=True)


def settings(cfg):
    return {**DEFAULTS, **((cfg or {}).get("local") or {})}


def _s16(a):
    return (np.clip(np.asarray(a, np.float32), -1, 1) * 32767).astype(np.int16).tobytes()


def _request(sock, obj, timeout):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    s.connect(sock)
    s.sendall((json.dumps(obj) + "\n").encode())
    return s


def _read_line(s):
    buf = b""
    while not buf.endswith(b"\n"):
        b = s.recv(1)
        if not b:
            break
        buf += b
    return json.loads(buf.decode() or "{}")


def max_tokens(text):
    """A ceiling on the codec frames (12.5 a second) for a line: two and a half times what the
    text needs at a calm 14 characters a second, plus two seconds. The small model sometimes
    misses the end of a very short line ("One moment.") and talks on for minutes; this stops it
    within seconds and never cuts a real line short."""
    return int(min(2048, max(48, 12.5 * (2.0 + 2.5 * len(text) / 14))))


# ====================================================================== the worker
class Worker:
    def __init__(self, cfg):
        self.c = settings(cfg)
        self.tts = None
        self.stt_model = None
        self.tts_lock = threading.Lock()
        self.stt_lock = threading.Lock()
        self.last = time.monotonic()
        self.parent = os.getppid()
        self.sr = 24000

    def _cuda12(self):
        """qwentts.cpp's wheel is built for CUDA 12: load its runtime and cuBLAS from the venv's
        nvidia packages first, so nothing on the system has to provide them."""
        import ctypes
        for sp in glob.glob(os.path.join(sys.prefix, "lib/python3*/site-packages")):
            for rel in ("nvidia/cuda_runtime/lib/libcudart.so.12", "nvidia/cublas/lib/libcublasLt.so.12",
                        "nvidia/cublas/lib/libcublas.so.12"):
                p = os.path.join(sp, rel)
                if os.path.exists(p):
                    try:
                        ctypes.CDLL(p, mode=ctypes.RTLD_GLOBAL)
                    except OSError as e:
                        log("couldn't preload", rel, e)

    def load_tts(self):
        t0 = time.monotonic()
        if FAKE:
            time.sleep(float(os.environ.get("BROMIGOS_LOCAL_VOICE_FAKE_LOAD_S", "0")))
            self.tts = "fake"
            return
        self._cuda12()
        from faster_qwen3_tts import FasterQwen3TTS
        self.tts = FasterQwen3TTS.from_pretrained(
            TTS_MODEL, backend="ggml",
            gguf_talker_path=local_models.path("tts", "qwen-talker-0.6b-base-Q8_0.gguf"),
            gguf_codec_path=local_models.path("tts", "qwen-tokenizer-12hz-Q8_0.gguf"),
            qwentts_ref_cache_dir=os.path.join(local_models.CACHE, "refs"))
        log(f"speech ready in {time.monotonic() - t0:.1f}s ({TTS_MODEL}, Q8_0 GGUF)")

    def load_stt(self):
        t0 = time.monotonic()
        if FAKE:
            self.stt_model = "fake"
            return
        import onnx_asr
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(self.c["stt_threads"])
        self.stt_model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2", local_models.where("stt"),
                                             quantization="int8", providers=["CPUExecutionProvider"],
                                             sess_options=so)
        log(f"hearing ready in {time.monotonic() - t0:.1f}s (Parakeet TDT 0.6B v2, int8, processor)")

    def speak(self, text, ref, ref_text):
        """Yields (float32 chunk, sr)."""
        if FAKE:
            sr = 24000
            for w in text.split()[:40]:
                t = np.arange(int(sr * 0.08)) / sr
                yield (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), sr
            return
        for chunk, sr, _ in self.tts.generate_voice_clone_streaming(
                text=text, language="English", ref_audio=ref, ref_text=ref_text, max_new_tokens=max_tokens(text),
                chunk_size=int(self.c["chunk_size"]), temperature=float(self.c["temperature"])):
            yield np.asarray(chunk, np.float32).reshape(-1), sr

    @staticmethod
    def voiced(gen, level=0.01, lead_s=2.0, tail_s=1.2):
        """The render, held back until it has a voice, and ended once the voice has been silent
        for `tail_s`. The small model now and then renders a short line as silence (seen with
        some reference voices); a render with no voice in its first `lead_s` yields nothing, so
        the caller can try again or hand the line to the next engine."""
        held, held_s, spoke, quiet = [], 0.0, False, 0.0
        try:
            for a, sr in gen:
                win = max(1, sr // 10)
                for i in range(0, len(a), win):
                    loud = float(np.sqrt(np.mean(np.square(a[i:i + win])))) > level if len(a[i:i + win]) else False
                    if loud:
                        spoke, quiet = True, 0.0
                    elif spoke:
                        quiet += len(a[i:i + win]) / sr
                if not spoke:
                    held.append((a, sr))
                    held_s += len(a) / sr
                    if held_s >= lead_s:
                        return
                    continue
                if held:
                    yield np.concatenate([h for h, _ in held] + [a]), sr
                    held = []
                else:
                    yield a, sr
                if quiet >= tail_s:
                    return
        finally:
            gen.close()

    def hear(self, pcm_path):
        raw = np.fromfile(pcm_path, dtype=np.int16)
        if FAKE:
            return f"fake heard {len(raw)} samples"
        return self.stt_model.recognize(raw.astype(np.float32) / 32768.0, sample_rate=16000).strip()

    def handle(self, req, conn):
        op = req.get("op")
        self.last = time.monotonic()
        if op == "ping":
            return {"ok": True, "tts": self.tts is not None, "stt": self.stt_model is not None, "pid": os.getpid()}
        if op == "quit":
            log("leaving:", req.get("why") or "asked to")
            conn.sendall(b'{"ok": true}\n')
            os._exit(0)
        if op == "stt":
            if self.stt_model is None:
                return {"error": "hearing is still loading"}
            t0 = time.monotonic()
            with self.stt_lock:
                text = self.hear(req["pcm"])
            return {"text": text, "ms": int((time.monotonic() - t0) * 1000)}
        if op in ("tts_stream", "warm"):
            if self.tts is None:
                return {"error": "speech is still loading"}
            with self.tts_lock:
                t0 = time.monotonic()
                text = req.get("text") or "One moment."
                if op == "warm":
                    n = sum(len(a) for a, _ in self.speak(text, req["ref"], req.get("ref_text", "")))
                    log(f"warmed {os.path.basename(req['ref'])} in {time.monotonic() - t0:.1f}s ({n / self.sr:.1f}s of audio)")
                    return {"ok": True}
                for attempt in (1, 2):
                    it = self.voiced(self.speak(text, req["ref"], req.get("ref_text", "")))
                    first = next(it, None)
                    if first is not None:
                        break
                    log(f"no voice in a render of {len(text)} characters (try {attempt} of 2)")
                if first is None:
                    return {"error": "it made no voice for that line"}
                conn.sendall((json.dumps({"sr": first[1]}) + "\n").encode())
                try:
                    conn.sendall(_s16(first[0]))
                    for a, _ in it:
                        conn.sendall(_s16(a))
                except (BrokenPipeError, ConnectionResetError):
                    it.close()            # barge-in: stop the render, ready for the next line
            self.last = time.monotonic()
            return None
        return {"error": "unknown op"}

    def janitor(self):
        while True:
            time.sleep(1 if FAKE else 5)
            if os.getppid() != self.parent:
                log("leaving: the voice server is gone")
                os._exit(0)
            if time.monotonic() - self.last > float(self.c["idle_exit_s"]):
                log(f"leaving: idle for {int(self.c['idle_exit_s'])} s")
                os._exit(0)

    def _client(self, c):
        try:
            buf = b""
            while not buf.endswith(b"\n"):
                b = c.recv(65536)
                if not b:
                    break
                buf += b
            if not buf.strip():
                return
            try:
                out = self.handle(json.loads(buf.decode()), c)
            except Exception as e:
                out = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            if out is not None:
                c.sendall((json.dumps(out) + "\n").encode())
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            c.close()

    def serve(self):
        import fcntl
        self._lock = open(SOCK + ".lock", "w")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log("another local voice holds the lock; leaving")
            os._exit(0)
        signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
        try:
            os.unlink(SOCK)
        except FileNotFoundError:
            pass
        threading.Thread(target=self.janitor, daemon=True).start()
        try:
            self.load_tts()
        except Exception as e:
            log("speech failed to load:", e)
            os._exit(3)
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(SOCK + ".new")
        os.chmod(SOCK + ".new", 0o600)
        os.replace(SOCK + ".new", SOCK)         # the socket appears only once speech is ready
        srv.listen(8)

        def stt():
            try:
                self.load_stt()
            except Exception as e:
                log("hearing failed to load (the lab hears instead):", e)
        threading.Thread(target=stt, daemon=True).start()
        log("ready", SOCK)
        while True:
            c, _ = srv.accept()
            threading.Thread(target=self._client, args=(c,), daemon=True).start()


# ====================================================================== the manager
class Manager:
    """The voice server's side: whether the local voice may speak now, its worker's life, and
    the status line. state: off | unavailable | preparing | stopped | starting | ready | away | failed."""

    def __init__(self, cfg, log=log, publish=None, watch=None, sock=SOCK, python=sys.executable):
        self.c = settings(cfg)
        self.log = log
        self.publish = publish or (lambda: None)
        self.sock = sock
        self.python = python
        self.gw = watch or gamewatch.GameWatch(self.c)
        self.gw.own.add(os.getpid())
        self.proc = None
        self.state, self.why = "stopped", None
        self.lock = threading.Lock()
        self.last_game = 0.0
        self.fails = []
        self.failed_until = 0.0
        self.prepared_at = 0.0
        self.preparing = False
        self._on = (False, "")
        self._on_at = 0.0
        self.warm_ref = None            # (wav, transcript): warmed as soon as the worker is up

    # ------------------------------------------------------------------ may it speak?
    def setting(self):
        """(on, why), re-read at most every 10 s (it shells out to nvidia-smi)."""
        if time.monotonic() - self._on_at > 10:
            self._on = local_voice_state.enabled(int(self.c["min_vram_mib"]))
            self._on_at = time.monotonic()
        return self._on

    def wanted(self):
        """The local voice goes first in his engines: it's on here (it may still be loading,
        stepped aside for a game or starting; then the next engine answers that line)."""
        return self.setting()[0] and self.state not in ("unavailable",)

    def ready(self):
        return self.state == "ready" and self.proc is not None and self.proc.poll() is None

    def _set(self, state, why=None):
        if (state, why) != (self.state, self.why):
            self.state, self.why = state, why
            self.log(f"local voice {state}" + (f": {why}" if why else ""))
            self.publish()

    def _runtime_ok(self):
        if FAKE:
            return True
        import importlib.util
        return all(importlib.util.find_spec(m) is not None for m in ("qwentts_cpp", "faster_qwen3_tts", "onnx_asr"))

    def _prepare(self):
        """Once, in the background: the runtime into his voice venv and the models into the cache.
        His own work, never the user's programs; it runs at a low priority."""
        if self.preparing or time.monotonic() - self.prepared_at < 3600 and self.prepared_at:
            return
        self.preparing = True
        self.prepared_at = time.monotonic()

        def go():
            try:
                if not self._runtime_ok():
                    req = os.path.join(HERE, "requirements", "local-voice.txt")
                    uv = shutil.which("uv")
                    argv = ([uv, "pip", "install", "--python", self.python, "-r", req] if uv else
                            [self.python, "-m", "pip", "install", "-r", req])
                    self._set("preparing", "installing its runtime into the voice environment (once)")
                    with open(LOG, "a") as out:
                        r = subprocess.run(["nice", "-n", "10", *argv], stdout=out, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL)
                    if r.returncode != 0 or not self._runtime_ok():
                        self._set("unavailable", "its runtime didn't install (bromigos-holo setup local-voice)")
                        return
                if not local_models.present():
                    mib = local_models.missing_bytes() >> 20
                    self._set("preparing", f"fetching its models, {mib} MiB (once)")
                    local_models.fetch(log=self.log)
                self._set("stopped", None)
            except Exception as e:
                self._set("unavailable", f"couldn't prepare: {str(e)[:160]}")
            finally:
                self.preparing = False
        threading.Thread(target=go, daemon=True, name="local-voice-prepare").start()

    # ------------------------------------------------------------------ the worker's life
    def ensure(self):
        """Start the worker if the local voice may run now and it isn't running. Never waits."""
        on, why = self.setting()
        if not on:
            return False
        with self.lock:
            if self.state in ("away", "preparing") or time.monotonic() < self.failed_until:
                return False
            if self.proc is not None and self.proc.poll() is None:
                return self.state == "ready"
            if not (self._runtime_ok() and (FAKE or local_models.present())):
                self._prepare()
                return False
            os.makedirs(STATE, exist_ok=True)
            env = dict(os.environ, BROMIGOS_LOCAL_VOICE_SOCK=self.sock,
                       BROMIGOS_LOCAL_VOICE_CFG=json.dumps({"local": self.c}))     # one source of settings
            self.proc = subprocess.Popen([self.python, "-m", "holo.local_voice"], cwd=HERE, env=env,
                                         stdin=subprocess.DEVNULL, stdout=open(LOG, "a"), stderr=subprocess.STDOUT)
            self.gw.own.add(self.proc.pid)
            self._set("starting", None)
        threading.Thread(target=self._wait_ready, args=(self.proc,), daemon=True).start()
        return False

    def _wait_ready(self, proc):
        t0 = time.monotonic()
        while time.monotonic() - t0 < float(self.c["start_timeout_s"]):
            if proc.poll() is not None:
                self._crashed(f"it exited while loading (code {proc.returncode})")
                return
            try:
                s = _request(self.sock, {"op": "ping"}, 2)
                p = _read_line(s)
                s.close()
                if p.get("tts"):
                    if self.warm_ref:
                        # his reference voice first: a new reference costs its one-time extraction
                        # here (cached on disk after), not on a line he's waiting to say
                        self._warm(*self.warm_ref)
                    if proc is self.proc and proc.poll() is None:
                        self._set("ready", None)
                        self.log(f"local voice ready in {time.monotonic() - t0:.1f}s")
                    return
            except OSError:
                pass
            time.sleep(0.2)
        self.stop("it took too long to load")
        self._crashed("it took too long to load")

    def _warm(self, ref, ref_text):
        try:
            s = _request(self.sock, {"op": "warm", "ref": ref, "ref_text": ref_text}, 90)
            _read_line(s)
            s.close()
        except (OSError, ValueError):
            pass

    def _crashed(self, why):
        now = time.monotonic()
        self.fails = [t for t in self.fails if now - t < 600] + [now]
        if len(self.fails) >= 3:
            self.failed_until = now + 600
            self._set("failed", f"{why}; resting ten minutes")
        else:
            self._set("stopped", why)

    def stop(self, why):
        """Tell the worker to leave; make sure it has. Its memory is the card's again."""
        with self.lock:
            p, self.proc = self.proc, None
        if p is None or p.poll() is not None:
            return
        try:
            s = _request(self.sock, {"op": "quit", "why": why}, 1)
            s.recv(64)
            s.close()
        except OSError:
            pass
        for sig, wait in ((None, 1.0), (signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0)):
            if sig:
                try:
                    p.send_signal(sig)
                except OSError:
                    pass
            try:
                p.wait(wait)
                break
            except subprocess.TimeoutExpired:
                continue
        self.gw.own.discard(p.pid)

    def tick(self):
        """One look: the setting, games, the worker. The watcher runs this every `watch_s`."""
        on, why = self.setting()
        if not on:
            if self.proc is not None:
                self.stop("turned off")
            self._set("off" if local_voice_state.setting() == "off" else "unavailable", why)
            return
        if self.state in ("off", "unavailable") and not self.preparing:
            self._set("stopped", None)
        game = self.gw.check()
        now = time.monotonic()
        if game:
            self.last_game = now
            if self.proc is not None and self.proc.poll() is None:
                self.stop(game)
            self._set("away", game)
            return
        if self.state == "away":
            if now - self.last_game < float(self.c["game_clear_s"]):
                return
            self._set("stopped", "the game ended; it starts when he next speaks")
        p = self.proc
        if p is not None and p.poll() is not None:
            with self.lock:
                if self.proc is p:
                    self.proc = None
            self.gw.own.discard(p.pid)
            if self.state in ("ready", "starting"):
                if p.returncode == 0:
                    self._set("stopped", "idle")
                else:
                    self._crashed(f"it exited (code {p.returncode})")

    def watch(self):
        def loop():
            while True:
                try:
                    self.tick()
                except Exception as e:
                    self.log("local voice watcher:", e)
                time.sleep(float(self.c["watch_s"]))
        threading.Thread(target=loop, daemon=True, name="local-voice-watch").start()

    # ------------------------------------------------------------------ speaking and hearing
    def tts(self, text, ref, ref_text):
        """Yields (float32 chunk, sr) from the worker. Raises when it isn't ready (the caller's
        next engine speaks) and starts it for the next line."""
        self.warm_ref = (ref, ref_text)
        if not self.ready():
            self.ensure()
            raise RuntimeError(f"local voice {self.state}" + (f" ({self.why})" if self.why else ""))
        try:
            s = _request(self.sock, {"op": "tts_stream", "text": text, "ref": ref, "ref_text": ref_text},
                         float(self.c["first_audio_timeout_s"]))
            head = _read_line(s)
            if head.get("error") or "sr" not in head:
                s.close()
                raise RuntimeError(head.get("error") or "no header")
        except (OSError, ValueError) as e:
            self._check_alive()
            raise RuntimeError(f"local voice didn't answer: {e}") from None
        return self._pcm(s, int(head["sr"]))

    def _pcm(self, s, sr):
        rest = b""
        try:
            s.settimeout(10)
            while True:
                b = s.recv(9600)
                if not b:
                    break
                b = rest + b
                n = len(b) // 2 * 2
                rest = b[n:]
                if n:
                    yield np.frombuffer(b[:n], np.int16).astype(np.float32) / 32768.0, sr
        finally:
            s.close()

    def stt(self, pcm_path, wait_s=20.0):
        """The text, or None (not ready: the lab hears; it starts for next time). While it's
        starting (your key press started it), wait up to `wait_s` rather than load another model."""
        if not self.ready():
            self.ensure()
            t0 = time.monotonic()
            while self.state == "starting" and time.monotonic() - t0 < wait_s:
                time.sleep(0.05)
            if not self.ready():
                return None
        try:
            s = _request(self.sock, {"op": "stt", "pcm": pcm_path}, 15)
            out = _read_line(s)
            s.close()
        except (OSError, ValueError):
            self._check_alive()
            return None
        return None if out.get("error") else out.get("text", "")

    def _check_alive(self):
        p = self.proc
        if p is not None and p.poll() is not None:
            self.tick()

    def status(self):
        return {"state": self.state, "why": self.why, "setting": local_voice_state.setting(),
                "pid": self.proc.pid if self.proc is not None and self.proc.poll() is None else None}


if __name__ == "__main__":
    try:
        _cfg = json.loads(os.environ["BROMIGOS_LOCAL_VOICE_CFG"])      # the manager's settings
    except (KeyError, ValueError):
        from .paths import conf
        try:
            with open(conf("voice.json")) as f:
                _cfg = json.load(f)
        except (OSError, ValueError):
            _cfg = {}
    Worker(_cfg).serve()
