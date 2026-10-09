"""VECTOR's conversation history, read from the chat log (the source of truth, never rewritten:
~/.local/state/bromigos/vector-chat.log, JSONL, kept forever).

Sessions: a new one starts after a 10-minute gap or when the brain is reset (a
{"role": "session_start"} line). Titles are generated once per finished session on hive
(two or three words of plain description) and cached in vector-sessions.json; until
then a session is titled by its first question.
"""
import datetime as dt
import json
import os
import re
import ssl
import threading
import time
import urllib.request

from ..private import PRIV

STATE = os.path.expanduser("~/.local/state/bromigos")
LOG = os.path.join(STATE, "vector-chat.log")
TITLES = os.path.join(STATE, "vector-sessions.json")
GAP = 600
from . import llm  # noqa: E402
KEY = llm.key_file()
CA = os.path.expanduser("~/.config/homelab/homelab-ca.crt")
MARK = re.compile(r"[‹<«\[]\s*/?\s*(robot|sci|scientist|floor|main|notify|mood\s*[:=]\s*[a-z]+)\s*[›>»\]]", re.I)


def _ts(s):
    try:
        return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
    except (ValueError, TypeError):
        return None


def clean(text):
    return MARK.sub("", text or "").strip()


def sessions():
    """-> newest first: [{id, start, end, title, count, messages:[{role, text, t}]}]"""
    out, cur, last = [], None, None
    try:
        f = open(LOG)
    except OSError:
        return []
    with f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = _ts(d.get("t"))
            if not t:
                continue
            role = d.get("role")
            if role == "session_start" or cur is None or (last and (t - last).total_seconds() > GAP):
                cur = {"id": d["t"], "start": t, "end": t, "messages": []}
                out.append(cur)
            last = t
            if role in ("user", "vector", "pilot"):
                cur["messages"].append({"role": "you" if role == "user" else "vector", "text": clean(d.get("text")), "t": t})
                cur["end"] = t
            elif role == "tool":
                cur["messages"].append({"role": "tool", "text": d.get("name", ""), "t": t})
    out = [s for s in out if any(m["role"] == "you" for m in s["messages"])]
    titles = _titles()
    for s in out:
        s["count"] = sum(1 for m in s["messages"] if m["role"] in ("you", "vector"))
        first = next(m["text"] for m in s["messages"] if m["role"] == "you")
        s["title"] = titles.get(s["id"], {}).get("title") or (first if len(first) <= 60 else first[:57] + "…")
        s["summary"] = titles.get(s["id"], {}).get("summary", "")
    return list(reversed(out))


def _titles():
    try:
        with open(TITLES) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def search(query, limit=20):
    """Messages matching every word of `query` -> [(session, message)] newest first."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
    hits = []
    for s in sessions():
        for m in s["messages"]:
            if m["role"] != "tool" and words and all(w in m["text"].lower() for w in words):
                hits.append((s, m))
                if len(hits) >= limit:
                    return hits
    return hits


def in_range(when):
    """'today', 'yesterday', 'last week', a weekday, or YYYY-MM-DD -> (start, end) local dates."""
    today = dt.date.today()
    w = (when or "").strip().lower()
    if not w:
        return None
    if w == "today":
        return today, today
    if w == "yesterday":
        d = today - dt.timedelta(days=1)
        return d, d
    if w in ("last week", "this week", "past week"):
        return today - dt.timedelta(days=7), today
    if w in ("last month", "past month"):
        return today - dt.timedelta(days=31), today
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    if w in days:
        back = (today.weekday() - days.index(w)) % 7 or 7
        d = today - dt.timedelta(days=back)
        return d, d
    try:
        d = dt.date.fromisoformat(w)
        return d, d
    except ValueError:
        return None


def tool(query="", when="", limit=8):
    """For VECTOR: session summaries (by date) or matching snippets (by words)."""
    rng = in_range(when)
    ss = sessions()
    if rng:
        ss = [s for s in ss if rng[0] <= s["start"].date() <= rng[1]]
    if query:
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
        found = []
        for s in ss:
            msgs = [m for m in s["messages"] if m["role"] != "tool" and any(w in m["text"].lower() for w in words)]
            if msgs:
                found.append({"when": s["start"].strftime("%a %d %b %H:%M"), "title": s["title"],
                              "snippets": [f"{m['role']}: {m['text'][:220]}" for m in msgs[:3]]})
            if len(found) >= limit:
                break
        return {"query": query, "when": when or "any time", "sessions": found}
    return {"when": when or "recent", "sessions": [
        {"when": s["start"].strftime("%a %d %b %H:%M"), "title": s["title"], "messages": s["count"],
         "summary": s["summary"] or "; ".join(m["text"][:80] for m in s["messages"] if m["role"] == "you")[:300]}
        for s in ss[:limit]]}


def title_missing(max_sessions=40):
    """Background: give finished sessions a short title and one-line summary on hive."""
    def work():
        titles = _titles()
        now = dt.datetime.now().astimezone()
        todo = [s for s in sessions()[:max_sessions]
                if s["id"] not in titles and (now - s["end"]).total_seconds() > GAP and s["count"] >= 2]
        if not todo:
            return
        key = llm.key()
        ctx = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()
        for s in todo:
            convo = "\n".join(f"{m['role']}: {m['text'][:300]}" for m in s["messages"] if m["role"] != "tool")[:4000]
            body = {"model": llm.model(), "max_tokens": 120, "temperature": 0.2,
                    "chat_template_kwargs": {"enable_thinking": False},
                    "messages": [{"role": "system", "content": "Give a conversation a title of at most six plain words, then "
                                  "a one-sentence summary. Answer as JSON: {\"title\": ..., \"summary\": ...}."},
                                 {"role": "user", "content": convo}]}
            try:
                req = urllib.request.Request(llm.url("/chat/completions"), data=json.dumps(body).encode(),
                                             headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                    txt = json.load(r)["choices"][0]["message"]["content"]
                m = re.search(r"\{.*\}", txt, re.S)
                d = json.loads(m.group(0)) if m else {}
                if d.get("title"):
                    titles[s["id"]] = {"title": str(d["title"])[:60], "summary": str(d.get("summary", ""))[:300]}
            except Exception:
                continue
            tmp = TITLES + ".part"
            with open(tmp, "w") as f:
                json.dump(titles, f)
            os.replace(tmp, TITLES)
            time.sleep(0.2)
    threading.Thread(target=work, daemon=True, name="history-titles").start()
