"""The redactor for what VECTOR reads from other programs' records (Claude Code's
transcripts and logs, claude.py): every string passes through redact() before it reaches
his model, his voice or the phone. Fails closed: a value that looks like a secret goes,
even when it might not be one.

Each match becomes `[redacted: <kind>]`. Host names and LAN addresses are not secrets for
VECTOR and stay.

The token shapes are the privacy guard's (lib/privacy_guard.py PUBLIC, the same list the
dotfiles' hook and CI use), plus the shapes that only show up in live output:
  * private key blocks, whole (to the end of the text when the END line is missing)
  * Authorization / Proxy-Authorization headers, Bearer, Basic and Token values
  * X-Api-Key, X-Auth-Token, Cookie and similar headers
  * key=value, key: value, "key": "value" and --key value where the key names a secret
    (token, secret, password, api key, client secret, credential, ...), .env lines with
    `export` too; references stay ($VAR, ${VAR}, <placeholder>, ***)
  * long high-entropy values beside any key (24+ characters, mixed classes)
  * a line that is one bare high-entropy word (a token a command printed alone)
  * a 32+ character word anywhere that mixes upper and lower case and digits, with high
    entropy (generated keys, wherever they are printed)
  * `vault kv get` / `vault read` tables: every value under the Key/Value header
  * credentials inside URLs (https://user:pass@host)
  * well-known prefixes: Google (AIza, ya29.), Hugging Face, Stripe, GitLab, npm, PyPI,
    Telegram and Discord bot tokens, Anthropic and OpenAI keys

tools/test-claude.py feeds it synthetic secrets of every shape and checks that none
survives (and that no output still matches a privacy-guard token shape).
"""
import math
import re

from .. import paths  # noqa: F401  (puts this tree's lib/ on sys.path)
import privacy_guard  # noqa: E402

# the privacy guard's token shapes; its LAN-address, DNS-name and MAC shapes are not secrets here
_NOT_SECRETS = ("IPv4", "DNS", "MAC")
GUARD = [(label, re.compile(p)) for label, p in privacy_guard.PUBLIC if not any(w in label for w in _NOT_SECRETS)]

_KEY_WORDS = (r"token|secret|passw(?:or)?d|passwd|passphrase|pwd|api[_-]?key|apikey|access[_-]?key|private[_-]?key|"
              r"client[_-]?secret|credential|auth[_-]?key|signing[_-]?key|encryption[_-]?key|master[_-]?key|"
              r"session[_-]?key|webhook[_-]?(?:url|secret)|dsn|bearer|cookie|x-amz-signature|x-amz-credential|"
              r"signature|mnemonic|seed[_-]?phrase|unseal[_-]?key|root[_-]?token|role[_-]?id|secret[_-]?id")
KEY = re.compile(r"(?i)[a-z0-9_.-]*(?:" + _KEY_WORDS + r")[a-z0-9_.-]*")
# keys that carry a secret's name, place or count, never its value
KEY_REFERENCE = re.compile(r"(?i)(?:tokens|tokenizer|token_?(?:count|type|usage|url|uri|endpoint|file|path|field|"
                           r"name|env|limit|budget|ttl|policies|accessor|duration|expir\w*)|_(?:path|file|name|field|"
                           r"property|ref|env|id_field|count|ttl|url|uri|dir|mount|length|len|policy|policies)$|"
                           r"^(?:secret_?name|secretname|secret_?ref|secretref|secret_?key_?ref|secretkeyref|"
                           r"secrets?|keyring|cookies)$|"
                           r"(?:^|_)(?:max|min|input|output|total|cache\w*)_?tokens?$)")
PLACEHOLDER = re.compile(r"(?i)^(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|<[^<>]*>|\{\{[^{}]*\}\}|\*{3,}|x{3,}|\.{3}|…|"
                         r"\[redacted[^\]]*\]|\(redacted\)|redacted|null|none|nil|true|false|yes|no|on|off|"
                         r"required|optional|changeme|example|placeholder|todo|tbd|unset|empty|string|str|int|"
                         r"bool|secret|password|token|value|\$\(.*\)|`.*`)$")

