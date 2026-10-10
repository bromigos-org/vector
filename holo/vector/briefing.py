"""Proactive, bounded: VECTOR speaks up on his own only for these three, and only within limits.

Explained alerts
  Every 60 s he reads the lab's firing alerts (Prometheus /api/v1/alerts: names, labels,
  annotations). For alerts that newly fire he writes one or two lines on what matters (what
  is wrong, where, how bad) and delivers it through the live layer's codec channel (the
  LAB call, in his voice), which keeps its own gap, hourly cap and dedupe. The live layer's
  raw "alerts firing went up, now N" call stands down while he is running. Resolved
  alerts are noted quietly in the transcript.

Return briefs
  When the host comes back (an unlock after the screen was locked 10 minutes or more, or the
  first input after 2 hours without any), a short spoken brief: lab health, what the user's
  plugins add (plugins.briefing: their own systems), CI that failed, anything broken. Once
  per return, at most once an hour.

Arrivals
  What Sir asked the lab for (bromigos lab media, the media_* tools) is in his library: every
  15 minutes he reads what Sonarr, Radarr and Lidarr imported since the last look and says
  it in one line ("Cowboy Bebop: 6 episodes are in your library, sir"). Nothing at start
  (what came before isn't news); a return brief also lists what arrived while he was away.

Never during fullscreen windows, games or screen recording; text only (a notification)
when muted; nothing at all while "be quiet" is on (quiet(minutes), e.g. "be quiet for an
hour"). Everything he says is also in the transcript.
"""
import json
import os
import subprocess
import threading
import time

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".local/state/bromigos/vector-briefing.json")
LIVE = __import__("holo.paths", fromlist=["command"]).command("bromigos-live", ".config/bromigos-live/bin/bromigos-live")
ALERT_EVERY = 60
CI_REPOS = ()          # GitHub repos whose failed CI a brief reports: the plugins' (plugins.briefing repos)
BRIEF_GAP = 3600
LOCK_MIN = 600
ARRIVALS_EVERY = 900
IDLE_RETURN = 7200
RECORDERS = ("obs", "wf-recorder", "gpu-screen-recorder", "wl-screenrec", "kooha")


def _load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def _held(reason, text):
    """A notice held back by a quiet rule: shown on the panel and pip ("SILENT: FULLSCREEN"), one click plays it."""
    st = _load()
    st["suppressed"] = {"reason": reason, "t": time.time(), "text": text[:400]}
    _save(st)


def quiet(minutes=60):
    """Be quiet: no alert calls and no briefs for this long (0 = speak again now)."""
    st = _load()
    m = max(0, min(int(minutes), 24 * 60))
    st["quiet_until"] = time.time() + m * 60
    _save(st)
    return {"ok": True, "quiet_minutes": m, "until": time.strftime("%H:%M", time.localtime(st["quiet_until"])) if m else None}


def busy_reason():
    """Why now is a bad moment to speak up, or None."""
    st = _load()
    if time.time() < st.get("quiet_until", 0):
        return "asked to be quiet"
    try:
        aw = json.loads(subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=5).stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        aw = {}
    if aw.get("fullscreen"):
        return "a fullscreen window"
    cls = (aw.get("class") or "").lower()
    if cls.startswith(("steam_app_", "gamescope")) or cls in ("steam", "lutris", "heroic", "minecraft", "factorio"):
        return "a game"
    for p in RECORDERS:
        if subprocess.run(["pgrep", "-x", p], capture_output=True).returncode == 0:
            return "screen recording"
    return None


# ------------------------------------------------------------------ alerts
def firing():
    """{key: alert} for the lab's firing alerts: the alerts capability of bromigos-homelab
    (Prometheus, Alertmanager, …). {} without the package or an alerts source; raises when the
    source is unreachable (the caller says "unavailable")."""
    from .tools import homelab
    H = homelab()
    if not H or not H.available("alerts"):
        return {}
    res = H.alerts()
    if not res["ok"]:
        raise OSError("; ".join(res["errors"].values()) or "alerts unreachable")
    out = {}
    for a in res["items"]:
        lab = a.get("labels") or {}
        if a.get("state") != "firing" or a.get("name") in ("Watchdog", "InfoInhibitor"):
            continue                          # always firing by design (the alerting pipeline's heartbeat)
        key = (a.get("name") or "?") + "|" + "|".join(f"{k}={v}" for k, v in sorted(lab.items()) if k != "alertname")
        out[key] = {"name": a.get("name"), "severity": a.get("severity"),
                    "where": lab.get("namespace") or lab.get("instance") or lab.get("node") or lab.get("pod") or "",
                    "labels": {k: v for k, v in lab.items() if k in ("namespace", "pod", "instance", "node", "job",
                                                                     "deployment", "service", "container")},
                    "summary": a.get("summary") or "", "since": a.get("since")}
    return out


