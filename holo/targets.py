"""Where VECTOR's voice and brain run: bromigOS's targets.toml (homelab, local, cloud;
bromigOS docs/targets.md), through `bromigos target resolve SERVICE --json`. The binary
owns the rules (bromigOS's seam: Python asks the core, never keeps a copy); this module
only caches its answer until targets.toml, homelab.toml or the private overlay changes,
so a `bromigos target local` reaches his next line and his next turn without a restart.

resolve(service) -> the entries in order ([{"target", "engine", "endpoint"?, "key_file"?}])
when the user has a targets.toml of their own; None otherwise (no file, no binary, an
older binary): voice.json and vector.toml decide, as they always have.
"""
import json
import os
import shutil
import subprocess
import threading
import time

HOME = os.path.expanduser("~")
CONFIG = os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")
WATCH = [
    os.environ.get("BROMIGOS_TARGETS_CONFIG") or os.path.join(CONFIG, "bromigos/targets.toml"),
    os.environ.get("BROMIGOS_HOMELAB_CONFIG") or os.path.join(CONFIG, "bromigos/homelab.toml"),
    os.path.join(CONFIG, "bromigos/private/config.json"),
    "/usr/share/bromigos/default/targets.toml",
]

_lock = threading.Lock()
_cache = {}          # service -> (stamp, entries or None)


def _stamp():
    out = []
    for p in WATCH:
        try:
            st = os.stat(p)
            out.append((st.st_mtime_ns, st.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _binary():
    return shutil.which("bromigos") or ("/usr/bin/bromigos" if os.path.exists("/usr/bin/bromigos") else None)


def resolve(service):
    """The service's entries in order, or None when targets.toml doesn't decide it."""
    stamp = _stamp()
    with _lock:
        hit = _cache.get(service)
        if hit and hit[0] == stamp:
            return hit[1]
    entries = None
    b = _binary()
    if b and stamp[0] is not None:            # the user's targets.toml exists
        try:
            r = subprocess.run([b, "target", "resolve", service, "--json"], capture_output=True,
                               text=True, timeout=5, stdin=subprocess.DEVNULL)
            if r.returncode == 0:
                doc = json.loads(r.stdout)
                if doc.get("configured"):
                    entries = doc.get("entries") or []
        except (OSError, ValueError, subprocess.SubprocessError):
            entries = None
    with _lock:
        _cache[service] = (stamp, entries)
    return entries


def engines(service):
    """Just the engines, in order (voice: lab, qwen, kokoro; stt: lab, local), or None."""
    e = resolve(service)
    return None if e is None else [x["engine"] for x in e]


def endpoint(service, engine):
    """The first entry's endpoint for that engine, or None."""
    for x in resolve(service) or []:
        if x.get("engine") == engine and x.get("endpoint"):
            return x["endpoint"]
    return None


_up = {}             # endpoint -> (checked at, answered)


def answers(url, ttl=60.0):
    """Whether something listens at the URL's host and port (a TCP connect, 1 s), cached
    for a minute: the brain moves on from a target that's off instead of failing a turn."""
    import socket
    from urllib.parse import urlsplit
    now = time.monotonic()
    hit = _up.get(url)
    if hit and now - hit[0] < ttl:
        return hit[1]
    u = urlsplit(url)
    ok = False
    try:
        with socket.create_connection((u.hostname, u.port or (443 if u.scheme == "https" else 80)), timeout=1.0):
            ok = True
    except OSError:
        ok = False
    _up[url] = (now, ok)
    return ok
