"""Text made speakable for the local voice: quantities, times, units and percentages spelled
out; versions, IP addresses, ids, paths and model names left exactly as written.

Why: the small local voice (Qwen3-TTS 0.6B) sometimes repeats or garbles a line dense with
digits ("2,318 files, 14.6 GB"): in testing, 3 renders of 6 went wrong with digits and 0 of 6
with the same line spelled out. The lab's larger model doesn't need this, so only the local
engine runs it.

VECTOR's persona keeps some numbers as written: "versions, IP addresses, commit hashes and
messages, model names and file paths are said exactly as written, in digits and characters
(kernel 7.2.8, not seven point two point eight)". Those spans are found first and left alone:

  * URLs, e-mail addresses, file paths (anything with a slash and a letter, or starting ~ or /)
  * IPv4 and IPv6 addresses (with a port or a prefix length), MAC addresses
  * versions: three or more dotted parts (7.2.8, 1.2.3-rc1), a v before digits (v2, v0.1.5),
    or a number after a word like kernel, version, Python, driver or release (Python 3.12)
  * ids: #12, a number after pid, port, issue, PR, build, job, line, exit code, HTTP and the
    like (port 8080), hex hashes and UUIDs, ISO dates (2026-11-11), numbers with a leading zero
  * model names and other mixed tokens: Qwen3, sm_120, 0.6B, x86_64, H100, GPT-4, and a
    number after a capitalised product word (RTX 5070)

Everything else with digits is spelled out in British English, which is how VECTOR talks:
2,318 -> "two thousand three hundred and eighteen", 9.4 -> "nine point four", 45% -> "forty-
five percent", 3:45 PM -> "three forty-five PM", 14.6 GB -> "fourteen point six gigabytes",
71°C -> "seventy-one degrees Celsius", $12.50 -> "twelve dollars fifty", 2nd -> "second",
3-5 -> "three to five", -4 -> "minus four", in 2026 -> "in twenty twenty-six".

Pure Python, standard library only: tools/test-speech-text.py checks it.
"""
import re

ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
        "sixteen seventeen eighteen nineteen").split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
SCALES = ((10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (10 ** 3, "thousand"))
ORD = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth", "nine": "ninth",
       "twelve": "twelfth"}

# number followed by a unit: (pattern, singular, plural); matched case-sensitively after a number
UNITS = [
    ("TiB", "tebibyte", "tebibytes"), ("GiB", "gibibyte", "gibibytes"), ("MiB", "mebibyte", "mebibytes"),
    ("KiB", "kibibyte", "kibibytes"), ("TB", "terabyte", "terabytes"), ("GB", "gigabyte", "gigabytes"),
    ("MB", "megabyte", "megabytes"), ("KB", "kilobyte", "kilobytes"), ("kB", "kilobyte", "kilobytes"),
    ("Gbps", "gigabit per second", "gigabits per second"), ("Mbps", "megabit per second", "megabits per second"),
    ("GB/s", "gigabyte per second", "gigabytes per second"), ("MB/s", "megabyte per second", "megabytes per second"),
    ("GHz", "gigahertz", "gigahertz"), ("MHz", "megahertz", "megahertz"), ("kHz", "kilohertz", "kilohertz"),
    ("Hz", "hertz", "hertz"), ("kW", "kilowatt", "kilowatts"), ("W", "watt", "watts"),
    ("ms", "millisecond", "milliseconds"), ("µs", "microsecond", "microseconds"), ("ns", "nanosecond", "nanoseconds"),
    ("min", "minute", "minutes"), ("mins", "minute", "minutes"), ("hrs", "hour", "hours"), ("h", "hour", "hours"),
    ("s", "second", "seconds"), ("sec", "second", "seconds"), ("secs", "second", "seconds"),
    ("km/h", "kilometre per hour", "kilometres per hour"), ("mph", "mile per hour", "miles per hour"),
    ("km", "kilometre", "kilometres"), ("kg", "kilogram", "kilograms"), ("fps", "frame per second", "frames per second"),
    ("°C", "degree Celsius", "degrees Celsius"), ("°F", "degree Fahrenheit", "degrees Fahrenheit"),
    ("°", "degree", "degrees"), ("%", "percent", "percent"), ("x", "times", "times"),
]
UNIT_RE = "|".join(re.escape(u[0]) for u in sorted(UNITS, key=lambda u: -len(u[0])))
UNIT = {u[0]: u[1:] for u in UNITS}

