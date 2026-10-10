"""Plain text for the hologram and the voice: models sometimes answer in markdown
(Nemotron likes bold and bullets); VECTOR's words are shown as holographic text and
read aloud, so the markup goes."""
import re

_BOLD = re.compile(r"(\*\*|__)(.+?)\1", re.S)
_EM = re.compile(r"(?<![\w*])([*_])(?!\s)(.+?)(?<!\s)\1(?![\w*])", re.S)
_CODE = re.compile(r"`{1,3}([^`]*)`{1,3}")
_HEAD = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_BULLET = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+", re.M)
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
# Roleplay the fallback model adds despite the persona:
#  * stage directions: "*VECTOR blinks, then smiles.*", "*waves*", "*a bit of static*"
#  * speaker labels: "**vector**: Oh!", "VECTOR: Right."
# A single-asterisk span of two or more words, or one action verb, is a direction and
# goes; a single other word ("*me*") is emphasis and keeps the word.
_STAR = re.compile(r"(?<![\w*])\*(?!\s|\*)([^*\n]+?)(?<!\s)\*(?![\w*])")
_SPEAKER = re.compile(r"^\s*(?:\*\*|__)?\s*vector\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*", re.I | re.M)
_VERBS = re.compile(r"^(?:blinks?|nods?|waves?|smiles?|grins?|laughs?|chuckles?|giggles?|sighs?|shrugs?|"
                    r"winks?|beams?|gestures?|pauses?|coughs?|gulps?|fidgets?|flickers?|crackles?|hums?|"
                    r"beeps?|whirs?|buzz(?:es)?|static|silence|ahem|ahem\.?)$", re.I)


# VECTOR's voice markers (the brain emits them; the voice switches on them; nobody sees them):
#   ‹robot›…‹/robot›  machine readouts      ‹sci›…‹/sci›  explaining technology
#   ‹floor›…‹/floor›  markets, trades and P&L (what each role is for: voice.json)
# Lenient: ‹ › < > « » [ ] all accepted, unknown names ignored, a stray close returns to main.
ROLE_ALIAS = {"robot": "robot", "sigil": "robot", "sys": "robot", "system": "robot",
              "sci": "scientist", "scientist": "scientist", "arc": "scientist", "prof": "scientist",
              "floor": "floor", "market": "floor", "markets": "floor", "lynx": "floor",
              "main": "main", "voss": "main", "notify": "notify"}
MARK = re.compile(r"[‹<«\[]\s*(/?)\s*(" + "|".join(ROLE_ALIAS) + r")\s*[›>»\]]", re.I)
MOODS = ("calm", "excited", "concerned", "alarmed")
MOOD_ALIAS = {"calm": "calm", "excited": "excited", "happy": "excited", "delighted": "excited",
              "concerned": "concerned", "worried": "concerned", "alarmed": "alarmed", "angry": "alarmed"}
MOOD = re.compile(r"[‹<«\[]\s*mood\s*[:=]\s*([a-z]+)\s*[›>»\]]", re.I)
_PARTIAL_MARK = re.compile(r"[‹<«\[]\s*/?\s*[a-z]{0,10}(?:\s*[:=]\s*[a-z]{0,10})?\s*$", re.I)


def strip_markers(text, streaming=False):
    t = MOOD.sub("", MARK.sub("", text))
    if streaming:
        t = _PARTIAL_MARK.sub("", t)          # half a marker at the end of a stream
    return t


def _star(m):
    inner = m.group(1).strip()
    if " " in inner or _VERBS.match(inner):
        return ""
    return inner


def scrub(text):
    """Was: the operator's callsign rewritten to "the host". Since 2026-10-06 VECTOR knows
    and may say who the operator is (and calls him Sir), and the rewrite mangled
    "blackflame007" repo names, so this passes text through. Kept for its callers."""
    return text


