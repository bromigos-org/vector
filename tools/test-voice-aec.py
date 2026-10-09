#!/usr/bin/env python3
"""Conversation mode hears the host, not itself; and his reply is audible end to end.

Quiet: a null sink stands in for the speakers and its monitor for the mic, so the speaker ->
mic leakage is total (worse than a real room) and nothing reaches the real speakers. The test
builds its own echo-cancel pair on them (vector_test_aec_*), runs the real Voice and
Conversation code on it, and the live voice server renders the speech.

  1. routing: his reply plays into the AEC sink (the canceller's reference), never straight
     to the "speakers", and the default sink is untouched;
  2. audible: the reply reaches the "speakers" (the null sink's monitor) at speech level;
  3. no self barge-in: while his own voice leaks back into the mic, conversation mode
     doesn't fire a barge-in;
  4. a real barge-in still works: another voice played straight into the "room" (not via the
     reference) while he talks stops him;
  5. a reply knocked off the AEC sink mid-play (today's bug) is moved back without a barge-in.

Run: ~/.local/share/bromigos/venv-brain/bin/python tools/test-voice-aec.py
"""
import os
import subprocess
import sys
import threading
import time

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
SPK = "vector_test_spk"
os.environ["BROMIGOS_VOICE_AEC"] = "vector_test_aec"
os.environ["BROMIGOS_VOICE_AEC_MASTERS"] = f"{SPK},{SPK}.monitor"

LINE = ("All six nodes are up and every application is synced and healthy. The rack room is quiet "
        "tonight, and the last deploy went out clean twenty minutes ago.")
OTHER = "Hold on, stop there, I need to ask you something about the gateway."


class _Avatar:
    level = 0.0
    state = "idle"


class _Scene:
    avatar = _Avatar()
    audio_level = None
    conversation = mic_live = False

    def __getattr__(self, name):              # set_state, set_voice, note, end_reply, ...
        return lambda *a, **k: None

    def mood_name(self):
        return "calm"


class _App:
    brain = None
    memory = None
    pscene = _Scene()

    def __getattr__(self, name):
        return lambda *a, **k: None


def pactl(*a):
    return subprocess.run(["pactl", *a], capture_output=True, text=True, timeout=10).stdout.strip()