R = "[redacted: {}]"


def _entropy(s):
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in (s.count(ch) for ch in set(s)))


def _classes(s):
    return sum(bool(re.search(p, s)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]"))


_UUID = re.compile(r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_HEX = re.compile(r"(?i)^[0-9a-f]+$")
_PATHISH = re.compile(r"^(?:[~./]|[A-Za-z]:\\|https?://)|/[\w.-]+/")
_HASH_KEY = re.compile(r"(?i)sha|hash|digest|commit|checksum|rev|uuid|etag|^id$|_id$|Id$|^ref$|parent|leaf|uid|"
                       r"version|integrity|nonce_?count|request_?id|session_?id|message_?id|tool_?use_?id|prompt_?id")


def high_entropy(v, key=""):
    """A value that looks generated: 24+ characters, no spaces, enough entropy and character
    classes. UUIDs, paths and git or content hashes (beside a hash-like key) are not."""
    v = v.strip().strip("\"'")
    if len(v) < 24 or re.search(r"\s", v) or _UUID.match(v) or _PATHISH.search(v):
        return False
    if _HEX.match(v):
        if key and _HASH_KEY.search(key):
            return False
        return len(v) >= 32 and len(v) != 40              # a bare 40-hex is a git commit
    if re.fullmatch(r"[A-Za-z]+(?:[-_][A-Za-z]+)*", v):  # words joined by - or _
        return False
    return _entropy(v) >= 3.6 and _classes(v) >= 2


def _secretish_value(key, v, sep):
    """Whether v, written beside a secret-named key, is a value to hide (not a reference)."""
    raw = v
    v = v.strip()
    if v.startswith(("\"", "'")) and v.endswith(v[0]) and len(v) >= 2:
        v = v[1:-1]
        quoted = True
    else:
        quoted = False
    if not v or PLACEHOLDER.match(v) or v.startswith("[redacted"):
        return False
    if re.fullmatch(r"\d{1,8}", v):                       # a count, a port, a length
        return False
    if _PATHISH.match(v) and not re.match(r"https?://[^/\s]*:[^@/\s]+@", v):  # a file the secret lives in
        return False
    if sep == "=" or quoted:
        return True
    # "key: value" (YAML or prose): a value with a digit, mixed case or a symbol, or a long one
    if len(v) >= 16 or (len(v) >= 6 and (_classes(v) >= 2 or not v.isalpha())):
        return not (" " in raw.strip() and not quoted)
    return False


# ------------------------------------------------------------------ the rules, in order
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"
                          r"(?:.*?-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|.*\Z)", re.S)
_PGP_BLOCK = re.compile(r"-----BEGIN PGP PRIVATE KEY BLOCK-----(?:.*?-----END PGP PRIVATE KEY BLOCK-----|.*\Z)", re.S)
EXTRA = [
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    ("API key", re.compile(r"\b(?:sk|rk|pk)-(?:proj-|svcacct-|admin-|live-|test-)?[A-Za-z0-9_-]{16,}")),
    ("Stripe key", re.compile(r"\b(?:sk|rk|pk|whsec)_(?:live|test)_[A-Za-z0-9]{12,}")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("GitLab token", re.compile(r"\bgl(?:pat|dt|rt|ptt|cbt|soat)-[A-Za-z0-9_-]{16,}")),
    ("Vault token", re.compile(r"\bhv[sbr]\.[A-Za-z0-9_-]{20,}|\bs\.(?=[A-Za-z0-9]*[0-9])(?=[A-Za-z0-9]*[A-Z])[A-Za-z0-9]{24}\b")),
    ("AWS key id", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[0-9A-Z]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("Google OAuth token", re.compile(r"\bya29\.[0-9A-Za-z_-]{20,}|\b1//0[0-9A-Za-z_-]{30,}")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}")),
    ("npm token", re.compile(r"\bnpm_[A-Za-z0-9]{30,}")),
    ("PyPI token", re.compile(r"\bpypi-[A-Za-z0-9_-]{50,}")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}|\bxapp-[A-Za-z0-9-]{10,}")),
    ("Slack webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+")),
    ("Discord webhook", re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+")),
    ("Telegram bot token", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}")),
    ("Discord bot token", re.compile(r"\b[MNO][A-Za-z\d_-]{23,27}\.[A-Za-z\d_-]{6}\.[A-Za-z\d_-]{27,}")),
    ("nolgia token", re.compile(r"\bnol_[A-Za-z0-9]{12,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("age secret key", re.compile(r"AGE-SECRET-KEY-1[0-9A-Za-z]{20,}")),
    ("SendGrid key", re.compile(r"\bSG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}")),
    ("Twilio key", re.compile(r"\bSK[0-9a-f]{32}\b")),
    ("DigitalOcean token", re.compile(r"\bdo[po]_v1_[a-f0-9]{40,}")),
    ("Doppler token", re.compile(r"\bdp\.(?:st|ct|sa|scim|audit)\.[A-Za-z0-9_-]{20,}")),
    ("Shopify token", re.compile(r"\bshp(?:at|ca|pa|ss)_[a-fA-F0-9]{24,}")),
    ("Linear key", re.compile(r"\blin_(?:api|oauth)_[A-Za-z0-9]{24,}")),
    ("Cloudflare token", re.compile(r"\bv1\.0-[a-f0-9]{24}-[a-f0-9]{100,}")),
]
_HEADER = re.compile(r"(?im)\b((?:proxy-)?authorization)(\"?\s*[:=]\s*\"?)(?:(bearer|basic|token|digest|negotiate|"
                     r"apikey|sso-key|aws4-hmac-sha256)\s+)?(?!\[redacted)([^\s\"',;]{4,}(?:[ ,][^\s\"',;]+=[^\s\"',;]+)*)")
_BEARER = re.compile(r"(?i)\b(bearer|token)\s+(?!\[redacted)([A-Za-z0-9._~+/=-]{16,})")


def _bearer(m):
    word, val = m.group(1), m.group(2)
    if word.lower() == "token" and not (high_entropy(val) or (_classes(val) >= 3 and len(val) >= 20)):
        return m.group(0)
    return word + " " + R.format("token")
_AUTH_HEADERS = re.compile(r"(?im)\b(x-api-key|x-auth-token|x-access-token|x-vault-token|x-goog-api-key|api-key|"
                           r"x-api-token|private-token|x-gitlab-token|x-hub-signature(?:-256)?|x-amz-security-token|"
                           r"set-cookie|cookie)(\"?\s*:\s*\"?)(?!\[redacted)([^\r\n\"]{4,})")
_URL_CRED = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+):([^\s@/]+)@")
_FLAG = re.compile(r"(?i)(--?(?:" + _KEY_WORDS + r")[a-z0-9_-]*)(=|\s+)(?!-)(?!\[redacted)(\"[^\"]*\"|'[^']*'|\S+)")
# key = value | key: value | "key": "value" | export KEY=value | key => value (one line)
_KV = re.compile(r"(?i)(?P<q>[\"']?)(?P<key>" + KEY.pattern[4:] + r")(?P=q)(?P<sep>\s*(?:=>|=|:)\s*)"
                 r"(?P<val>\"(?:[^\"\\\n]|\\.)*\"|'[^'\n]*'|[^\s,;}\])\"'<>]+)")
_ANY_KV = re.compile(r"(?P<q>[\"']?)(?P<key>[A-Za-z_][A-Za-z0-9_.-]{0,60})(?P=q)(?P<sep>\s*(?:=|:)\s*)"
                     r"(?P<val>\"[^\"\s]{24,}\"|'[^'\s]{24,}'|[A-Za-z0-9+/_=.:~-]{24,})")
_BARE = re.compile(r"(?m)^(\s*)([A-Za-z0-9+/_=.~-]{32,})\s*$")
# a long generated-looking word anywhere: 32+ characters with upper and lower case and digits
_LONG = re.compile(r"(?<![A-Za-z0-9+/_=.~-])(?=[A-Za-z0-9+/_=~-]*[a-z])(?=[A-Za-z0-9+/_=~-]*[A-Z])"
                   r"(?=[A-Za-z0-9+/_=~-]*[0-9])([A-Za-z0-9+/_=~-]{32,})(?![A-Za-z0-9+/_=~-])")
_VAULT_TABLE = re.compile(r"(?m)^(?:={2,} ?(?:Data|Secret Data|Metadata)? ?={2,}\s*\n)?Key\s{2,}Value\s*\n-{3}\s+-{5}\s*\n"
                          r"(?P<rows>(?:[^\n]*\S[^\n]*\n?)*)")


def _kv(m):
    key, val, sep = m.group("key"), m.group("val"), m.group("sep").strip()
    if KEY_REFERENCE.search(key) and not high_entropy(val, key):
        return m.group(0)
    if not _secretish_value(key, val, "=" if sep in ("=", "=>") else ":"):
        return m.group(0)
    kind = "password" if re.search(r"(?i)passw|passphrase|pwd", key) else \
        "token" if re.search(r"(?i)token|bearer", key) else "secret"
    q = val[0] if val[:1] in "\"'" and val[-1:] == val[:1] and len(val) > 1 else ""
    return m.group(0)[:m.start("val") - m.start(0)] + q + R.format(kind) + q


def _any_kv(m):
    key, val = m.group("key"), m.group("val")
    if _HASH_KEY.search(key) or not high_entropy(val, key):
        return m.group(0)
    q = val[0] if val[:1] in "\"'" else ""
    return m.group(0)[:m.start("val") - m.start(0)] + q + R.format("high-entropy value") + q


def _vault_table(m):
    out = []
    rows = m.group("rows")
    head = m.group(0)[:m.start("rows") - m.start(0)]
    for line in rows.split("\n"):
        mm = re.match(r"^(\S+)(\s{2,})(\S.*)$", line)
        out.append(f"{mm.group(1)}{mm.group(2)}" + R.format("vault value") if mm else line)
    return head + "\n".join(out)


def redact(text):
    """text with every secret-looking value replaced by [redacted: <kind>]."""
    if not text:
        return text if isinstance(text, str) else ""
    if not isinstance(text, str):
        text = str(text)
    t = _PGP_BLOCK.sub(R.format("private key"), text)
    t = _PRIVATE_KEY.sub(R.format("private key"), t)
    t = _VAULT_TABLE.sub(_vault_table, t)
    for label, rx in EXTRA:
        t = rx.sub(R.format(label), t)
    for label, rx in GUARD:
        t = rx.sub(R.format(label), t)
    t = _HEADER.sub(lambda m: m.group(1) + m.group(2) + (m.group(3) + " " if m.group(3) else "") +
                    R.format("credential"), t)
    t = _AUTH_HEADERS.sub(lambda m: m.group(1) + m.group(2) + R.format("credential"), t)
    t = _BEARER.sub(_bearer, t)
    t = _URL_CRED.sub(lambda m: m.group(1) + ":" + R.format("password") + "@", t)
    t = _FLAG.sub(lambda m: m.group(0) if not _secretish_value(m.group(1), m.group(3), "=")
                  or KEY_REFERENCE.search(m.group(1).lstrip("-")) else
                  m.group(1) + m.group(2) + R.format("secret"), t)
    t = _KV.sub(_kv, t)
    t = _ANY_KV.sub(_any_kv, t)
    t = _BARE.sub(lambda m: m.group(1) + R.format("bare token") if high_entropy(m.group(2)) else m.group(0), t)
    t = _LONG.sub(lambda m: R.format("high-entropy value") if high_entropy(m.group(1)) else m.group(0), t)
    return t


def leaks(text):
    """The token shapes still present in text (the tests' and the smoke check's scan): the
    privacy guard's and the extra prefixes. Empty when clean."""
    found = []
    for label, rx in GUARD + EXTRA:
        if rx.search(text or ""):
            found.append(label)
    if re.search(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY", text or ""):
        found.append("private key")
    return found


def deep(obj):
    """redact() over every string in a JSON-like structure (keys too)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {redact(k) if isinstance(k, str) else k: deep(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [deep(x) for x in obj]
    return obj
