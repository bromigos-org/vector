#!/usr/bin/env python3
"""The local voice for real, on this machine's NVIDIA card: the worker loads the real models,
speaks five lines in a reference voice, hears them back, steps aside for a pretend game and
comes back. It writes WAV files; it never plays a sound or opens a window.

It refuses to run while the card is busy: a game or other GPU work (holo/gamewatch.py's signs)
or utilisation above 10 %. Then it runs at the lowest priority. It needs the voice venv with the
local voice's runtime and models (`bromigos-holo setup local-voice`).

    ~/.local/share/bromigos/venv-tts/bin/python tools/test-local-voice-gpu.py \
        [--ref voice.wav (its transcript beside it, .txt)] [--out DIR]

Prints what it measured (time to first audio, real-time factor, the card's memory, start and
leave times) and exits non-zero if a line isn't heard back or the card isn't freed.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from holo import gamewatch, local_models, local_voice, speech_text  # noqa: E402

LINES = {
    "short": "Done. The lights are off in the rack room.",
    "long": "The backup finished an hour ago, and everything checked out. Three of the drives are running a "
            "little warm, so I have moved tonight's heavy jobs to the cooler machines, and I will keep an eye on "
            "them until morning. If anything changes, you will be the first to know.",
    "numbers": "Your graphics card is at 71 degrees, using 9.4 of 12 gigabytes. The download finished at 3:45 PM: "
               "2,318 files, 14.6 gigabytes, in 6 minutes and 12 seconds.",
    "question": "Would you like me to pause the updates until you have finished your game, or shall I run them now?",
    "name": "Good evening, WAYFARER. Siobhan sent word from the Wick, and Argo CD has finished syncing Prometheus.",
}


def smi_util():
    r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True)
    return int(r.stdout.split()[0]) if r.returncode == 0 else None


def gpu_mib(pid):
    out = subprocess.run(["nvidia-smi"], capture_output=True, text=True).stdout
    for p, mib in gamewatch.GameWatch().gpu_procs() if out else []:
        if p == pid:
            return mib
    return 0


def words(t):
    return re.sub(r"[^a-z0-9 ]", " ", speech_text.speakable(t).lower().replace("-", " ")).split()


def errors(ref, hyp):
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)], len(r)


class Pretend:
    def __init__(self):
        self.why, self.own, self.real = None, set(), gamewatch.GameWatch()

    def check(self):
        return self.why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default=os.path.join(HERE, "voices", "vector-ref-A.wav"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    txt = a.ref[:-4] + ".txt"
    ref_text = open(txt).read().strip() if os.path.exists(txt) else json.load(
        open(os.path.join(HERE, "voice.json")))["designed_fallback"]["text"]
    why = gamewatch.GameWatch().check()
    util = smi_util()
    if util is None:
        sys.exit("no NVIDIA card here: nothing to test")
    if why or util > 10:
        sys.exit(f"the card is busy ({why or f'utilisation {util} %'}): not now")
    if not local_models.present():
        sys.exit("the models aren't fetched: bromigos-holo setup local-voice")
    os.nice(19)
    tmp = tempfile.mkdtemp(prefix="vector-local-voice-gpu-")
    out = a.out or tmp
    os.makedirs(out, exist_ok=True)
    os.environ["BROMIGOS_LOCAL_VOICE_SOCK"] = os.path.join(tmp, "lv.sock")
    watch = Pretend()
    m = local_voice.Manager({"local": {"idle_exit_s": 120}}, log=lambda *x: None, watch=watch,
                            sock=os.environ["BROMIGOS_LOCAL_VOICE_SOCK"])
    m._on, m._on_at = (True, "on (test)"), 1e18
    res = {"lines": {}}
    t0 = time.monotonic()
    m.warm_ref = (a.ref, ref_text)
    m.ensure()
    if not wait(m.ready, 120):
        sys.exit(f"the worker didn't get ready: {m.state} {m.why}")
    res["cold_start_s"] = round(time.monotonic() - t0, 2)
    pid = m.proc.pid
    time.sleep(3)                                          # the warm line and hearing load
    peak, bad = 0, 0
    for k, line in LINES.items():
        t0 = time.monotonic()
        first, chunks, sr = None, [], 24000
        for c, sr in m.tts(speech_text.speakable(line), a.ref, ref_text):
            first = first or time.monotonic() - t0
            chunks.append(c)
            peak = max(peak, gpu_mib(pid)) if len(chunks) % 8 == 1 else peak
        total = time.monotonic() - t0
        audio = np.concatenate(chunks)
        wav = os.path.join(out, f"local-{k}.wav")
        with wave.open(wav, "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        a16 = np.interp(np.linspace(0, len(audio), int(len(audio) * 16000 / sr), endpoint=False),
                        np.arange(len(audio)), audio)
        pcm = os.path.join(tmp, f"{k}.pcm")
        (np.clip(a16, -1, 1) * 32767).astype(np.int16).tofile(pcm)
        t1 = time.monotonic()
        heard = m.stt(pcm) or ""
        e, n = errors(line, heard)
        bad += e > max(3, n // 5)
        res["lines"][k] = {"first_audio_s": round(first, 3), "rtf": round(total / (len(audio) / sr), 3),
                           "audio_s": round(len(audio) / sr, 2), "heard_ms": int((time.monotonic() - t1) * 1000),
                           "errors": e, "words": n, "heard": heard}
        print(k, res["lines"][k], flush=True)
    res["peak_gpu_mib"] = peak
    watch.why = "a pretend game"
    t0 = time.monotonic()
    m.tick()
    freed = wait(lambda: gpu_mib(pid) == 0, 10)
    res["leave_s"] = round(time.monotonic() - t0, 2)
    res["card_freed"] = freed
    watch.why = None
    m.c["game_clear_s"] = 0
    m.tick()
    t0 = time.monotonic()
    m.ensure()
    back = wait(m.ready, 60)
    res["start_again_s"] = round(time.monotonic() - t0, 2)
    m.stop("test done")
    res["wavs"] = out
    print(json.dumps(res, indent=1))
    ok = freed and back and not bad
    print("all good" if ok else "FAILED")
    return 0 if ok else 1


def wait(pred, s):
    t0 = time.monotonic()
    while time.monotonic() - t0 < s:
        if pred():
            return True
        time.sleep(0.05)
    return False


if __name__ == "__main__":
    sys.exit(main())
