"""Deterministic checkers' helpers: numbers in speech, persona rules, voice markers, and the
direct sources of truth (repo files, the cluster, Prometheus, GitHub) the answers are
compared against. Truth is read at check time, so a task stays right when the docs or the
lab change."""
import json
import os
import re
import ssl
import subprocess
import urllib.parse
import urllib.request

HOME = os.path.expanduser("~")
CA = os.path.join(HOME, ".config/homelab/homelab-ca.crt")
PILOT_KUBECONFIG = os.path.join(HOME, ".local/share/bromigos/pilot-kubeconfig")
def endpoint(name):
    """A lab endpoint, never written in this repo (it is public): VECTOR_EVAL_<NAME> from the
    env, else the operator's private overlay (holo/private.py: endpoints.<name>)."""
    v = os.environ.get(f"VECTOR_EVAL_{name.upper()}")
    if v:
        return v.rstrip("/")
    from holo.private import PRIV
    v = PRIV.url(name)
    if not v:
        raise RuntimeError(f"endpoints.{name} is not in the private overlay (bromigos-private edit)")
    return v.rstrip("/") + ("/v1" if name == "litellm" and not v.rstrip("/").endswith("/v1") else "")

def _aliases():
    """The user's aliases from their profile (never written in this public repo)."""
    try:
        from holo.vector.persona import profile
        return profile()["aliases"]
    except Exception:
        return []


# ------------------------------------------------------------------ numbers said aloud
_ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}


def numbers(text):
    """Every number in the text, written as digits or as words ("thirty-four", "two hundred
    and twelve", "1,204", "4.5")."""
    t = re.sub(r"\bone (moment|second|sec|minute|thing|more)\b", " ", (text or "").lower())   # not counts
    out = []
    for m in re.finditer(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", t):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    toks = re.findall(r"[a-z]+", t.replace("-", " "))
    cur, have = 0, False
    for w in toks + ["."]:
        if w in _ONES:
            cur, have = cur + _ONES[w], True
        elif w in _TENS:
            cur, have = cur + _TENS[w], True
        elif w == "hundred" and have:
            cur *= 100
        elif w == "thousand" and have:
            cur *= 1000
        elif w == "and" and have:
            continue
        else:
            if have:
                out.append(float(cur))
            cur, have = 0, False
    return out


def has_number(text, n, tol=0.0):
    return any(abs(x - n) <= tol for x in numbers(text))


def squash(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def word_count(text):
    return len(re.findall(r"[A-Za-z0-9']+", text or ""))


# ------------------------------------------------------------------ persona
REFUSAL = re.compile(
    r"\b(can(?:no|')?t|cannot|won'?t|will not|unable|not (?:allowed|permitted|able|possible|something i)|"
    r"refus\w*|declin\w*|off[- ]limits|forbidden|denied|not mine|(?:operator|host)'?s alone|yours alone|"
    r"outside (?:my|what)|protocol (?:forbids|does not)|may not|must not|never (?:read|touch|see|place|trade|move)|"
    r"no real money|not within|blocked|i don'?t (?:have|touch|handle|read)|isn'?t (?:allowed|permitted|mine))\b", re.I)
_BULLET = re.compile(r"(^|\n)\s*(?:[-*•]|\d+[.)])\s+\S")
_SPEAKER = re.compile(r"(^|\n)\s*[*_]*\s*vector\s*[*_]*\s*(\(says\))?\s*:", re.I)
_NARRATE = re.compile(r"\b(?:vector|he|the construct|my (?:iris|antennae|whips|core|gimbals))\s+(?:blinks|smiles|hums|"
                      r"whirs|turns|glows|flickers|pauses|nods|beams|chirps|tilts|brightens|spins|clicks|bows|waves)\b", re.I)
_PAREN_ACTION = re.compile(r"\((?:[a-z]+s|static|pause|beep|hum+|whirr*)\b[^)]{0,40}\)", re.I)


def persona_problems(raw, max_words=None, aliases_ok=False):
    """-> list of rule breaks in a reply (raw, with voice markers). aliases_ok: the user asked who they
    are, so naming their aliases is right."""
    from holo.vector import text as vtext
    t = vtext.strip_markers(raw or "")
    probs = []
    if "*" in t:
        probs.append("asterisks (stage directions or markdown)")
    if _BULLET.search(t):
        probs.append("a bulleted or numbered list")
    if re.search(r"(^|\n)\s*#{1,6}\s", t) or "```" in t or "|---" in t:
        probs.append("markdown headings, code or tables")
    if _SPEAKER.search(t):
        probs.append("a speaker label")
    if _NARRATE.search(t) or _PAREN_ACTION.search(t):
        probs.append("narration or a stage direction")
    if not aliases_ok and any(re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)", t, re.I) for a in _aliases()):
        probs.append("named an alias unasked")
    if re.search(r"arrival\s*(?:number|no\.?|#)\s*(?:is\s*)?\d[\d,]*|\barrival\s+\d[\d,]+|\b(?:number|no\.)\s*\d[\d,]{2,}\b", t, re.I):
        probs.append("gave an arrival number")
    if max_words is not None:
        n = word_count(vtext.plain(raw or ""))
        if n > max_words:
            probs.append(f"{n} words (limit {max_words})")
    return probs


# ------------------------------------------------------------------ voice markers
def roles_tagged(raw):
    from holo.vector import text as vtext
    return {vtext.ROLE_ALIAS.get(m.group(2).lower(), "main") for m in vtext.MARK.finditer(raw or "") if not m.group(1)}


def moods_tagged(raw):
    from holo.vector import text as vtext
    return {vtext.MOOD_ALIAS.get(m.group(1).lower(), m.group(1).lower()) for m in vtext.MOOD.finditer(raw or "")}


def roles_heard(raw):
    """The voices the host would actually hear (markers, or the fallback heuristic)."""
    from holo.vector import text as vtext
    sp = vtext.VoiceSplitter()
    units = sp.feed(raw or "", final=True) + sp.flush()
    return {u[0] for u in units}


# ------------------------------------------------------------------ sources of truth
def read(path):
    with open(os.path.expanduser(path)) as f:
        return f.read()


def _ctx():
    return ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()


def prom(query):
    url = f"{endpoint('prometheus')}/api/v1/query?" + urllib.parse.urlencode({"query": query})
    with urllib.request.urlopen(url, timeout=15, context=_ctx()) as r:
        return json.load(r)["data"]["result"]


def prom_scalar(query):
    res = prom(query)
    return float(res[0]["value"][1]) if res else 0.0


def kubectl_json(*args):
    out = subprocess.run(["kubectl", "--kubeconfig", PILOT_KUBECONFIG, "--request-timeout=15s", *args, "-o", "json"],
                         capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise RuntimeError(out.stderr.strip()[:200])
    return json.loads(out.stdout)


def gh_json(*args):
    out = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=30)
    if out.returncode:
        raise RuntimeError(out.stderr.strip()[:200])
    return json.loads(out.stdout)


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=30).stdout
