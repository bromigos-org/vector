#!/usr/bin/env python3
"""Render a tagged VECTOR reply to a wav exactly as the desktop would speak it: the same
splitter (voices on whole sentences), the same server (roles, shimmer, mood shimmer) and the
dial scratch on every voice change. Nothing is played.

    tools/voice-demo.py OUT.wav "reply with ‹sci›markers‹/sci› and ‹mood:alarmed›" [...more replies]
"""
import json
import os
import socket
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from holo.vector.text import VoiceSplitter, spoken  # noqa: E402
from holo.vector.voice import SOCK, Voice  # noqa: E402

from holo.paths import conf  # noqa: E402
CFG = json.load(open(conf("voice.json")))


def stream(text, role, cue, add):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(120)
    s.connect(SOCK)
    s.sendall((json.dumps({"op": "tts_stream", "text": text, "role": role, "cue": cue, "shimmer_add": add}) + "\n").encode())
    f = s.makefile("rb")
    head = json.loads(f.readline())
    data = f.read()
    s.close()
    return head["sr"], np.frombuffer(data[: len(data) // 2 * 2], np.int16)


def main():
    out, replies = sys.argv[1], sys.argv[2:]
    Voice(None)._req({"op": "ping"})                 # spawn the server if it is down
    pcm, sr, log = [], 24000, []
    for reply in replies:
        vs, last, mood = VoiceSplitter(), "main", "calm"
        for role, text, m in vs.feed(reply, final=True):
            mood = m or mood
            add = float(CFG.get("mood_shimmer", {}).get(mood, 0.0))
            sr, a = stream(spoken(text), role, role != last, add)
            pcm.append(a)
            log.append(f"{role:9s} {mood:9s} {text}")
            last = role
        pcm.append(np.zeros(int(sr * 1.2), np.int16))
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.concatenate(pcm).tobytes())
    print("\n".join(log))
    print(out, f"{sum(len(x) for x in pcm) / sr:.1f} s")


if __name__ == "__main__":
    main()