def plain(text, bullets="· ", streaming=False):
    text = scrub(text)
    t = _SPEAKER.sub("", strip_markers(text, streaming))
    t = _LINK.sub(r"\1", t)
    t = _CODE.sub(r"\1", t)
    t = _BOLD.sub(r"\2", t)
    t = _STAR.sub(_star, t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\s+([,.!?…])", r"\1", t)        # "words *waves* ." -> "words."
    t = re.sub(r"(?m)^\W*$\n?", "", t) if t.strip() else t
    t = re.sub(r"^[ \t]+|[ \t]+$", "", t, flags=re.M)
    t = _EM.sub(r"\2", t)
    t = _HEAD.sub("", t)
    t = _BULLET.sub(bullets, t)
    t = t.replace("**", "")
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def spoken(text):
    """For the voice: no markup, no bullet glyphs, newlines become pauses."""
    text = scrub(text)
    t = plain(text, bullets="")
    t = re.sub(r"([.!?:;,…])\s*\n+\s*", r"\1 ", t)       # a line that already ends in punctuation
    return re.sub(r"\s*\n+\s*", ". ", t).strip()


# ---------------------------------------------------------------- voice splitting
_ANYMARK = r"[‹<«\[]\s*/?\s*[a-z]+(?:\s*[:=]\s*[a-z]+)?\s*[›>»\]]"
# a sentence ends at . ! ? … followed by space, a marker or the end; markers there belong to it
SENT_END = re.compile(r"(.+?[.!?…]+)((?:\s|" + _ANYMARK + r")+|$)", re.S | re.I)
# Arc's fallback: words that mean "talking about how technology works"
_TECH = re.compile(r"\b(?:algorithm|model|models|neural|network|transformer|token|tokens|gpu|gpus|cpu|cuda|kernel|"
                   r"draft|decod\w*|forward pass|verif\w*|throughput|batch\w*|pipelin\w*|"
                   r"cache|latency|bandwidth|compiler|graph|graphs|tensor|tensors|quantis|quantiz|weights?|encoder|"
                   r"decoder|codec|frequenc\w*|signal|signals|protocol stack|relay|relays|circuit|voltage|"
                   r"inference|training|parameters?|architecture|vector|vectors|embedding|embeddings|speculative|"
                   r"decoding|attention|stream(?:s|ing)?|pipeline|cluster|replica\w*|shard\w*|scheduler|"
                   r"clever|elegant|ingenious|invention|discovery|mechanism|engine)\b", re.I)
_MARKET = re.compile(r"\b(?:floor|market|markets|trade|trades|traded|fill|fills|position|positions|"
                     r"p&l|pnl|equity|drawdown|portfolio|paper|book|lineup|referee|coin|price|prices|shares?|"
                     r"long|short|rally|sell-?off|volatility|tape|allocator|cash)\b", re.I)


_OWN = {}


def _own_words():
    """The words the user's plugins add to a role (plugins.words): their own systems' names."""
    if not _OWN:
        try:
            from . import plugins
            plugins.load()
            words = plugins.WORDS
        except Exception:
            words = {}
        for role in ("floor", "scientist"):
            ws = sorted({re.escape(w) for w in words.get(role, [])}, key=len, reverse=True)
            _OWN[role] = re.compile(r"\b(?:" + "|".join(ws) + r")\b", re.I) if ws else None
    return _OWN


_NUM = re.compile(r"\b\d[\d,.]*%?|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|"
                  r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|percent)\b", re.I)


def _words(t):
    return re.findall(r"[A-Za-z0-9']+", t)


class VoiceSplitter:
    """Streamed reply text -> spoken units (role, sentence, mood).

    Roles come from the brain's markers; untagged text is main. A sentence takes the role
    that covers most of it, so voices change on whole sentences; a short sentence (under
    three words) never switches voice on its own. If a reply carries no markers at all, a
    light heuristic gives a sentence dense with technology words to the scientist, with
    hysteresis so it doesn't flap. mode != "auto" pins every sentence to that role.
    """

    def __init__(self, mode="auto", roles=("main", "robot", "scientist", "floor", "notify")):
        self.mode = mode
        self.roles = set(roles)
        self.reset()

    def reset(self):
        self.buf = ""
        self.role = "main"          # marker state at the end of what has been read
        self.last = "main"          # the voice of the last unit given out
        self.tagged = False
        self.mood = None

    def feed(self, delta, final=False):
        self.buf += delta
        out = []
        while True:
            m = SENT_END.match(self.buf)
            if not m:
                break
            if m.end(1) == len(self.buf) and not m.group(2) and not final:
                break                               # "3." may be "3.5" when more arrives
            end = m.end()
            # a "*stage direction*" can span sentence ends: wait for its closing asterisk
            while self.buf[:end].count("*") % 2 and len(self.buf) < 600 and not final:
                nxt = SENT_END.match(self.buf, end)
                if not nxt or (nxt.end(1) == len(self.buf) and not nxt.group(2)):
                    end = None
                    break
                end = nxt.end()
            if end is None:
                break
            unit = self._unit(self.buf[:end])
            self.buf = self.buf[end:]
            if unit:
                out.append(unit)
        if final and self.buf.strip():
            unit = self._unit(self.buf)
            self.buf = ""
            if unit:
                out.append(unit)
        return out

    def flush(self):
        out = self.feed("", final=True)
        self.role, self.tagged = "main", False
        return out

    def _unit(self, raw):
        mood = None
        for mm in MOOD.finditer(raw):
            mood = MOOD_ALIAS.get(mm.group(1).lower(), mood)
        raw = scrub(MOOD.sub("", raw))
        cover, role, pos = {}, self.role, 0
        for mk in MARK.finditer(raw):
            cover[role] = cover.get(role, 0) + len(_words(raw[pos:mk.start()]))
            self.tagged = True
            name = ROLE_ALIAS.get(mk.group(2).lower(), "main")
            role = "main" if mk.group(1) else name
            pos = mk.end()
        cover[role] = cover.get(role, 0) + len(_words(raw[pos:]))
        self.role = role
        text = strip_markers(raw).strip()
        if not re.search(r"\w", text):
            return None
        n = len(_words(text))
        voice = max(cover, key=lambda r: (cover[r], r == role)) if cover else "main"
        if self.mode != "auto":
            voice = self.mode
        else:
            if not self.tagged and voice == "main":
                own = _own_words()
                hits = len(_TECH.findall(text)) + (len(own["scientist"].findall(text)) if own["scientist"] else 0)
                mk = len(_MARKET.findall(text)) + (len(own["floor"].findall(text)) if own["floor"] else 0)
                nums = len(_NUM.findall(text))
                if (mk >= 2 and mk / max(n, 1) >= 0.12) or (self.last == "floor" and mk >= 1):
                    voice = "floor"
                elif nums >= 3 or (self.last == "robot" and nums >= 2):
                    voice = "robot"                     # a readout: a sentence of figures
                elif (hits >= 2 and hits / max(n, 1) >= 0.10) or (self.last == "scientist" and hits >= 1):
                    voice = "scientist"
            voice = voice if voice in self.roles else "main"
            if voice != self.last and voice != "main" and n < 3:
                voice = self.last                   # never switch into a voice for a word or two
        self.last = voice
        if mood:
            self.mood = mood
        return voice, text, mood


TAG = {"robot": "robot", "scientist": "sci", "floor": "floor", "notify": "notify"}


def tag_untagged(text):
    """A reply the model left without voice markers, marked the way the voice will speak it
    (the same sentence heuristic), so the transcript, the log and the voice agree. A reply
    that already has markers is returned unchanged."""
    if not text or MARK.search(text):
        return text
    units = VoiceSplitter().feed(text, final=True)
    if not any(v != "main" for v, _, _ in units):
        return text
    out, cur, run = [], None, []

    def close():
        if run:
            body = " ".join(run)
            out.append(f"‹{TAG[cur]}›{body}‹/{TAG[cur]}›" if cur in TAG else body)
    for voice, sent, _ in units:
        if voice != cur:
            close()
            cur, run = voice, []
        run.append(sent)
    close()
    return " ".join(out)


# ---------------------------------------------------------------- someone else's markdown, read aloud
# Claude Code's replies (claude.py) are markdown written for a screen. read_aloud() makes
# them sound natural in VECTOR's voice: headings and bold become plain sentences, a list
# is read item by item, a short table row by row ("Big monitor: Shows."), a long one is
# summarised, code becomes "a command to run is shown on screen" (a short one-liner is
# spelled out), links and URLs become their site, file paths "a file path", and emoji and
# symbols go. The caller's redaction runs first, and again here.
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*([\w+#.-]*)[^\n]*\n(.*?)(?:^[ \t]*\1[ \t]*$|\Z)", re.M | re.S)
_TABLE_ROW = re.compile(r"^\s*\|?(.+\|.+?)\|?\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_URL = re.compile(r"\b(?:https?|ftp)://([^\s/?#<>()\]]+)(?:[^\s<>()\]]*[^\s<>()\].,;:!?'\"])?", re.I)
_PATH = re.compile(r"(?<![\w/:.])(?:~|\.{1,2})?(?:/[\w.@+-]+){2,}/?|(?<![\w/:.])[\w.@+-]+(?:/[\w.@+-]+){2,}/?")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF⬀-⯿️‍"
                    "←-⇿─-▟■-◿✀-➿⌀-⏿]")
_REDACTED = re.compile(r"\[redacted(?::\s*([^\]]+))?\]")
_SHELLISH = {"sh", "bash", "zsh", "shell", "console", "fish", "terminal", "cmd", "powershell", "ps1"}


def _site(m):
    host = m.group(1).lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _spell_code(code):
    """A short one-liner, said aloud: the words, with the symbols that don't read dropped."""
    c = _URL.sub(_site, code)
    c = _PATH.sub("a file path", c)
    c = re.sub(r"[`$<>{}\[\]|\;&*#^\"]", " ", c)
    return re.sub(r"\s+", " ", c).strip()


def _code_block(lang, body):
    lines = [x for x in body.strip("\n").split("\n") if x.strip()]
    lang = (lang or "").lower()
    if len(lines) == 1:
        one = re.sub(r"^\s*[$#>]\s+", "", lines[0]).strip()
        if len(one.split()) < 8 and lang in _SHELLISH | {""}:
            return f"\n\nThe command is: {_spell_code(one)}.\n\n"
    if lang in _SHELLISH or (lines and all(re.match(r"^\s*[$#]\s", x) for x in lines[:3])):
        what = "A command to run is shown on screen." if len(lines) <= 1 else "Some commands to run are shown on screen."
    elif lang in ("diff", "patch"):
        what = "A change is shown on screen."
    elif lang in ("json", "yaml", "yml", "toml", "ini", "xml", "csv"):
        what = "Some data is shown on screen."
    elif lang in ("text", "txt", "log", "output") or not lang:
        what = "Some output is shown on screen." if len(lines) > 1 else "A line is shown on screen."
    else:
        what = "Some code is shown on screen."
    return f"\n\n{what}\n\n"


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table(rows, max_rows=6):
    head, body = _cells(rows[0]), [_cells(r) for r in rows[2:] if r.strip()]
    if not body:
        return ", ".join(h for h in head if h) + "."
    if len(body) > max_rows:
        first = [r[0] for r in body[:3] if r and r[0]]
        return (f"A table of {len(body)} rows, by {', '.join(h for h in head if h)}. The first ones are "
                + ", ".join(first[:-1]) + (" and " if len(first) > 1 else "") + (first[-1] if first else "") + ".")
    out = []
    for r in body:
        r = r + [""] * (len(head) - len(r))
        if len(head) <= 2:
            out.append(f"{r[0]}: {r[1]}." if len(r) > 1 and r[1] else f"{r[0]}.")
        else:
            rest = ", ".join(f"{h} {v}" if h else v for h, v in zip(head[1:], r[1:]) if v)
            out.append(f"{r[0]}: {rest}." if rest else f"{r[0]}.")
    return " ".join(out)


def _tables(text, max_rows=6):
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        if (i + 1 < len(lines) and _TABLE_ROW.match(lines[i]) and _TABLE_SEP.match(lines[i + 1])
                and "|" in lines[i]):
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                j += 1
            out += ["", _table(lines[i:j], max_rows), ""]
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


def _inline_code(m):
    c = m.group(1).strip()
    if _URL.search(c):
        return _URL.sub(_site, c)
    if _PATH.fullmatch(c) or (("/" in c) and " " not in c and len(c) > 3):
        return "a file path"
    if len(c.split()) <= 4:
        return _spell_code(c)
    return "a command"


def read_aloud(text, max_rows=6):
    """Markdown written for a screen, as natural speech (see above)."""
    from .redact import redact
    t = redact(text or "")
    t = _FENCE.sub(lambda m: _code_block(m.group(2), m.group(3)), t)
    t = _tables(t, max_rows)
    t = re.sub(r"(?m)^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", "", t)          # rules
    t = re.sub(r"(?m)^\s*>\s?", "", t)                                 # quotes
    t = re.sub(r"</?[a-zA-Z][^>\n]*>", "", t)                          # html tags
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) and f"an image, {m.group(1)}", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)\s]+)[^)]*\)", lambda m: m.group(1), t)      # links: their words
    t = re.sub(r"`([^`\n]+)`", _inline_code, t)
    t = _URL.sub(_site, t)
    t = _PATH.sub("a file path", t)
    t = _REDACTED.sub("something redacted", t)
    # headings: a sentence of their own
    t = re.sub(r"(?m)^\s{0,3}#{1,6}\s*(.+?)\s*#*\s*$", lambda m: m.group(1).rstrip(".:") + ".", t)
    t = _BOLD.sub(r"\2", t)
    t = _EM.sub(r"\2", t)
    t = t.replace("**", "").replace("__", "")
    # lists: each item a sentence ("1." numbering read as the order it already has)
    t = re.sub(r"(?m)^\s*(?:[-*+•]|\d+[.)])\s+(.+?)\s*$",
               lambda m: m.group(1) + ("" if re.search(r"[.!?:;]$", m.group(1)) else "."), t)
    t = t.replace("—", ", ").replace("–", ", ").replace("→", " to ").replace("←", " from ").replace("&", " and ")
    t = t.replace("·", ", ").replace("…", "...").replace("≈", "about ").replace("×", " by ")
    t = _EMOJI.sub("", t)
    t = re.sub(r"[|~^*_#`<>\\{}\[\]]", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"([.!?:;,])\s*\n+\s*", r"\1 ", t)                      # a line that ends in punctuation
    t = re.sub(r"\s*\n+\s*", ". ", t)
    t = re.sub(r"(?:\s*[.,;:]){2,}", ".", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.lstrip(" .,;:").rstrip(" ,;")
