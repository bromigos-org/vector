"""VECTOR's reach beyond the workstation: the web (via the homelab's SearXNG) and herdr,
the operator's terminal workspace manager for AI coding agents.

web_search  SearXNG at the private overlay's endpoints.searxng (JSON), homelab CA; fails cleanly while it's down.
web_fetch   readable text via trafilatura; http/https only; no LAN targets except the lab's own
            domain (private overlay lan.domain).
herdr_*     structured wrappers over ~/.local/bin/herdr (fixed argv, never a shell string).
Watcher     herdr agents going blocked (waiting on the operator) or finishing become a
            short notice in the notify voice, rate-capped.
"""
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from ..private import PRIV

SEARX = PRIV.url("searxng")                        # private: endpoints.searxng
LAB_DOMAIN = PRIV.lan_domain()                     # private: lan.domain (its hosts are allowed)
CA = os.path.expanduser("~/.config/homelab/homelab-ca.crt")
HERDR = os.path.expanduser("~/.local/bin/herdr")
UA = "Mozilla/5.0 (X11; Linux x86_64) VECTOR/1.0 (homelab desktop assistant)"
MAX_PAGE = 2_000_000
MAX_TEXT = 8000


def _ctx():
    """Public CAs plus the homelab CA (SearXNG and the lab's own pages)."""
    c = ssl.create_default_context()
    if os.path.exists(CA):
        c.load_verify_locations(CA)
    return c


# ------------------------------------------------------------------ web
def web_search(query, n=5, category=""):
    """SearXNG; when a category comes back empty, try the others before giving up."""
    cats = [category] if category else [""]
    cats += [c for c in ("general", "it", "science", "news") if c not in cats]
    last_err = None
    for cat in cats:
        params = {"q": query[:300], "format": "json"}
        if cat:
            params["categories"] = cat
        try:
            with urllib.request.urlopen(urllib.request.Request(f"{SEARX}/search?{urllib.parse.urlencode(params)}",
                                                               headers={"User-Agent": UA}), timeout=8, context=_ctx()) as r:
                d = json.load(r)
        except Exception as e:
            last_err = e
            continue
        out = [{"title": x.get("title", "")[:160], "url": x.get("url"), "snippet": (x.get("content") or "")[:300],
                "engines": x.get("engines") or [x.get("engine")]} for x in d.get("results", [])[:max(1, min(int(n), 10))]]
        info = [{"title": b.get("infobox"), "text": (b.get("content") or "")[:400]} for b in (d.get("infoboxes") or [])[:1]]
        if out or info:
            return {"query": query, "category": cat or "general", "results": out, **({"infobox": info[0]} if info else {})}
    if last_err:
        return {"error": f"web search isn't available right now ({type(last_err).__name__}: {str(last_err)[:60]})"}
    return {"query": query, "results": [], "note": "no results in any category"}


def _allowed_url(url):
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return "only http and https URLs"
    host = u.hostname.lower()
    if LAB_DOMAIN and host.endswith("." + LAB_DOMAIN):
        return None
    if host in ("localhost",) or host.endswith((".local", ".lan", ".internal", ".home.arpa")):
        return "LAN-only hosts are off limits (except the lab's own domain)"
    try:
        for fam, _, _, _, addr in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(addr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return "that host resolves to a private address"
    except socket.gaierror:
        return "can't resolve that host"
    return None


def web_fetch(url):
    why = _allowed_url(url)
    if why:
        return {"error": why}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,text/plain"})
        with urllib.request.urlopen(req, timeout=15, context=_ctx()) as r:
            final = r.geturl()
            why = _allowed_url(final)
            if why:
                return {"error": f"redirected somewhere off limits: {why}"}
            raw = r.read(MAX_PAGE)
            ctype = r.headers.get("Content-Type", "")
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}
    html = raw.decode("utf-8", "replace")
    text = None
    if "html" in ctype or html.lstrip().startswith("<"):
        try:
            import trafilatura
            text = trafilatura.extract(html, url=final, include_comments=False, include_tables=True, favor_precision=True)
        except Exception:
            text = None
        if not text:
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", html)))
    else:
        text = html
    text = text.strip()
    cut = len(text) > MAX_TEXT
    return {"url": final, "chars": len(text), "text": text[:MAX_TEXT] + ("…[truncated]" if cut else "")}


