#!/usr/bin/env python3
"""The local voice's number normaliser (holo/speech_text.py): quantities, times, units and
percentages are spelled out; versions, IP addresses, ids, paths and model names stay exactly as
written, as VECTOR's persona says.

Offline and quick: the standard library only, nothing spoken, no window.

Run: python3 tools/test-speech-text.py
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "holo"))
import speech_text  # noqa: E402

S = speech_text.speakable

# the five lines the local voice was measured with (the owner listened to these)
FIVE = [
    ("Done. The lights are off in the rack room.",
     "Done. The lights are off in the rack room."),
    ("The backup finished an hour ago, and everything checked out. Three of the drives are running a little "
     "warm, so I have moved tonight's heavy jobs to the cooler machines, and I will keep an eye on them until "
     "morning. If anything changes, you will be the first to know.",
     None),                                             # no digits: unchanged
    ("Your graphics card is at 71 degrees, using 9.4 of 12 gigabytes. The download finished at 3:45 PM: 2,318 "
     "files, 14.6 gigabytes, in 6 minutes and 12 seconds.",
     "Your graphics card is at seventy-one degrees, using nine point four of twelve gigabytes. The download "
     "finished at three forty-five PM: two thousand three hundred and eighteen files, fourteen point six "
     "gigabytes, in six minutes and twelve seconds."),
    ("Would you like me to pause the updates until you have finished your game, or shall I run them now?", None),
    ("Good evening, WAYFARER. Siobhan sent word from the Wick, and Argo CD has finished syncing Prometheus.", None),
]

CASES = [
    # spelled out: quantities, units, percentages, times, money, ordinals, ranges, years
    ("45% of it", "forty-five percent of it"),
    ("99.9% up", "ninety-nine point nine percent up"),
    ("2.5 GiB used", "two point five gibibytes used"),
    ("a 12GB card", "a twelve gigabytes card"),
    ("1 GB left", "one gigabyte left"),
    ("71°C, -4°C, 98.6 °F", "seventy-one degrees Celsius, minus four degrees Celsius, ninety-eight point six "
                            "degrees Fahrenheit"),
    ("in 250ms", "in two hundred and fifty milliseconds"),
    ("2.5x faster", "two point five times faster"),
    ("at 3pm", "at three PM"),
    ("at 3:05 am.", "at three oh five AM."),
    ("at 15:00", "at fifteen hundred"),
    ("at 15:30", "at fifteen thirty"),
    ("at 9:00", "at nine o'clock"),
    ("$12.50 or £1", "twelve dollars fifty or one pound"),
    ("the 2nd and 21st and 100th", "the second and twenty-first and one hundredth"),
    ("3-5 minutes", "three to five minutes"),
    ("in 2026 and since 1999", "in twenty twenty-six and since nineteen ninety-nine"),
    ("on 2 March 2026", "on two March twenty twenty-six"),
    ("1,000,000 rows", "one million rows"),
    ("105 jobs, 1005 files", "one hundred and five jobs, one thousand and five files"),
    ("0.5 seconds", "zero point five seconds"),
    ("Thirty-four of 34 services", "Thirty-four of thirty-four services"),
    # left as written: versions, addresses, ids, hashes, paths, model names
    ("Kernel 7.2.8 is out", None),
    ("Python 3.12 and kernel 7.2", None),
    ("v0.1.5-rc1 and v2", None),
    ("192.0.2.10:8080 answers", None),
    ("fe80::1/64 and ::1", None),
    ("192.168.1.0/24", None),
    ("aa:bb:cc:dd:ee:ff", None),
    ("commit a1b2c3d4 fixed it", None),
    ("issue #12 and PR 34", None),
    ("pid 4412, exit code 137, port 8080", None),
    ("HTTP 404", None),
    ("2026-11-11 at 08:30", "2026-11-11 at eight thirty"),
    ("/usr/lib/bromigos and ~/.cache/x.json", None),
    ("see https://example.com/a1/2", None),
    ("mail ops@example.com", None),
    ("Qwen3 0.6B on sm_120 and x86_64", None),
    ("the RTX 5070 and an H100", None),
    ("GPT-4 and Llama-3.2", None),
    ("550e8400-e29b-41d4-a716-446655440000", None),
    ("file mode 0644, agent 007", None),
    ("uptime 1:02:03", None),
    # mixed: the ids stay, the quantities around them don't
    ("Qwen3 0.6B on the RTX 5070 used 2.5 GiB, 45% of it, at 71°C.",
     "Qwen3 0.6B on the RTX 5070 used two point five gibibytes, forty-five percent of it, at seventy-one degrees "
     "Celsius."),
    ("pid 4412 used 3 GB", "pid 4412 used three gigabytes"),
    ("Kernel 7.2.8 took 12 seconds on 10.0.0.2", "Kernel 7.2.8 took twelve seconds on 10.0.0.2"),
]

WORDS = [(0, "zero"), (7, "seven"), (13, "thirteen"), (40, "forty"), (71, "seventy-one"), (100, "one hundred"),
         (118, "one hundred and eighteen"), (1000, "one thousand"), (2318, "two thousand three hundred and eighteen"),
         (1_200_005, "one million two hundred thousand and five"), (-3, "minus three")]


def main():
    bad = 0
    for i, (src, want) in enumerate(FIVE + CASES):
        want = src if want is None else want
        got = S(src)
        ok = got == want
        bad += not ok
        label = f"line {i + 1}" if i < len(FIVE) else repr(src)[:60]
        print(("ok   " if ok else "FAIL ") + label + ("" if ok else f"\n     got:  {got}\n     want: {want}"))
    for n, want in WORDS:
        got = speech_text.words(n)
        bad += got != want
        print(("ok   " if got == want else "FAIL ") + f"words({n}) = {got}")
    # idempotent: speaking it twice changes nothing more
    for src, _ in FIVE + CASES:
        once = S(src)
        if S(once) != once:
            bad += 1
            print(f"FAIL not idempotent: {src!r}")
    print("all good" if not bad else f"{bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
