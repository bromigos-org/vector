"""Real money: a service named in the user's config is off limits to VECTOR.

The generic rules live in the code that enforces them: live venue order endpoints, moving
funds and wallet keys (shell.MONEY), banking and trading sites (browser.BLOCKED_SITES,
eyes.DEFAULT_BLOCK). This file adds the user's own systems, named in vector.toml's
[real_money] table, so no one's trading system is written into this public package:

    [real_money]
    services = ["ledger-live"]              # workloads and services that move real money
    hosts = ["@private:endpoints.ledger"]   # their consoles (a reference to the private overlay works)
    systems = ["ledger"]                    # systems that hold money or venue keys
    titles = ['\\bledger\\b.*\\blive\\b']     # window titles his eyes never capture (regular expressions)
    refuse = [{match = '/api/orders\\b', what = "the ledger's order route"}]   # more commands refused
    about = "the ledger's live routes"      # how the persona names them in its list of refusals

    [real_money.code]                       # a repository's real-money code, as paths: a folder ends
    "example-org/ledger" = ["engine/live/", "console/app/*/wallet/"]    # in /, * stays inside one folder

What each list does:
  * services: his terminal refuses any command that names one (shell.check), and his act
    tools refuse a workload whose name starts with one (act._name).
  * hosts: his own browser never opens them (browser.site_blocked); his eyes never capture a
    window whose title names one.
  * systems: an Ansible playbook that names one is logged as sensitive and announced first.
  * titles: windows his eyes never capture, and host names his browser refuses.
  * refuse: commands his terminal refuses, each with what it is.
  * code: a push of commits that touch these paths is refused (shell.check_push), and a pull
    request that touches them is the host's to merge (devflow.LIMITS, for a repository that
    has a list there; a repository without one is all the host's already).

Every vector.toml that exists adds to these lists: the user's, the old location's, the
machine's (/etc) and the package's, and a file named by $VECTOR_REAL_MONEY (the tests'
made-up names, tools/fixtures/real-money.toml). None can take a rule away. vector.toml is protected
safety config (guard.PROTECTED): his terminal can't write it, and his sandbox binds it
read-only. So is this file.
"""
import os
import re
import tomllib

from ..paths import ETC, LEGACY, ROOT, USER

ERRORS = []                                    # (file, "Type: message"): a layer that couldn't be read


def _layers():
    out = [os.path.join(USER, "vector.toml")]
    if os.path.realpath(LEGACY) != ROOT:
        out.append(os.path.join(LEGACY, "vector.toml"))
    out += [os.path.join(ETC, "vector.toml"), os.path.join(ROOT, "vector.toml")]
    if os.environ.get("VECTOR_REAL_MONEY"):      # one more file (the tests' made-up names): it only adds
        out.append(os.path.expanduser(os.environ["VECTOR_REAL_MONEY"]))
    return list(dict.fromkeys(p for p in out if os.path.isfile(p)))


def _resolve(v):
    """"@private:<key>" -> the private overlay's value ("" when it has none)."""
    if isinstance(v, str) and v.startswith("@private:"):
        try:
            from ..private import PRIV
            got = PRIV.get(v[len("@private:"):].strip())
        except Exception:
            got = None
        return got if isinstance(got, str) else ""
    return v


def _strings(xs):
    return [str(_resolve(x)).strip() for x in (xs if isinstance(xs, list) else [xs] if xs else [])
            if isinstance(_resolve(x), str) and str(_resolve(x)).strip()]


def load():
    """The [real_money] tables of every vector.toml in force, merged (lists add up)."""
    out = {"services": [], "hosts": [], "systems": [], "titles": [], "refuse": [], "about": [], "code": {}}
    for path in _layers():
        try:
            with open(path, "rb") as f:
                t = tomllib.load(f).get("real_money") or {}
        except (OSError, ValueError) as e:
            ERRORS.append((path, f"{type(e).__name__}: {str(e)[:120]}"))
            continue
        if not isinstance(t, dict):
            ERRORS.append((path, "[real_money] isn't a table"))
            continue
        for k in ("services", "hosts", "systems", "titles", "about"):
            out[k] += _strings(t.get(k))
        for r in t.get("refuse") or []:
            if isinstance(r, dict) and isinstance(r.get("match"), str) and r["match"].strip():
                out["refuse"].append((r["match"], str(r.get("what") or "a real-money action")))
            elif isinstance(r, str) and r.strip():
                out["refuse"].append((r, "a real-money action"))
        for slug, paths in (t.get("code") or {}).items():
            if isinstance(slug, str) and slug.strip():
                out["code"].setdefault(slug.strip(), [])
                out["code"][slug.strip()] += [p.lstrip("/") for p in _strings(paths)]
    for k in ("services", "hosts", "systems", "titles", "about"):
        out[k] = list(dict.fromkeys(out[k]))
    out["services"] = [s.lower() for s in out["services"]]
    out["hosts"] = list(dict.fromkeys(h for h in (_host(x) for x in out["hosts"]) if h))
    for slug in out["code"]:
        out["code"][slug] = list(dict.fromkeys(out["code"][slug]))
    return out


def _host(v):
    import urllib.parse
    v = v.strip()
    return ((urllib.parse.urlparse(v if "://" in v else "http://" + v).hostname) or "").lower().rstrip(".")


def _compile(rx):
    try:
        return re.compile(rx, re.I)
    except re.error as e:
        ERRORS.append(("vector.toml", f"bad pattern {rx[:40]!r}: {e}"))
        return None


def path_rx(entries):
    """Paths in a repository (a folder ends in /, * stays inside one folder) -> one regex, or None."""
    alts = []
    for e in entries:
        e = e.lstrip("/")
        if not e:
            continue
        body = "[^/]*".join(re.escape(p) for p in e.split("*"))
        alts.append(body if e.endswith("/") else body + "$")
    return re.compile("^(?:" + "|".join(alts) + ")") if alts else None


def matches(path, entry):
    """Whether one path in a repository is the entry (a path, a folder ending in /, or a glob)."""
    rx = path_rx([entry])
    return bool(rx and rx.match((path or "").lstrip("/")))


CONFIG = load()
SERVICES = CONFIG["services"]
HOSTS = CONFIG["hosts"]
SYSTEMS = CONFIG["systems"]
TITLES = CONFIG["titles"]
CODE = CONFIG["code"]                          # "owner/repo" -> [paths]
CODE_RX = {slug.rsplit("/", 1)[-1]: path_rx(p) for slug, p in CODE.items() if path_rx(p)}   # repo name -> regex
RULES = [(rx, f"the {s} service (real money)") for s in SERVICES
         for rx in [_compile(r"(?<!\w)" + re.escape(s) + r"(?!\w)")] if rx]
RULES += [(rx, what) for m, what in CONFIG["refuse"] for rx in [_compile(m)] if rx]


def service_refusal(name):
    """-> why a workload by this name is off limits (it is, or starts with, a named
    real-money service), or None."""
    n = (name or "").lower()
    for s in SERVICES:
        if n.startswith(s):
            return f"no real money: {s} is the host's alone"
    return None


def code_rule(repo_name):
    """The regex of a repository's real-money code paths (by its name), or None."""
    return CODE_RX.get(repo_name or "")


def about():
    """How the persona names the user's own real-money systems, in a phrase ("" when none)."""
    said = ", ".join(CONFIG["about"])
    rest = [f"the {s} service" for s in SERVICES if s not in said.lower()]
    return ", ".join(([said] if said else []) + rest)
