"""VECTOR's ears and voice: a small server in the voice venv (faster-whisper, Qwen3-TTS via
faster-qwen3-tts, kokoro-onnx), spawned on demand by the desktop daemon and talked to
over a unix socket, one JSON request per connection:

    {"op": "warm"}                      load speech-to-text now (on push-to-talk press)
    {"op": "warm_tts"}                  load the voice now (when VECTOR's window opens)
    {"op": "stt", "pcm": path}          16 kHz mono s16 raw -> {"text", "ms"}
    {"op": "tts", "text": "...", "role": r}        -> {"wav": path, "ms", "cached", "engine"}
    {"op": "tts_stream", "text": "...", "role": r, "shimmer_add": x, "cue": bool}
                                        -> one JSON header line {"sr", "engine", "cached"}, then raw
                                           mono s16le PCM until the socket closes; with cue, the
                                           dial-scratch transition comes first in the same stream
    roles (voice.json "voices"): main, robot, scientist, floor, notify
    {"op": "ping"}

Voice (voice.json "engine", then "fallback": one engine or a list, tried in order):
  lab      the homelab's voice service (homelab chart `voice`: the same Qwen3-TTS and Whisper on
           the 5090): each role's reference is uploaded once by content hash, then lines stream
           from POST /v1/tts/stream. The URL is voice.json lab.url, else bromigos-homelab's
           `voice` capability, else the private overlay's tts endpoint. A lab that's down,
           loading or slower than lab.first_audio_timeout_s to start falls through to the next
           engine, and nothing loads on this GPU unless that happens. With stt.provider "lab",
           speech-to-text goes there first too (local whisper is the fallback).
  qwen     Qwen3-TTS 1.7B (Apache-2.0) speaking in VECTOR's own designed voice: the voice
           was designed once from a text description with the VoiceDesign model (no one's
           recording), saved as voices/vector-ref.wav, and every line is spoken by the Base
           model from that reference. Streams: ~0.2 s to first audio, ~2.3x realtime on
           the RTX 5070, ~4.7 GiB VRAM while loaded.
  kokoro   Kokoro-82M on the CPU, a blend of stock British male voices; the fallback.
  breeze   the homelab's Breeze TTS 2 (voice designed by instruction); too slow to talk live.
  fish     Fish Audio, only with a reference_id the operator owns (his own recording).
Every engine goes through the projector shimmer (holo/shimmer.py, voice.json "shimmer").
If the main engine fails or is still loading, the `fallback` engine answers.

Every finished line is cached as a wav by a hash of the engine settings and the text, so
repeats cost nothing. Models are unloaded after `unload_after_seconds` idle; the process
exits after `exit_after_seconds` idle. Nothing here listens to the microphone.

Run: ~/.local/share/bromigos/venv-tts/bin/python -m holo.voice_server
"""
import hashlib
import http.client
import io
import json
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave


import numpy as np

from . import targets
from .private import PRIV
from .shimmer import SilenceShaper, Shimmer, TimeStretch, dial_scratch

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from .paths import conf  # noqa: E402
CONF = conf("voice.json")
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
SOCK = os.environ.get("BROMIGOS_VOICE_SOCK") or os.path.join(RUNTIME, "bromigos-holo-voice.sock")   # tests use their own
CACHE = os.path.expanduser("~/.cache/bromigos/vector-tts")
VOICE_DIR = os.path.expanduser("~/.local/share/bromigos/voice")
CA = os.path.expanduser("~/.config/homelab/homelab-ca.crt")
FISH_KEY = os.path.expanduser("~/.local/share/bromigos/fish-audio-key")


def log(*a):
    print(time.strftime("%H:%M:%S"), "holo-voice:", *a, flush=True)


def _wav_bytes(a, sr):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(_s16(a))
    return buf.getvalue()


def _s16(a):
    return (np.clip(np.asarray(a, np.float32), -1, 1) * 32767).astype(np.int16).tobytes()


