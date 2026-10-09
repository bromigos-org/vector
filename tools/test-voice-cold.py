#!/usr/bin/env python3
"""VECTOR is never silent after a cold start. Run with the voice venv:
    ~/.local/share/bromigos/venv-tts/bin/python tools/test-voice-cold.py

The real voice server code runs in this process on its own socket (the host's live server is
untouched), with the cast voice's ~30 s load replaced by a 30 s sleep: the cold-start window
in which replies used to wait silently behind the load. Nothing touches the GPU.

  1. show: the server starts (as SUPER+E / M1 / M3 start it, in parallel with the window);
  2. 2 s later, the first reply sentence arrives (the brain's first token alone is 1-2 s);
  3. its first audio must play within 1.5 s, from Kokoro, while the cast voice still loads;
  4. a second fresh sentence right after is just as quick (no lock held by the load);
  5. a line already cached in the cast voice plays in the cast voice even mid-load.
Also prints the worst case: a sentence sent the instant the server starts.
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import uuid

HOLO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HOLO)
TMP = tempfile.mkdtemp(prefix="vector-voice-test-")
os.environ["BROMIGOS_VOICE_SOCK"] = os.path.join(TMP, "voice.sock")
from holo import voice_server as VS  # noqa: E402

bad = 0
LIMIT = 1.5


def ok(cond, what, detail=""):
    global bad
    print(f"{'pass' if cond else 'FAIL'}  {what}" + (f" — {detail}" if detail else ""), flush=True)
    bad += 0 if cond else 1


def first_audio(text, role="main", timeout=40):
    """-> (seconds to the first PCM bytes, engine, cached) through the real tts_stream op."""
    t0 = time.monotonic()
    for _ in range(200):
        try:
            s = socket.socket(socket.AF_UNIX)
            s.settimeout(timeout)
            s.connect(VS.SOCK)
            break
        except OSError:
            time.sleep(0.02)
    s.sendall((json.dumps({"op": "tts_stream", "text": text, "role": role, "cue": False}) + "\n").encode())
    f = s.makefile("rb")
    head = json.loads(f.readline().decode() or "{}")
    b = f.read1(4800) if hasattr(f, "read1") else f.read(4800)
    dt = time.monotonic() - t0
    while f.read1(65536):
        pass
    s.close()
    return dt, head.get("engine"), head.get("cached"), len(b)


def start_server(load_s=30.0):
    srv = VS.Server()

    def slow_load():                       # the cast voice "loading" for load_s seconds
        if srv.qwen is not None or srv.qwen_loading.is_set():
            return None
        srv.qwen_loading.set()
        time.sleep(load_s)
        srv.qwen_loading.clear()
        return None
    srv._load_qwen = slow_load
    threading.Thread(target=srv.serve, daemon=True, name="test-voice-server").start()
    return srv


def main():
    print("== worst case: a sentence the instant the server starts")
    srv = start_server()
    dt, eng, cached, n = first_audio(f"Hello host, the line is open. {uuid.uuid4().hex[:4]}.")
    print(f"      first audio {dt:.2f} s from start ({eng})")
    print("== the cold-start window (cast voice loading)")
    time.sleep(2.0)
    ok(srv.qwen_loading.is_set() and srv.qwen is None, "the cast voice is still loading")
    dt, eng, cached, n = first_audio(f"Forty-five Argo applications, forty-four synced and healthy. {uuid.uuid4().hex[:4]}.")
    ok(dt <= LIMIT and n > 0, f"first reply audible within {LIMIT} s", f"{dt:.2f} s, engine {eng}")
    ok(eng == "kokoro", "spoken by the quick fallback while the cast loads", eng)
    dt2, eng2, _, _ = first_audio(f"The searxng deployment has been restarted. {uuid.uuid4().hex[:4]}.")
    ok(dt2 <= LIMIT, "the next sentence too (no lock held by the load)", f"{dt2:.2f} s")
    # a line cached in the cast voice plays in the cast voice, even mid-load
    key = srv._key(srv.cfg["engine"], "Cached cast line.", "main", srv.shimmer_for("main"))
    path = os.path.join(VS.CACHE, key + ".wav")
    made = not os.path.exists(path)
    if made:
        import numpy as np
        with open(path, "wb") as f:
            f.write(VS._wav_bytes(np.zeros(4800, np.float32), 24000))
    try:
        dt3, eng3, cached3, _ = first_audio("Cached cast line.")
        ok(eng3 == srv.cfg["engine"] and cached3, "a cached cast-voice line keeps the cast voice mid-load", f"{eng3}, {dt3:.2f} s")
    finally:
        if made:
            os.unlink(path)
    print("FAILURES:", bad, flush=True)
    sys.stdout.flush()
    os._exit(1 if bad else 0)               # the in-process server thread never ends on its own


if __name__ == "__main__":
    main()