def _compose(system, facts, max_tokens=180):
    """A short line in VECTOR's voice from facts (the lab's model; plain text, no markers)."""
    import ssl
    import urllib.request
    from .brain_pai import BASE
    from . import llm
    key = llm.key()
    ca = os.path.join(HOME, ".config/homelab/homelab-ca.crt")
    ctx = ssl.create_default_context(cafile=ca) if os.path.exists(ca) else ssl.create_default_context()
    body = {"model": llm.model(), "max_tokens": max_tokens, "temperature": 0.3, "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(facts, default=str)[:12000]}]}
    req = urllib.request.Request(BASE + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
        from .text import scrub, strip_markers
        return scrub(strip_markers(json.load(r)["choices"][0]["message"]["content"])).strip()


def _style(p=None):
    """'the caretaker on a line to …' with what to call the user, from their profile."""
    from . import persona
    p = persona.profile() if p is None else p
    w = persona.words(p)
    forms = p["address"]
    if forms:
        call = f"call {w['him']} '{forms[0]}'" + (f", or now and then '{forms[1]}'" if forms[1:] else "")
    elif p["name"]:
        call = f"call {w['him']} {p['name']}"
    else:
        call = "use no form of address and no name"
    return f"You are VECTOR, the caretaker on a line to {w['ref']} ({call}; never 'host' or 'operator')", w


def alert_style(p=None):
    head, _ = _style(p)
    return (head + ". Lab alerts just started firing; the facts are below. In one or two short spoken sentences "
            "(under 40 words), say what is wrong, where, and how serious, plainly; the most severe first. No markdown, "
            "no lists, no greeting, no 'Lab here'. Only what the facts say.")


def brief_style(p=None):
    head, w = _style(p)
    own = "".join(f"{d['about']} since {w['his']} last session, then " for d in _own().values() if d["about"])
    return (head + f", greeting {w['him']} on {w['his']} return. From the facts below, give a brief of three or four "
            "short spoken sentences (under 80 words): lead with anything broken or failed, then lab health, then "
            f"{own}anything else worth knowing. Only what the "
            "facts say; if everything is fine, say so in one sentence. No markdown, no lists.")


def _own():
    """The facts the user's plugins add to a brief (plugins.briefing), by name."""
    try:
        from . import plugins
        plugins.load()
        return dict(plugins.BRIEFING)
    except Exception:
        return {}


class Briefing:
    def __init__(self, speak, note, locked=None):
        """speak(text, voice) says it (and shows it); note(text) is transcript-only; locked() -> bool."""
        self.speak, self.note = speak, note
        self.locked = locked or (lambda: subprocess.run(["pgrep", "-x", "gtklock|hyprlock"], capture_output=True).returncode == 0)
        self.seen = None              # alert keys already explained
        self.lock_t = None
        self.last_cursor, self.cursor_t = None, time.time()
        self.away_since = None
        threading.Thread(target=self._alerts, daemon=True, name="briefing-alerts").start()
        threading.Thread(target=self._returns, daemon=True, name="briefing-returns").start()
        threading.Thread(target=self._arrivals, daemon=True, name="briefing-arrivals").start()

    # -------------------------------------------------------------- alerts
    def _alerts(self):
        while True:
            try:
                now = firing()
                if self.seen is None:
                    self.seen = set(now)         # alerts already firing at start: no call
                new = {k: v for k, v in now.items() if k not in self.seen}
                gone = self.seen - set(now)
                self.seen = set(now)
                if gone:
                    self.note(f"Resolved in the lab: {', '.join(sorted({k.split('|')[0] for k in gone}))}.")
                why = busy_reason() if new else None
                if new and why and why != "asked to be quiet":
                    _held(why, "Lab alerts: " + ", ".join(sorted({a["name"] for a in new.values()})) + " started firing.")
                if new and not why:
                    facts = {"new": list(new.values()), "still_firing_total": len(now)}
                    try:
                        text = _compose(alert_style(), facts, 120)
                    except Exception:
                        a = sorted(new.values(), key=lambda x: x.get("severity") != "critical")[0]
                        text = f"{a['name']} is firing{' in ' + a['where'] if a['where'] else ''}: {a['summary'] or 'no summary'}."
                    self._deliver_alert(text)
                    st = _load()
                    st["last_alert_call"] = time.time()
                    _save(st)
            except Exception as e:
                print("briefing: alerts:", type(e).__name__, str(e)[:120], flush=True)
            time.sleep(ALERT_EVERY)

    def _deliver_alert(self, text):
        """Through the live layer's codec channel (its gap, cap and dedupe); else in his own voice."""
        r = subprocess.run([LIVE, "codec", text, "LAB"], capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            self.speak(text, "notify")
        else:
            self.note("Lab: " + text)

    # -------------------------------------------------------------- arrivals
    def _arrivals(self):
        from . import tools
        st = _load()
        st["media_seen"] = time.time()                  # what came before he started isn't news
        _save(st)
        while True:
            time.sleep(ARRIVALS_EVERY)
            try:
                if "media_recent" not in tools.FUNCS:
                    continue
                st = _load()
                since = st.get("media_seen") or time.time() - ARRIVALS_EVERY
                got = tools.FUNCS["media_recent"](hours=max(1, (time.time() - since) / 3600 + 0.1)).get("imported") or []
                fresh = [g for g in got if _epoch(g.get("at")) > since]
                st["media_seen"] = time.time()
                _save(st)
                if not fresh:
                    continue
                text = arrivals_line(fresh)
                why = busy_reason()
                if why == "asked to be quiet":
                    self.note(text)
                elif why:
                    _held(why, text)
                else:
                    self.speak(text, "notify")
            except Exception as e:
                print("briefing: arrivals:", type(e).__name__, str(e)[:120], flush=True)

    # -------------------------------------------------------------- returns
    def _returns(self):
        while True:
            try:
                now = time.time()
                if self.locked():
                    if self.lock_t is None:
                        self.lock_t = now
                else:
                    if self.lock_t is not None:
                        away = now - self.lock_t
                        self.lock_t = None
                        if away >= LOCK_MIN:
                            self._brief(away)
                    cur = subprocess.run(["hyprctl", "cursorpos"], capture_output=True, text=True, timeout=5).stdout.strip()
                    if cur != self.last_cursor:
                        idle = now - self.cursor_t
                        self.last_cursor, self.cursor_t = cur, now
                        if idle >= IDLE_RETURN:
                            self._brief(idle)
            except Exception as e:
                print("briefing: returns:", type(e).__name__, str(e)[:120], flush=True)
            time.sleep(10)

    def _brief(self, away_s):
        st = _load()
        if time.time() - st.get("last_brief", 0) < BRIEF_GAP:
            return
        why = busy_reason()
        if why:
            if why != "asked to be quiet":
                _held(why, f"Welcome back, sir. You were away {round(away_s / 60)} minutes; ask me to brief you when you're ready.")
            return
        time.sleep(8)                                   # let him settle in (and the lock fade)
        facts = gather(away_s)
        try:
            text = _compose(brief_style(), facts, 220)
        except Exception as e:
            text = f"Welcome back, sir. I couldn't put the brief together ({type(e).__name__})."
        st["last_brief"] = time.time()
        _save(st)
        self.speak(text, "main")


def _epoch(iso):
    try:
        import datetime as dt
        return dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0


def arrivals_line(groups):
    """'In your library now, sir: Cowboy Bebop (6 episodes), Cowboy Bebop: The Movie.'"""
    bits = []
    for g in groups[:5]:
        n = len(g.get("items") or [])
        if g.get("app") == "sonarr":
            bits.append(f"{g['title']} ({n} episode{'s' if n != 1 else ''})" if n > 1 else
                        f"{g['title']} {g['items'][0]}")
        elif g.get("app") == "lidarr":
            bits.append(f"{g['title']}: {', '.join(str(x) for x in (g.get('items') or [])[:2])}")
        else:
            bits.append(str(g["title"]))
    more = f", and {len(groups) - 5} more" if len(groups) > 5 else ""
    return "In your library now, sir: " + ", ".join(bits) + more + "."


def gather(away_s):
    """The facts for a return brief (each lookup is best-effort)."""
    from . import tools
    out = {"away_minutes": round(away_s / 60)}

    def take(name, fn):
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = f"unavailable ({type(e).__name__})"
    if "lab_status" in tools.FUNCS:                      # bromigos-homelab's tools, when the homelab has them
        take("lab", lambda: tools.FUNCS["lab_status"]("overview"))
    if "argocd_apps" in tools.FUNCS:
        take("argocd_problems", lambda: tools.FUNCS["argocd_apps"](problems_only=True).get("problems"))
    take("alerts_firing", lambda: [{"name": a["name"], "where": a["where"], "severity": a["severity"]} for a in firing().values()])
    if "media_recent" in tools.FUNCS:                    # what he asked the lab for, in the library now
        take("media_arrived", lambda: [{"title": g["title"], "what": g.get("items")[:6]} for g in
                                       tools.FUNCS["media_recent"](hours=max(1, away_s / 3600)).get("imported") or []][:10]
             or "nothing new")
    own = _own()
    for name, d in own.items():
        take(name, d["fn"])
    since = time.time() - away_s
    repos = list(dict.fromkeys(list(CI_REPOS) + [r for d in own.values() for r in d["repos"]]))

    def ci():
        bad = []
        for repo in repos:
            r = subprocess.run(["gh", "run", "list", "-R", repo, "--limit", "10", "--json",
                                "workflowName,conclusion,createdAt,headBranch"], capture_output=True, text=True, timeout=20)
            for x in json.loads(r.stdout or "[]"):
                t = time.mktime(time.strptime(x["createdAt"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
                if t >= since and x.get("conclusion") in ("failure", "timed_out", "cancelled"):
                    bad.append(f"{repo.split('/')[1]}: {x['workflowName']} on {x['headBranch']} {x['conclusion']}")
        return bad or "none failed"
    take("ci_failures", ci)
    return out


BRIEFING = None


def briefing_now():
    """A brief right now ("brief me", "what did I miss?"): the facts, for VECTOR to say."""
    return gather(4 * 3600)