# ------------------------------------------------------------------ herdr
def _herdr(*args, timeout=15):
    if not os.path.exists(HERDR):
        raise RuntimeError("herdr isn't installed")
    r = subprocess.run([HERDR, *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    out = (r.stdout or "").strip()
    try:
        d = json.loads(out)
    except ValueError:
        if r.returncode != 0:
            raise RuntimeError((r.stderr or out).strip()[:200])
        return out
    if isinstance(d, dict) and d.get("error"):
        raise RuntimeError(f"{d['error'].get('code')}: {d['error'].get('message')}")
    return d.get("result", d) if isinstance(d, dict) else d


def _agents():
    return (_herdr("agent", "list").get("agents") or [])


def _name(a):
    t = a.get("terminal_title_stripped") or ""
    t = re.sub(r"^[^\w]+", "", t).strip()
    return f"{a.get('agent')} in {os.path.basename(a.get('cwd') or '') or '~'}" + (f" ({t[:50]})" if t else "")


def herdr_status():
    snap = _herdr("api", "snapshot").get("snapshot", {})
    ws = {w.get("workspace_id") or w.get("id"): w.get("label") or w.get("name") for w in snap.get("workspaces", [])}
    out = []
    for a in snap.get("agents") or _agents():
        out.append({"target": a.get("terminal_id") or a.get("pane_id"), "pane": a.get("pane_id"),
                    "workspace": ws.get(a.get("workspace_id"), a.get("workspace_id")), "agent": a.get("agent"),
                    "status": a.get("agent_status"), "cwd": (a.get("cwd") or "").replace(os.path.expanduser("~"), "~"),
                    "title": a.get("terminal_title_stripped"), "focused": a.get("focused")})
    return {"agents": out, "workspaces": list(ws.values()), "panes": len(snap.get("panes", []))}


def _resolve(agent):
    """Accept a terminal/pane id, an agent name, or a fuzzy hint (repo dir, title words)."""
    agents = _agents()
    for a in agents:
        if agent in (a.get("terminal_id"), a.get("pane_id")):
            return a
    hint = agent.lower()
    scored = []
    for a in agents:
        hay = " ".join(str(a.get(k) or "") for k in ("agent", "cwd", "terminal_title_stripped")).lower()
        score = sum(w in hay for w in re.findall(r"[a-z0-9-]+", hint))
        if score:
            scored.append((score, a))
    if not scored:
        raise ValueError(f"no herdr agent matches {agent!r}")
    scored.sort(key=lambda x: -x[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        raise ValueError(f"{agent!r} matches more than one agent: " + "; ".join(_name(a) for _, a in scored[:3]))
    return scored[0][1]


def herdr_read(agent, lines=40):
    a = _resolve(agent)
    r = _herdr("agent", "read", a["terminal_id"], "--source", "recent-unwrapped", "--lines", str(max(5, min(int(lines), 200))))
    if isinstance(r, dict):
        r = r.get("read", r)
    text = r.get("text") if isinstance(r, dict) else str(r)
    text = re.sub(r"[─━═]{8,}", "", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text or "").strip()
    return {"agent": _name(a), "status": a.get("agent_status"), "recent": text[-5000:]}


def herdr_send(agent, text):
    a = _resolve(agent)
    _herdr("agent", "send", a["terminal_id"], text[:4000])
    return {"ok": True, "sent_to": _name(a), "chars": len(text)}


def herdr_start(name, cwd="~", argv=None):
    argv = argv or ["claude"]
    cwd = os.path.realpath(os.path.expanduser(cwd))
    if not os.path.isdir(cwd):
        raise ValueError(f"no such directory: {cwd}")
    r = _herdr("agent", "start", name[:40], "--cwd", cwd, "--no-focus", "--", *[str(x) for x in argv][:20], timeout=30)
    return {"ok": True, "started": name, "cwd": cwd, "argv": argv, "result": r if isinstance(r, dict) else str(r)[:200]}


def herdr_wait(agent, status="idle", timeout_s=120):
    a = _resolve(agent)
    if status not in ("idle", "working", "blocked", "unknown"):
        raise ValueError("status must be idle, working, blocked or unknown")
    t = max(1, min(int(timeout_s), 600))
    try:
        _herdr("agent", "wait", a["terminal_id"], "--status", status, "--timeout", str(t * 1000), timeout=t + 10)
        return {"ok": True, "agent": _name(a), "status": status}
    except Exception as e:
        return {"ok": False, "agent": _name(a), "detail": str(e)[:120]}


class HerdrWatcher:
    """Polls herdr every few seconds; an agent turning blocked (waiting on the operator) or
    finishing (working -> idle after a while) becomes a short spoken notice. Cheap: one
    `herdr agent list` call per poll, nothing while herdr isn't running."""

    def __init__(self, notify, every=5.0, min_work_s=20, per_hour=12):
        self.notify, self.every, self.min_work_s, self.per_hour = notify, every, min_work_s, per_hour
        self.seen = {}          # terminal id -> (status, since)
        self.sent = []          # notice times (rate cap)
        self.last_text = {}     # dedupe per agent
        threading.Thread(target=self._loop, daemon=True, name="herdr-watch").start()

    def _loop(self):
        while True:
            try:
                self._poll()
            except Exception:
                time.sleep(25)              # herdr not running: back off
            time.sleep(self.every)

    def _poll(self):
        now = time.monotonic()
        for a in _agents():
            tid, st = a.get("terminal_id"), a.get("agent_status")
            prev = self.seen.get(tid)
            self.seen[tid] = (st, prev[1] if prev and prev[0] == st else now)
            if not prev or prev[0] == st:
                continue
            msg = None
            where = os.path.basename(a.get("cwd") or "") or "home"
            if st == "blocked":
                msg = f"The {where} session is waiting on you."
            elif st == "idle" and prev[0] == "working" and now - prev[1] >= self.min_work_s:
                msg = f"The {where} session has finished."
            if msg and self._may_send(tid, msg):
                self.notify(msg, a)

    def _may_send(self, tid, msg):
        now = time.monotonic()
        self.sent = [t for t in self.sent if now - t < 3600]
        last = self.last_text.get(tid)
        if len(self.sent) >= self.per_hour or (last and last[0] == msg and now - last[1] < 120):
            return False
        self.sent.append(now)
        self.last_text[tid] = (msg, now)
        return True