def record(sink, out, stop):
    """What the "speakers" (a sink) play, from its monitor."""
    p = subprocess.Popen(["pw-record", "--raw", "--rate", "16000", "--channels", "1", "--format", "s16",
                          "--target", sink, "-P", "{ stream.capture.sink = true }", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    while not stop.is_set():
        b = p.stdout.read(3200)
        if not b:
            break
        out.extend(b)
    p.terminate()


def rms_db(buf):
    x = np.frombuffer(bytes(buf), np.int16).astype(np.float32) / 32768.0
    if not len(x):
        return -120.0
    frames = x[: len(x) // 1600 * 1600].reshape(-1, 1600)          # 0.1 s
    loud = np.sort(np.sqrt((frames ** 2).mean(axis=1)))[-max(1, len(frames) // 4):]   # the speech, not the gaps
    return float(20 * np.log10(max(loud.mean(), 1e-6)))


def wait_quiet(v, timeout=60):
    t = time.monotonic()
    while time.monotonic() - t < timeout and (v.speaking or (v.player and v.player.poll() is None)):
        time.sleep(0.05)


def main():
    default_before = pactl("get-default-sink")
    mod = pactl("load-module", "module-null-sink", f"sink_name={SPK}",
                f"sink_properties=device.description={SPK}")
    bad = 0
    v = conv = None
    try:
        for _ in range(50):                     # the AEC pair must find the null sink, or it links elsewhere
            if SPK in pactl("list", "short", "sinks"):
                break
            time.sleep(0.1)
        time.sleep(1.0)
        from holo.vector.voice import Voice
        from holo.vector.conversation import Conversation
        v = Voice(_App())
        v.muted = False
        v.cfg["sink"] = None
        assert v.ensure_aec(), "the test echo-cancel pair didn't load"
        sink = v.playback_sink()
        ok = sink == "vector_test_aec_sink"
        bad += not ok
        print(f"{'pass' if ok else 'FAIL'}  playback goes to the AEC sink — {sink}")
        conv = Conversation(v)
        v.conv = conv
        conv.start()
        time.sleep(1.0)

        # 1-3: his own line, with total leakage back into the mic
        heard, stop = bytearray(), threading.Event()
        rec = threading.Thread(target=record, args=(SPK, heard, stop), daemon=True)
        rec.start()
        v.say(LINE)
        t0 = time.monotonic()
        while not (v.player and v.player.poll() is None) and time.monotonic() - t0 < 30:
            time.sleep(0.05)
        time.sleep(0.5)
        where = v._stream_sink(v.player_node) if v.player else None
        ok = bool(where) and where[1] == "vector_test_aec_sink"
        bad += not ok
        print(f"{'pass' if ok else 'FAIL'}  the playing stream is linked to the AEC sink — {where}")
        wait_quiet(v)
        stop.set()
        rec.join(2)
        db = rms_db(heard)
        ok = db > -40
        bad += not ok
        print(f"{'pass' if ok else 'FAIL'}  audible end to end: the reply reached the speakers at {db:.1f} dBFS "
              f"({v.last_stats.get('played_s')} s played)")
        ok = conv.barge_ins == 0
        bad += not ok
        print(f"{'pass' if ok else 'FAIL'}  no barge-in from his own voice — {conv.barge_ins} fired")

        # 4: a real barge-in: another voice straight into the room while he talks
        other = v._req({"op": "tts", "text": OTHER, "role": "robot"}, 120)["wav"]
        n0 = conv.barge_ins
        v.say(LINE)
        t0 = time.monotonic()
        while not (v.player and v.player.poll() is None) and time.monotonic() - t0 < 30:
            time.sleep(0.05)
        time.sleep(1.2)
        subprocess.run(["pw-play", "--target", SPK, other], timeout=30)
        wait_quiet(v, 20)
        ok = conv.barge_ins > n0
        bad += not ok
        print(f"{'pass' if ok else 'FAIL'}  a real barge-in still stops him — {conv.barge_ins - n0} fired")
        # 5: the stream knocked off the AEC sink mid-reply (the bug: his voice went straight to the
        # speakers, uncancelled) is moved back at once, and no barge-in fires
        time.sleep(1.0)
        n0, r0 = conv.barge_ins, v.reroutes
        v.say(LINE)
        t0 = time.monotonic()
        while not (v.player and v.player.poll() is None) and time.monotonic() - t0 < 30:
            time.sleep(0.05)
        time.sleep(1.5)
        got = v._stream_sink(v.player_node)
        if got:
            pactl("move-sink-input", got[0], SPK)
        time.sleep(1.0)
        back = v._stream_sink(v.player_node)
        wait_quiet(v)
        ok = v.reroutes > r0 and bool(back) and back[1] == "vector_test_aec_sink" and conv.barge_ins == n0
        bad += not ok
        print(f"{'pass' if ok else 'FAIL'}  knocked off the AEC sink, it's moved back — reroutes {v.reroutes - r0}, "
              f"now on {back and back[1]}, barge-ins {conv.barge_ins - n0}")
        ok = pactl("get-default-sink") == default_before
        bad += not ok
        print(f"{'pass' if ok else 'FAIL'}  the default sink is untouched — {pactl('get-default-sink')}")
    finally:
        if conv:
            conv.stop()
        if v:
            v.stop()
            v.drop_aec()
        for line in pactl("list", "short", "modules").splitlines():     # whatever is left of the test pair
            if "vector_test_aec" in line:
                pactl("unload-module", line.split()[0])
        if mod.isdigit():
            pactl("unload-module", mod)
    print("FAILURES:", bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