# a number after one of these words is an id or a version: left as written
ID_WORDS = (r"pid|port|ports|issue|issues|pr|prs|pull request|ticket|build|run|job|line|lines|uid|gid|exit code|"
            r"errno|error|http|status|commit|version|versions|kernel|python|release|driver|cuda|firmware|bios|"
            r"node|api|protocol|ubuntu|debian|fedora|android|ios|macos|windows|chrome|firefox|gcc|glibc|openssl|"
            r"java|php|ruby|perl|postgres|postgresql|mysql|redis|k3s|kubernetes|rc|beta|alpha|step|phase|chapter|"
            r"page|item|option|slot|gpu|cpu|core|disk|sda|nvme|vlan|vm|lxc|pve|room|channel|ch|zone|rack|unit")
MONTHS = ("january february march april may june july august september october november december "
          "jan feb mar apr jun jul aug sep sept oct nov dec").split()
YEAR_WORDS = r"in|since|by|from|until|till|of|year|around|circa|summer|winter|spring|autumn|fall|" + "|".join(MONTHS)
NOT_PRODUCT = {"AM", "PM", "OK", "I", "A", "TV", "AND", "OR", "THE", "AT", "ON", "IN", "OF", "TO", "BY"}

_PROTECT = [re.compile(p, f) for p, f in (
    (r"\bhttps?://\S+|\bwww\.\S+", re.I),
    (r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", 0),
    (r"(?:~|\.{1,2})?/[\w.@~+-]+(?:/[\w.@~+-]*)*|\b[\w.-]*[A-Za-z][\w.-]*/[\w./@~+-]+", 0),   # paths
    (r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b", 0),                                      # MAC
    (r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?:/\d{1,2})?\b", 0),                               # IPv4
    (r"(?<![\w:])(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{0,4}(?:/\d{1,3})?(?![\w:])|::1\b", 0),  # IPv6
    (r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),             # UUID
    (r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?\b", 0),  # ISO dates
    (r"\b\d{1,2}:\d{2}:\d{2}\b", 0),                                                         # 1:02:03 durations
    (r"\bv\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.]+)?\b", re.I),                                     # v2, v0.1.5-rc1
    (r"\b\d+(?:\.\d+){2,}(?:[-+][0-9A-Za-z.]+)?\b", 0),                                      # 7.2.8, 1.2.3-rc1
    (r"(?<!\w)#\d+\b", 0),                                                                   # #12
    (r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,64}\b", 0),                             # hex hashes
    (r"(?<![\d,:.])\b0\d+\b(?![,:.]\d)", 0),                                                # 007, 0644
)]
# a mixed token (letters and digits joined, or an underscore): Qwen3, sm_120, 0.6B, GPT-4, x86_64
_MIXED = re.compile(r"(?<![\w.])(?=[\w.-]*[A-Za-z])(?=[\w.-]*\d)[A-Za-z0-9_][\w.]*(?:-[\w.]+)*")
_AFTER_ID = re.compile(rf"(?i)\b(?:{ID_WORDS})\s+#?(\d[\d.,]*\d|\d)\b")
_AFTER_PRODUCT = re.compile(r"\b([A-Z][A-Z0-9]{1,})\s+(\d[\d.]*\d|\d)\b")
_PH = re.compile("\ue000(.)\ue001")


def words(n):
    """A whole number in British English words: 2318 -> two thousand three hundred and eighteen."""
    n = int(n)
    if n < 0:
        return "minus " + words(-n)
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else "-" + ONES[n % 10])
    if n < 1000:
        return ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " and " + words(n % 100))
    for size, name in SCALES:
        if n >= size:
            head, rest = divmod(n, size)
            if head >= 1000:
                return str(n)                       # past the trillions: leave it
            out = words(head) + " " + name
            if rest == 0:
                return out
            return out + (" and " if rest < 100 else " ") + words(rest)
    return str(n)


def ordinal(n):
    w = words(n)
    head, sep, last = w.rpartition("-" if "-" in w.split(" ")[-1] else " ")
    if last in ORD:
        last = ORD[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return head + sep + last


def number(s):
    """A plain number as written (2,318 · 9.4 · -4 · 0.5) in words."""
    s = s.replace(",", "")
    neg = s.startswith(("-", "−"))
    s = s.lstrip("-−")
    whole, _, frac = s.partition(".")
    out = words(int(whole or "0"))
    if frac:
        out += " point " + " ".join(ONES[int(d)] for d in frac)
    return ("minus " if neg else "") + out


def year(n):
    n = int(n)
    if n % 1000 < 10 and n % 100 == n % 1000:          # 2000-2009: "two thousand and six"
        return words(n)
    hi, lo = divmod(n, 100)
    return words(hi) + " " + ("hundred" if lo == 0 else ("oh " + ONES[lo] if lo < 10 else words(lo)))


def clock(h, m, ampm=""):
    h, m = int(h), int(m)
    ampm = ampm.upper().replace(".", "").replace(" ", "")
    if m == 0:
        body = words(h) + ("" if ampm else (" hundred" if h >= 13 or h == 0 else " o'clock"))
    else:
        body = words(h) + " " + ("oh " + ONES[m] if m < 10 else words(m))
    return body + (" " + ampm if ampm else "")


NUM = r"[-−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-−]?\d+(?:\.\d+)?"


def speakable(text):
    """The line with its quantities spelled out and its ids, versions and names untouched."""
    kept = []

    def keep(m):
        kept.append(m.group(0))
        return f"\ue000{chr(0xE100 + len(kept) - 1)}\ue001"

    def keep_group(m, g=1):
        a, b = m.span(g)
        s, e = m.span(0)
        whole = m.group(0)
        kept.append(m.group(g))
        return whole[:a - s] + f"\ue000{chr(0xE100 + len(kept) - 1)}\ue001" + whole[b - s:]

    t = text
    for rx in _PROTECT:
        t = rx.sub(keep, t)
    # number+unit written together (12GB, 250ms, 2.5x, 90%) -> apart, before mixed tokens are kept
    t = re.sub(rf"(?<![\w.])({NUM})({UNIT_RE})(?![\w/])", r"\1 \2", t)
    t = re.sub(r"(?<![\w.])(\d+)(st|nd|rd|th)\b", lambda m: ordinal(m.group(1)), t)
    # times (before mixed tokens, so 3pm is one): 3:45 PM, 15:30, 3pm
    t = re.sub(r"\b(\d{1,2}):(\d{2})(?:\s*([ap]\.?\s?m)(?![a-z]))?", lambda m: clock(m.group(1), m.group(2), m.group(3) or ""),
               t, flags=re.I)
    t = re.sub(r"\b(\d{1,2})\s*([ap]\.?m)(?![a-z])", lambda m: clock(m.group(1), 0, m.group(2)), t, flags=re.I)

    t = _AFTER_ID.sub(keep_group, t)
    t = _AFTER_PRODUCT.sub(lambda m: m.group(0) if m.group(1) in NOT_PRODUCT else keep_group(m, 2), t)
    t = _MIXED.sub(keep, t)

    # money
    def money(m):
        sym, amt = m.group(1), m.group(2).replace(",", "")
        whole, _, frac = amt.partition(".")
        big = {"$": ("dollar", "dollars"), "£": ("pound", "pounds"), "€": ("euro", "euros")}[sym]
        out = words(int(whole)) + " " + (big[0] if int(whole) == 1 else big[1])
        if frac and int(frac[:2].ljust(2, "0")):
            out += " " + words(int(frac[:2].ljust(2, "0")))
        return out
    t = re.sub(r"([$£€])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)", money, t)

    # ranges: 3-5, 10–20 (a unit after the second number still applies)
    t = re.sub(rf"(?<![\w.-])({NUM})\s?[–-]\s?({NUM})(?![\w.])",
               lambda m: number(m.group(1)) + " to " + number(m.group(2)), t)

    # number + unit
    def with_unit(m):
        n, u = m.group(1), m.group(2)
        one, many = UNIT[u]
        if u == "x":
            return number(n) + " times"
        return number(n) + " " + (one if n.lstrip("-−") in ("1", "1.0") else many)
    t = re.sub(rf"(?<![\w.])({NUM})\s?({UNIT_RE})(?![\w])", with_unit, t)

    # years after "in", "since", a month: in 2026 -> in twenty twenty-six
    t = re.sub(rf"(?i)\b({YEAR_WORDS})(\s+(?:\d{{1,2}}(?:st|nd|rd|th)?,?\s+)?)(1[5-9]\d\d|20\d\d)\b(?!\s*(?:{UNIT_RE})\b)",
               lambda m: m.group(1) + m.group(2) + year(m.group(3)), t)

    # every other plain number
    t = re.sub(rf"(?<![\w.])({NUM})(?![\w.]|\.\d)", lambda m: number(m.group(1)), t)

    return _PH.sub(lambda m: kept[ord(m.group(1)) - 0xE100], t)


if __name__ == "__main__":
    import sys
    for line in sys.argv[1:] or sys.stdin.read().splitlines():
        print(speakable(line))