class Server:
    def __init__(self):
        with open(CONF) as f:
            self.cfg = json.load(f)
        self.whisper = None
        self.kokoro = None
        self.kokoro_lock = threading.Lock()
        self.kstyle = None
        self.qwen = None
        self.qwen_loading = threading.Event()
        self.last = time.monotonic()
        self.stt_lock = threading.Lock()
        self.tts_lock = threading.Lock()
        os.makedirs(CACHE, exist_ok=True)

    # ------------------------------------------------------------------ where he runs
    def order(self):
        """(main engine, fallbacks): bromigOS's targets.toml (voice.tts: the homelab's lab,
        then this machine's qwen and kokoro, in the user's order) when the user has one,
        else voice.json's "engine" and "fallback". Asked per line: `bromigos target local`
        reaches the next one."""
        e = targets.engines("voice.tts")
        if e:
            return e[0], e[1:]
        fb = self.cfg.get("fallback", "kokoro")
        return self.cfg["engine"], (fb if isinstance(fb, list) else [fb])

    def stt_lab(self):
        """Whether hearing goes to the lab first (targets.toml voice.stt, else voice.json)."""
        e = targets.engines("voice.stt")
        if e:
            return e[0] == "lab"
        return self.cfg["stt"].get("provider", "local") == "lab"

    # ------------------------------------------------------------------ STT
    def warm(self, local=False):
        if not local and self.stt_lab() and self.lab_url():
            return {"ok": True, "where": "lab"}         # the lab's whisper is resident; the GPU here stays free
        with self.stt_lock:
            if self.whisper is None:
                from faster_whisper import WhisperModel
                c = self.cfg["stt"]
                t0 = time.monotonic()
                try:
                    self.whisper = WhisperModel(c["model"], device=c.get("device", "cuda"), compute_type=c.get("compute_type", "float16"))
                except Exception as e:
                    log("whisper on GPU failed, using CPU:", e)
                    self.whisper = WhisperModel(c["model"], device="cpu", compute_type="int8")
                # the first pass loads the VAD and the CUDA kernels: do it now, not on the first sentence
                list(self.whisper.transcribe(np.zeros(16000, np.float32), beam_size=1, language="en", vad_filter=True)[0])
                log(f"whisper {c['model']} loaded in {time.monotonic() - t0:.2f}s")
        return {"ok": True}

    def stt(self, pcm):
        raw = np.fromfile(pcm, dtype=np.int16)
        if self.stt_lab():
            t0 = time.monotonic()
            got = self._lab_stt(raw)
            if got is not None:
                return {"text": got.get("text", ""), "ms": int((time.monotonic() - t0) * 1000),
                        "seconds": round(len(raw) / 16000, 2), "where": "lab"}
        self.warm(local=True)
        a = raw.astype(np.float32) / 32768.0
        t0 = time.monotonic()
        with self.stt_lock:
            segs, _ = self.whisper.transcribe(a, beam_size=1, language="en", vad_filter=True,
                                              initial_prompt=self.cfg["stt"].get("prompt", ""))
            text = " ".join(s.text for s in segs).strip()
        return {"text": text, "ms": int((time.monotonic() - t0) * 1000), "seconds": round(len(a) / 16000, 2)}

    # ------------------------------------------------------------------ roles
    def role(self, name):
        v = self.cfg.get("voices", {})
        return v.get(name) or v.get("main") or {}

    def ref(self, name):
        """(reference wav, its transcript) for a role; the designed voice if the reference is missing."""
        r = self.role(name)
        p = os.path.expanduser(r.get("ref") or "")
        if p and os.path.exists(p) and os.path.exists(p[:-4] + ".txt"):
            with open(p[:-4] + ".txt") as f:
                return p, f.read().strip()
        if name != "main":
            return self.ref("main")
        d = self.cfg.get("designed_fallback", {})
        return os.path.join(HERE, d.get("ref", "voices/vector-ref-A.wav")), d.get("text", "")

    # ------------------------------------------------------------------ TTS engines (each yields float chunks)
    def _load_qwen(self):
        if self.qwen is not None:
            return self.qwen
        if self.qwen_loading.is_set():
            return None
        self.qwen_loading.set()
        try:
            from faster_qwen3_tts import FasterQwen3TTS
            c = self.cfg["qwen"]
            t0 = time.monotonic()
            m = FasterQwen3TTS.from_pretrained(c["model"])
            m.warmup(prefill_len=100)
            # every role's voice prompt (speaker embedding + reference codes) is computed here
            # once and cached inside the model, so switching voices mid-reply costs nothing
            for name in self.cfg.get("voices", {"main": {}}):
                ref, text = self.ref(name)
                list(m.generate_voice_clone_streaming(text="One moment.", language="English", ref_audio=ref,
                                                      ref_text=text, chunk_size=c.get("chunk_size", 4)))
            self.qwen = m
            log(f"qwen voice {c['model']} ready in {time.monotonic() - t0:.1f}s")
            return m
        finally:
            self.qwen_loading.clear()

    def warm_tts(self, local=False):
        main = self.order()[0]
        if main == "lab" and not local:
            self.warm_lab()
            return {"ok": True, "ready": True}
        if (main == "qwen" or local) and self.qwen is None and not self.qwen_loading.is_set():
            threading.Thread(target=self._safe_load, daemon=True).start()
        return {"ok": True, "ready": main != "qwen" or self.qwen is not None}

    def _safe_load(self):
        # Not under tts_lock: the load takes ~30 s, and holding the lock made every reply in
        # that window wait silently. Kokoro answers meanwhile; Qwen takes over once it's set.
        try:
            self._load_qwen()
        except Exception as e:
            log("qwen voice failed to load:", e)

    def warm_fallback(self):
        """Kokoro (CPU) ready at once, so a cold start speaks within a second."""
        def go():
            try:
                list(self._gen_kokoro("Ready.", "main"))   # not under tts_lock: replies never wait on it
                log("kokoro fallback ready")
            except Exception as e:
                log("kokoro fallback failed to load:", e)
        threading.Thread(target=go, daemon=True, name="kokoro-warm").start()

    def _gen_qwen(self, text, role):
        m = self.qwen
        if m is None:
            raise RuntimeError("qwen voice not loaded yet")
        c = self.cfg["qwen"]
        ref, ref_text = self.ref(role)
        for chunk, sr, _ in m.generate_voice_clone_streaming(
                text=text, language="English", ref_audio=ref, ref_text=ref_text,
                chunk_size=c.get("chunk_size", 4), temperature=c.get("temperature", 0.7)):
            yield np.asarray(chunk, np.float32).squeeze(), sr

    def _gen_kokoro(self, text, role):
        c = self.role(role).get("kokoro") or self.role("main").get("kokoro") or {"blend": {"bm_george": 1.0}}
        with self.kokoro_lock:                 # the warm-up and a first reply never load it twice
            if self.kokoro is None:
                import onnxruntime as ort
                from kokoro_onnx import Kokoro
                so = ort.SessionOptions()
                so.intra_op_num_threads = 8          # measured: faster than all 16 threads under desktop load
                sess = ort.InferenceSession(os.path.join(VOICE_DIR, "kokoro-v1.0.onnx"), sess_options=so,
                                            providers=["CPUExecutionProvider"])
                self.kokoro = Kokoro.from_session(sess, os.path.join(VOICE_DIR, "voices-v1.0.bin"))
                self.kstyle = {}
        key = json.dumps(c["blend"], sort_keys=True)
        if key not in self.kstyle:
            self.kstyle[key] = sum(w * self.kokoro.get_voice_style(v) for v, w in c["blend"].items())
        # Kokoro renders a whole piece before it returns; clause by clause, the first audio
        # comes out of a short clause, not the whole sentence (cold-start replies use it)
        import re as _re
        parts, cur = [], ""
        for piece in _re.split(r"(?<=[,;:—])\s+", text):
            cur = (cur + " " + piece).strip()
            if len(cur.split()) >= 2:
                parts.append(cur)
                cur = ""
        if cur:
            if parts:
                parts[-1] += " " + cur
            else:
                parts.append(cur)
        w = parts[0].split()
        if len(w) > 8:                         # a long opening clause: break at a natural joint after word 4
            for i in range(4, min(len(w) - 2, 9)):
                if w[i].lower() in ("and", "but", "so", "which", "that", "with", "on", "in", "of", "for", "to", "at",
                                    "from", "because", "while", "is", "are", "was"):
                    parts[0:1] = [" ".join(w[:i]), " ".join(w[i:])]
                    break
        for piece in parts:
            a, sr = self.kokoro.create(piece, voice=self.kstyle[key], speed=c.get("speed", 1.05), lang=c.get("lang", "en-gb"))
            yield np.asarray(a, np.float32), sr

    # ------------------------------------------------------------------ the lab's voice
    def lab_url(self):
        """The lab voice service's base URL: voice.json lab.url, else bromigos-homelab's `voice`
        capability (homelab.toml), else the private overlay's tts endpoint. None: no lab."""
        c = self.cfg.get("lab") or {}
        url = c.get("url") or targets.endpoint("voice.tts", "lab") or ""
        if not url:
            try:
                import bromigos_homelab as H
                for v in H.voice().get("items", []):
                    if v.get("endpoint"):
                        url = v["endpoint"]
                        break
            except Exception:
                pass
        if not url:
            url = PRIV.url("tts") or ""
        for tail in ("/v1/audio/speech", "/v1/tts/stream", "/health"):
            if url.endswith(tail):
                url = url[: -len(tail)]
        return url.rstrip("/") or None

    def _lab_new(self):
        url = self.lab_url()
        if not url:
            raise RuntimeError("no lab voice configured (voice.json lab.url, homelab.toml voice source)")
        u = urllib.parse.urlparse(url)
        t = (self.cfg.get("lab") or {}).get("timeout_s", 3)
        if u.scheme == "https":
            ctx = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()
            conn = http.client.HTTPSConnection(u.hostname, u.port, context=ctx, timeout=t)
        else:
            conn = http.client.HTTPConnection(u.hostname, u.port, timeout=t)
        conn.lab_url, conn.lab_prefix = url, u.path.rstrip("/")
        return conn

    def _lab_open(self, path, data=None, method=None, headers=None, timeout=None):
        """Send one request on a kept-alive connection from the pool (a new TLS handshake through
        the gateway per line cost ~100 ms of first audio); a fresh one if the lab closed the old.
        Returns (response, connection): give the connection back with _lab_put once the response
        is read to the end, or close it. An HTTP error status raises urllib.error.HTTPError."""
        c = self.cfg.get("lab") or {}
        pool = self.__dict__.setdefault("_lab_pool", [])
        url = self.lab_url()
        for attempt in (0, 1):
            conn = None
            with self.__dict__.setdefault("_lab_pool_lock", threading.Lock()):
                while pool and conn is None:
                    cand = pool.pop()
                    if cand.lab_url == url:
                        conn = cand
                    else:
                        cand.close()
            reused = conn is not None
            conn = conn or self._lab_new()
            try:
                conn.timeout = timeout or c.get("timeout_s", 3)
                if conn.sock is not None:
                    conn.sock.settimeout(conn.timeout)
                conn.request(method or ("POST" if data is not None else "GET"), conn.lab_prefix + path, body=data,
                             headers=headers or {})
                r = conn.getresponse()
                break
            except (http.client.RemoteDisconnected, http.client.CannotSendRequest, http.client.ResponseNotReady,
                    BrokenPipeError, ConnectionResetError):
                conn.close()
                if attempt or not reused:
                    raise
            except Exception:
                conn.close()
                raise
        if r.status >= 400:
            body = r.read()
            self._lab_put(conn)
            raise urllib.error.HTTPError(conn.lab_url + path, r.status, r.reason, r.headers, io.BytesIO(body))
        return r, conn

    def _lab_put(self, conn):
        with self.__dict__.setdefault("_lab_pool_lock", threading.Lock()):
            pool = self.__dict__.setdefault("_lab_pool", [])
            if len(pool) < 4:
                pool.append(conn)
                return
        conn.close()

    def _lab_call(self, path, data=None, method=None, headers=None, timeout=None):
        """A whole request: the body (read to the end, the connection back in the pool)."""
        r, conn = self._lab_open(path, data, method, headers, timeout)
        try:
            body = r.read()
        except Exception:
            conn.close()
            raise
        self._lab_put(conn)
        return body

    def _lab_voice(self, role):
        """The lab's id for a role's reference voice, uploading it (once, by content) if new."""
        ref, text = self.ref(role)
        with open(ref, "rb") as f:
            audio = f.read()
        vid = hashlib.sha256(audio).hexdigest()[:16]
        known = getattr(self, "_lab_voices", set())
        if vid in known:
            return vid
        try:
            self._lab_call(f"/v1/voices/{vid}")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            b = uuid.uuid4().hex
            body = (f"--{b}\r\nContent-Disposition: form-data; name=\"audio\"; filename=\"ref.wav\"\r\n\r\n".encode()
                    + audio + f"\r\n--{b}\r\nContent-Disposition: form-data; name=\"text\"\r\n\r\n{text}\r\n--{b}--\r\n".encode())
            self._lab_call(f"/v1/voices/{vid}", body, "PUT", {"Content-Type": f"multipart/form-data; boundary={b}"},
                           timeout=15)
            log(f"lab voice {role} uploaded as {vid}")
        known.add(vid)
        self._lab_voices = known
        return vid

    def warm_lab(self):
        """Register every role's voice with the lab now, so the first reply streams at once."""
        def go():
            for name in self.cfg.get("voices", {"main": {}}):
                try:
                    self._lab_voice(name)
                except Exception as e:
                    log(f"lab voice {name} not registered: {e}")
                    return
        threading.Thread(target=go, daemon=True, name="lab-warm").start()

    def _gen_lab(self, text, role):
        c = self.cfg.get("lab") or {}
        vid = self._lab_voice(role)
        body = json.dumps({"text": text, "voice": vid, "language": c.get("language", "English"),
                           "temperature": c.get("temperature", 0.7), "chunk_size": c.get("chunk_size", 4)}).encode()
        # a lab that's down, loading (503) or slow to start fails here, fast, and the next engine speaks
        r, conn = self._lab_open("/v1/tts/stream", body, "POST", {"Content-Type": "application/json"},
                                 timeout=c.get("first_audio_timeout_s", 2.5))
        sr = int(r.headers.get("X-Sample-Rate") or 24000)
        rest, first, done = b"", True, False
        try:
            while True:
                try:
                    pcm = r.read1(9600)
                except Exception:
                    raise RuntimeError("lab stream stalled") from None
                if first:
                    first = False
                    if conn.sock is not None:
                        conn.sock.settimeout(c.get("stream_timeout_s", 10))      # after the first audio: no hair trigger
                if not pcm:
                    done = True
                    break
                pcm = rest + pcm
                n = len(pcm) // 2 * 2
                rest = pcm[n:]
                if n:
                    yield np.frombuffer(pcm[:n], np.int16).astype(np.float32) / 32768.0, sr
        finally:
            if done:
                self._lab_put(conn)
            else:
                conn.close()              # barge-in or a stall: that stream can't carry the next line

    def _lab_stt(self, a16):
        """Speech-to-text in the lab; None when there's no lab or it didn't answer."""
        if not self.lab_url():
            return None
        c = self.cfg.get("stt", {})
        q = urllib.parse.urlencode({"language": "en", "prompt": c.get("prompt", "")})
        try:
            return json.loads(self._lab_call("/v1/stt?" + q, a16.tobytes(), "POST", {"Content-Type": "audio/L16"},
                                             timeout=c.get("lab_timeout_s", 8)))
        except Exception as e:
            log("lab stt failed, using local whisper:", e)
            return None

    def _gen_breeze(self, text, role):
        c = self.cfg["breeze"]
        b = uuid.uuid4().hex
        parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"{n}\"\r\n\r\n{v}\r\n"
                 for n, v in (("text", text), ("instruction", c["instruction"]), ("seed", str(c.get("seed", 7))),
                              ("cfg_scale", str(c.get("cfg_scale", 4))))]
        body = ("".join(parts) + f"--{b}--\r\n").encode()
        ctx = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()
        url = c.get("url") or PRIV.url("tts")
        if not url:
            raise RuntimeError("breeze: no TTS endpoint configured (private overlay endpoints.tts)")
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
            while True:
                pcm = r.read1(9600)
                if not pcm:
                    break
                yield np.frombuffer(pcm[: len(pcm) // 2 * 2], np.int16).astype(np.float32) / 32768.0, 24000

    def _gen_fish(self, text, role):
        c = self.cfg["fish"]
        if not c.get("reference_id"):
            raise RuntimeError("fish engine needs a reference_id of a voice model the operator owns")
        with open(FISH_KEY) as f:
            key = f.read().strip()
        body = {"reference_id": c["reference_id"], "text": text, "format": "wav", "normalize": True}
        req = urllib.request.Request("https://api.fish.audio/v1/tts", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        with wave.open(io.BytesIO(data)) as w:
            sr = w.getframerate()
            yield np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0, sr

    # ------------------------------------------------------------------ TTS front
    def _key(self, engine, text, role="main", shimmer=0.0):
        r = self.role(role)
        conf = [engine, self.cfg.get(engine), role, r.get("ref"), r.get("kokoro") if engine == "kokoro" else None,
                round(shimmer, 3), round(self.speed_for(role), 3), self.cfg.get("pacing")]
        return hashlib.sha1(json.dumps([conf, text], sort_keys=True).encode()).hexdigest()[:24]

    def _engines(self):
        main, fb = self.order()
        if main == "lab":
            out = ["lab"] if self.lab_url() else []
            for e in (fb if isinstance(fb, list) else [fb]):
                if e == "qwen" and self.qwen is None:
                    if not out:                           # no lab at all: get the local voice ready now
                        self.warm_tts(local=True)
                    continue                              # (a lab that fails warms it in speak())
                if e and e not in out:
                    out.append(e)
            return out
        if isinstance(fb, list):
            fb = next((e for e in fb if e != main), None)
        if main == "qwen" and self.qwen is None:
            self.warm_tts()
            # give a load in progress a moment (the window usually warmed it already)
            t0 = time.monotonic()
            while self.qwen is None and self.qwen_loading.is_set() and time.monotonic() - t0 < self.cfg["qwen"].get("wait_for_load_s", 0):
                time.sleep(0.05)
        out = [main] if (main != "qwen" or self.qwen is not None) else []
        return out + ([fb] if fb and fb != main else [])

    def speed_for(self, role):
        """voice.json "speed" (default 1.12), a role may override; WSOLA keeps the pitch."""
        return max(0.7, min(1.6, float(self.role(role).get("speed", self.cfg.get("speed", 1.12)))))

    def shimmer_for(self, role, add=0.0):
        return max(0.0, min(1.0, float(self.role(role).get("shimmer", 0.3)) + float(add or 0)))

    def cue(self, role, sr, add=0.0):
        """The dial scratch into `role`, through that role's shimmer; cached."""
        key = (role, sr, round(add, 3))
        if not hasattr(self, "_cues"):
            self._cues = {}
        if key not in self._cues:
            variant = sorted(self.cfg.get("voices", {"main": 0})).index(role) if role in self.cfg.get("voices", {}) else 0
            a = dial_scratch(sr, variant, self.cfg.get("cue", {}).get("seconds", 0.24))
            self._cues[key] = Shimmer(sr, self.shimmer_for(role, add)).process(a)
        return self._cues[key]

    def speak(self, text, role="main", add=0.0, cue=False):
        """Yields (header, chunks...): header {"sr", "engine", "cached"} then float32 chunks."""
        text = text.strip()
        shim = self.shimmer_for(role, add)
        engines = self._engines()
        main = self.order()[0]
        if main not in engines and os.path.exists(os.path.join(CACHE, self._key(main, text, role, shim) + ".wav")):
            engines = [main] + engines           # a cached cast-voice line needs no model: play it during a load
        for engine in engines:
            path = os.path.join(CACHE, self._key(engine, text, role, shim) + ".wav")
            if os.path.exists(path):
                with wave.open(path) as w:
                    sr = w.getframerate()
                    a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
                yield {"sr": sr, "engine": engine, "cached": True}
                if cue:
                    yield self.cue(role, sr, add)
                for i in range(0, len(a), sr // 5):
                    yield a[i:i + sr // 5]
                return
            try:
                gen = getattr(self, "_gen_" + engine)(text, role)
                first = next(gen)
            except Exception as e:
                log(f"tts engine {engine} failed: {e}")
                if engine == "lab" and self.qwen is None:
                    self.warm_tts(local=True)         # only now: the next line can have the local cast voice
                continue
            chunk, sr = first
            dsp = Shimmer(sr, shim)
            ts = TimeStretch(sr, self.speed_for(role))
            pc = self.cfg.get("pacing", {})
            sh = SilenceShaper(sr, pc.get("max_pause_s", 0.12), pc.get("sentence_tail_s", 0.09))
            yield {"sr": sr, "engine": engine, "cached": False}
            if cue:
                yield self.cue(role, sr, add)
            done = []
            y = dsp.process(ts.process(sh.process(chunk)))
            done.append(y)
            yield y
            for chunk, _ in gen:
                y = dsp.process(ts.process(sh.process(chunk)))
                if len(y):
                    done.append(y)
                    yield y
            y = dsp.process(ts.process(sh.process(np.zeros(0, np.float32), final=True), final=True))
            if len(y):
                done.append(y)
                yield y
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                f.write(_wav_bytes(np.concatenate(done), sr))
            os.replace(tmp, path)
            return
        raise RuntimeError("no TTS engine answered")

    def tts(self, text, role="main"):
        t0 = time.monotonic()
        with self.tts_lock:
            it = self.speak(text, role)
            head = next(it)
            a = np.concatenate([c for c in it] or [np.zeros(1, np.float32)])
        path = os.path.join(CACHE, self._key(head["engine"], text.strip(), role, self.shimmer_for(role)) + ".wav")
        return {"wav": path, "ms": int((time.monotonic() - t0) * 1000), "cached": head["cached"], "engine": head["engine"]}

    # ------------------------------------------------------------------ loop
    def handle(self, req, conn):
        op = req.get("op")
        self.last = time.monotonic()
        if op == "ping":
            return {"ok": True, "whisper": self.whisper is not None, "qwen": self.qwen is not None,
                    "engine": self.order()[0], "lab": self.lab_url() is not None,
                    "targets": targets.resolve("voice.tts") is not None}
        if op == "warm":
            return self.warm()
        if op == "warm_tts":
            return self.warm_tts()
        if op == "stt":
            return self.stt(req["pcm"])
        if op == "tts":
            return self.tts(req["text"], req.get("role", "main"))
        if op == "cue":
            return {"seconds": self.cfg.get("cue", {}).get("seconds", 0.24)}
        if op == "tts_stream":
            with self.tts_lock:
                it = self.speak(req["text"], req.get("role", "main"), req.get("shimmer_add", 0.0), bool(req.get("cue")))
                head = next(it)
                conn.sendall((json.dumps(head) + "\n").encode())
                for chunk in it:
                    try:
                        conn.sendall(_s16(chunk))
                    except (BrokenPipeError, ConnectionResetError):
                        # barge-in: the client hung up; finish quietly so the cache is still written
                        for _ in it:
                            pass
                        break
            return None
        return {"error": "unknown op"}

    def janitor(self):
        while True:
            time.sleep(20)
            idle = time.monotonic() - self.last
            if idle > self.cfg.get("unload_after_seconds", 1800):
                if self.whisper is not None:
                    with self.stt_lock:
                        self.whisper = None
                    log("whisper unloaded (idle)")
                if self.qwen is not None:
                    with self.tts_lock:
                        self.qwen = None
                        import gc
                        gc.collect()
                        try:
                            import torch
                            torch.cuda.empty_cache()
                        except Exception:
                            pass
                    log("qwen voice unloaded (idle)")
            if idle > self.cfg.get("exit_after_seconds", 3600):
                log("exiting (idle)")
                os._exit(0)

    def serve(self):
        # One server per session: two clients spawning at once each loaded every model and
        # the second ran the 12 GB card out of memory (CUBLAS_STATUS_ALLOC_FAILED).
        import fcntl
        self._lock = open(SOCK + ".lock", "w")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log("another voice server holds the lock; exiting")
            os._exit(0)
        try:
            os.unlink(SOCK)
        except FileNotFoundError:
            pass
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(SOCK)
        os.chmod(SOCK, 0o600)
        srv.listen(8)
        threading.Thread(target=self.janitor, daemon=True).start()
        main, fb = self.order()
        log("ready", SOCK, "engine", main, "then", ", ".join(fb) or "nothing",
            "(targets.toml)" if targets.resolve("voice.tts") is not None else "(voice.json)")
        if "kokoro" in fb or main == "kokoro":
            self.warm_fallback()
        if self.cfg.get("preload_tts", True):
            self.warm_tts()
        while True:
            c, _ = srv.accept()
            threading.Thread(target=self._client, args=(c,), daemon=True).start()

    def _client(self, c):
        try:
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = c.recv(65536)
                if not chunk:
                    break
                buf += chunk
            if not buf.strip():
                return                       # a liveness probe
            try:
                out = self.handle(json.loads(buf.decode()), c)
            except Exception as e:
                out = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            if out is not None:
                c.sendall((json.dumps(out) + "\n").encode())
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            c.close()


if __name__ == "__main__":
    Server().serve()
