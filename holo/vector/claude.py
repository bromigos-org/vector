"""VECTOR reads Claude Code on this machine: its sessions, transcripts (the background jobs'
own conversations too), memory files and logs. Read only, and every string is redacted
(redact.py) before it reaches his model, his voice or the phone.

  claude_sessions(project, live_only, session, limit)
      sessions newest first: project, start and last activity, a title (Claude Code's own
      title, else the first prompt), message counts, whether it's live now and waiting on
      the operator, its herdr pane, and its background jobs (subagents) with their
      descriptions and status. One session (session=) shows all its jobs.
  claude_read(session, since, before, limit, include_tools, latest)
      the conversation as plain text, paged (cursors are byte offsets, so a 200 MB file is
      read from where the page starts, never whole). latest (the default without since):
      the last messages, and for a live session what it's doing now and its running jobs.
      A job's transcript reads the same way (session: its agent id).
  claude_search(query, project, days, limit, include_tools)
      words or a "phrase" across transcripts and jobs, newest first, bounded in time.
  claude_memory(project, file)        the memory index (MEMORY.md) and its files
  claude_logs(kind, since, session)   debug logs, the prompt history, background task output
  claude_say_latest(session, n, about)  Claude's latest reply (or the one about X), read
                                        aloud in VECTOR's voice (text.read_aloud)
  claude_follow(mode, session)        follow mode: each new final reply is read aloud

Which session: a session id or its first characters, a job's agent id, a hint ("the
bromigOS one", "left pane", "the research one"), or nothing: the one live session; with several,
the focused herdr pane, else the clearly most recent; when that's a toss-up the result
says so (ambiguous, with the question to ask).

What is never opened: Claude Code's login (.credentials.json), the session keys
(sessions/*.key), session-env, shell snapshots, pastes, uploads, file history, settings
and plugins. _readable() is the allowlist; every open goes through _open() in "rb".
Nothing here writes, deletes or sends anything to a Claude session; herdr is asked only
to list its panes. Cloud sessions (claude.ai/code) aren't on this disk and aren't seen.
"""
import datetime as dt
import glob
import json
import os
import re
import subprocess
import threading
import time

from .redact import redact

HOME = os.path.expanduser("~")
UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
LIVE_S = 180                     # a transcript written in the last 3 minutes counts as live
RUNNING_S = 90                   # a job or task output written in the last 90 s is running
SEARCH_BUDGET_S = 12.0
OUT_BUDGET = 5600                # characters of text per answer (tools._short caps at 7000)


def root():
    return os.path.realpath(os.environ.get("VECTOR_CLAUDE_HOME") or os.environ.get("CLAUDE_CONFIG_DIR")
                            or os.path.join(HOME, ".claude"))


def tasks_root():
    return os.path.realpath(os.environ.get("VECTOR_CLAUDE_TASKS") or f"/tmp/claude-{os.getuid()}")


def herdr_bin():
    return os.environ.get("VECTOR_HERDR_BIN") or os.path.join(HOME, ".local/bin/herdr")


# ------------------------------------------------------------------ what may be opened
_ALLOWED = [
    r"projects/[^/]+/" + UUID_RE + r"\.jsonl",
    r"projects/[^/]+/" + UUID_RE + r"/subagents/agent-[A-Za-z0-9_-]+\.(?:jsonl|meta\.json)",
    r"projects/[^/]+/memory/[^/]+\.md",
    r"projects/[^/]+/sessions-index\.json",
    r"history\.jsonl",
    r"sessions/\d+\.json",
    r"debug/[^/]+\.txt",
    r"transcripts/ses_[A-Za-z0-9]+\.jsonl",
]
_ALLOWED_RE = re.compile("^(?:" + "|".join(_ALLOWED) + ")$")
_TASK_RE = re.compile(r"^[^/]+/" + UUID_RE + r"/tasks/[A-Za-z0-9_-]+\.output$")


def _readable(path):
    """Whether VECTOR's Claude tools may open this file: Claude Code's transcripts, jobs,
    memory, history, live-session records and debug logs, and background task output.
    Nothing else (no credentials, keys, session environments, pastes or settings)."""
    r, t = root(), tasks_root()
    try:
        if os.path.islink(path) and os.path.abspath(path).startswith((t + os.sep, os.path.abspath(t) + os.sep)):
            return False                     # a task link is a job's transcript: read as a job, not as output
        rp = os.path.realpath(path)
    except (OSError, ValueError):
        return False
    if rp.startswith(r + os.sep):
        return bool(_ALLOWED_RE.match(rp[len(r) + 1:]))
    if rp.startswith(t + os.sep):
        return bool(_TASK_RE.match(rp[len(t) + 1:])) and not os.path.islink(path)
    return False


def _open(path):
    """The one way this module opens a file: allowlisted, binary, read only."""
    if not _readable(path):
        raise PermissionError(f"not one of Claude Code's readable records: {os.path.basename(path)}")
    return open(path, "rb")


def _clip(s, n):
    s = s or ""
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _local(ts):
    """An ISO timestamp (or epoch ms/s) -> 'YYYY-MM-DD HH:MM' local time."""
    try:
        if isinstance(ts, (int, float)):
            d = dt.datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts)
        else:
            d = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone()
        return d.strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return None


def _epoch(ts):
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _ago(epoch):
    s = max(0, time.time() - epoch)
    if s < 90:
        return f"{int(s)} s ago"
    if s < 5400:
        return f"{int(s // 60)} min ago"
    if s < 172800:
        return f"{s / 3600:.0f} h ago"
    return f"{s / 86400:.0f} days ago"


def _when(spec):
    """'2h', '30m', '3d', 'today', 'yesterday', 'last night', an ISO date/time -> epoch, or None."""
    if spec in (None, ""):
        return None
    s = str(spec).strip().lower()
    now = dt.datetime.now()
    m = re.fullmatch(r"(\d+)\s*(s|m|min|h|hr|d|w)", s)
    if m:
        n, u = int(m.group(1)), m.group(2)
        return time.time() - n * {"s": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600, "d": 86400, "w": 604800}[u]
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if s == "today":
        return midnight.timestamp()
    if s == "yesterday":
        return (midnight - dt.timedelta(days=1)).timestamp()
    if s in ("last night", "tonight"):
        return (midnight - dt.timedelta(hours=6)).timestamp()
    try:
        d = dt.datetime.fromisoformat(s.replace("z", "+00:00"))
        return d.timestamp()
    except ValueError:
        raise ValueError("since: a time like 2h, 30m, 3d, today, yesterday, last night or 2026-10-09T18:00")


# ------------------------------------------------------------------ records -> messages
_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_CMD = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_CMD_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_TAGGED = re.compile(r"<(local-command-stdout|local-command-stderr|bash-stdout|bash-stderr|bash-input|"
                     r"command-message|task-notification|user-prompt-submit-hook)>(.*?)</\1>", re.S)


def _user_text(s):
    """A typed prompt, cleaned: no system reminders or caveats; a slash command as typed."""
    if not isinstance(s, str):
        return ""
    s = _REMINDER.sub("", s)
    m = _CMD.search(s)
    if m:
        a = _CMD_ARGS.search(s)
        return (m.group(1).strip() + " " + (a.group(1).strip() if a else "")).strip()
    s = _TAGGED.sub(lambda mm: "" if mm.group(1) != "bash-input" else "! " + mm.group(2), s)
    if s.lstrip().startswith("Caveat: The messages below were generated"):
        return ""
    return s.strip()


def _result_text(content, lines):
    if isinstance(content, list):
        content = "\n".join(c.get("text", "") if isinstance(c, dict) and c.get("type") == "text" else
                            "[image]" if isinstance(c, dict) and c.get("type") == "image" else "" for c in content)
    if isinstance(content, dict):
        content = json.dumps(content)[:4000]
    ls = str(content or "").split("\n")
    head = "\n".join(x[:200] for x in ls[:lines])
    return head + (f"\n… {len(ls) - lines} more lines" if len(ls) > lines else "")


def arg_summary(name, inp):
    """A tool call in a few words: what it ran or touched."""
    if not isinstance(inp, dict):
        return ""
    for k in ("command", "file_path", "path", "pattern", "url", "query", "description", "prompt", "skill", "notebook_path"):
        v = inp.get(k)
        if isinstance(v, str) and v:
            first = v.strip().split("\n")[0]
            extra = f" in {inp['path']}" if k == "pattern" and inp.get("path") else ""
            return _clip(first, 140) + extra
    parts = [f"{k}={_clip(str(v), 40)}" for k, v in list(inp.items())[:2]]
    return ", ".join(parts)


def messages(rec, include_tools=False, result_lines=8):
    """One JSONL record -> [{who, text, ...}]: what a person reads in the conversation.
    who: you (the operator's prompts), claude, tool (a call), result (its first lines),
    summary (a compacted earlier part), recap, job (a background job's notice)."""
    out = []
    t = rec.get("type")
    ts = rec.get("timestamp")
    if t == "user":
        if rec.get("isMeta"):
            return out
        msg = rec.get("message") or {}
        c = msg.get("content")
        if rec.get("isCompactSummary"):
            return [{"who": "summary", "text": _clip(_REMINDER.sub("", c if isinstance(c, str) else _result_text(c, 40)), 800), "ts": ts}]
        if isinstance(c, str):
            if "<task-notification>" in c:
                m = re.search(r"<summary>(.*?)</summary>", c, re.S)
                if m:
                    return [{"who": "job", "text": m.group(1).strip(), "ts": ts}]
            txt = _user_text(c)
            if txt:
                out.append({"who": "you", "text": txt, "ts": ts})
            return out
        for b in c or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                txt = _user_text(b.get("text", ""))
                if txt:
                    out.append({"who": "you", "text": txt, "ts": ts})
            elif b.get("type") == "image":
                out.append({"who": "you", "text": "[an image]", "ts": ts})
            elif b.get("type") == "tool_result" and include_tools:
                out.append({"who": "result", "text": _result_text(b.get("content"), result_lines), "ts": ts,
                            **({"error": True} if b.get("is_error") else {})})
    elif t == "assistant":
        msg = rec.get("message") or {}
        final = msg.get("stop_reason") in ("end_turn", "stop_sequence")
        for b in msg.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                out.append({"who": "claude", "text": b["text"].strip(), "ts": ts, "final": final, "id": msg.get("id")})
            elif b.get("type") == "tool_use" and include_tools:
                out.append({"who": "tool", "text": f"{b.get('name')}: {arg_summary(b.get('name'), b.get('input'))}", "ts": ts})
    elif t == "attachment":
        a = rec.get("attachment") or {}
        if a.get("type") == "queued_command" and isinstance(a.get("prompt"), str):
            txt = _user_text(a["prompt"])
            if txt:
                out.append({"who": "you", "text": txt, "ts": ts or a.get("timestamp")})
    elif t == "system" and rec.get("subtype") == "away_summary" and include_tools:
        out.append({"who": "recap", "text": _clip(rec.get("content") or "", 600), "ts": ts})
    elif t == "summary" and rec.get("summary"):
        out.append({"who": "summary", "text": _clip(rec["summary"], 400), "ts": ts})
    elif t in ("tool_use", "tool_result") and "tool_name" in rec and include_tools:      # the old transcripts/ format
        if t == "tool_use":
            out.append({"who": "tool", "text": f"{rec['tool_name']}: {arg_summary(rec['tool_name'], rec.get('tool_input'))}", "ts": ts})
        else:
            out.append({"who": "result", "text": _result_text(rec.get("tool_output"), result_lines), "ts": ts})
    if t == "user" and "message" not in rec and isinstance(rec.get("content"), str):       # the old format's prompts
        txt = _user_text(rec["content"])
        if txt:
            out.append({"who": "you", "text": txt, "ts": ts})
    return out


def _parse(line):
    try:
        d = json.loads(line)
        return d if isinstance(d, dict) else None
    except ValueError:
        return None


# ------------------------------------------------------------------ reading files
def _lines_from(path, start, end=None):
    """(offset, line) for each complete line from byte `start` up to `end` (default: the end)."""
    with _open(path) as f:
        f.seek(start)
        off = start
        for line in f:
            if end is not None and off >= end:
                break
            if not line.endswith(b"\n"):
                break                       # a line still being written
            yield off, line
            off += len(line)


def _matching_lines(path, word, chunk=16 << 20):
    """(offset, line) for each complete line holding `word` as a whole word (in lower,
    Capitalised, UPPER or as-typed case), scanning the file in big chunks with bytes.find:
    only the lines around a hit are cut out and returned."""
    variants = {v.encode() for v in (word, word.lower(), word.capitalize(), word.upper())}
    with _open(path) as f:
        base, carry = 0, b""
        while True:
            data = f.read(chunk)
            if not data:
                break
            buf = carry + data
            cut = buf.rfind(b"\n") + 1           # complete lines only; the rest waits for the next chunk
            body, carry = buf[:cut], buf[cut:]
            starts = set()
            for v in variants:
                i = body.find(v)
                while i >= 0:
                    j = i + len(v)
                    if not (i and body[i - 1:i].isalnum()) and not body[j:j + 1].isalnum():
                        starts.add(body.rfind(b"\n", 0, i) + 1)
                    i = body.find(v, j)
            for a in sorted(starts):
                b = body.find(b"\n", a)
                yield base + a, body[a:b + 1]
            base += len(body)


def _tail(path, end=None, want=20, accept=None, cap=96 << 20):
    """The last `want` items (accept(offset, rec) -> [items]) before byte `end`, reading
    backwards in growing chunks: a 200 MB file costs only its tail."""
    size = os.path.getsize(path) if end is None else end
    chunk = 1 << 20
    while True:
        start = max(0, size - chunk)
        items = []
        with _open(path) as f:
            f.seek(start)
            data = f.read(size - start)
        pos = start
        lines = data.split(b"\n")
        if start > 0:                        # the first piece is a partial line
            pos += len(lines[0]) + 1
            lines = lines[1:]
        for i, line in enumerate(lines):
            if line.strip() and (i < len(lines) - 1):          # the last piece has no newline yet
                rec = _parse(line)
                if rec is not None:
                    items += [(pos, x) for x in accept(pos, rec)]
            pos += len(line) + 1
        if len(items) >= want or start == 0 or chunk >= cap:
            return items[-want:], (items[-want][0] if len(items) >= want else start)
        chunk *= 4


# ------------------------------------------------------------------ the index (in memory, incremental)
_INDEX = {}
_INDEX_LOCK = threading.Lock()


def _blank():
    return {"offset": 0, "cwd": None, "branch": None, "first_ts": None, "last_ts": None, "ai_title": None,
            "summary": None, "first_prompt": None, "last_prompt": None, "you": 0, "claude": 0, "tools": 0,
            "last": None, "last_tool": None, "last_reply": None, "slug": None}


def index(path):
    """What a transcript holds, kept per file and brought up to date from where it stopped."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    with _INDEX_LOCK:
        cur = _INDEX.get(path)
        if cur and cur["size"] == st.st_size and cur["mtime"] == st.st_mtime:
            return cur
        if not cur or st.st_size < cur["offset"]:
            cur = _blank()
        off = cur["offset"]
        for off_, line in _lines_from(path, off):
            off = off_ + len(line)
            rec = _parse(line)
            if not rec:
                continue
            t = rec.get("type")
            ts = rec.get("timestamp")
            if ts:
                cur["first_ts"] = cur["first_ts"] or ts
                cur["last_ts"] = ts
            if rec.get("cwd") and not cur["cwd"]:
                cur["cwd"] = rec["cwd"]
            if rec.get("gitBranch"):
                cur["branch"] = rec["gitBranch"]
            if t == "ai-title" and rec.get("aiTitle"):
                cur["ai_title"] = rec["aiTitle"]
            elif t == "summary" and rec.get("summary"):
                cur["summary"] = rec["summary"]
            elif t == "last-prompt" and isinstance(rec.get("lastPrompt"), str):
                cur["last_prompt"] = rec["lastPrompt"]
            for m in messages(rec, include_tools=True, result_lines=1):
                if m["who"] == "you":
                    cur["you"] += 1
                    cur["first_prompt"] = cur["first_prompt"] or m["text"]
                    cur["last"] = "you"
                elif m["who"] == "claude":
                    cur["claude"] += 1
                    cur["last"] = "claude_final" if m.get("final") else "claude"
                    cur["last_reply"] = m["text"][:400]
                elif m["who"] == "tool":
                    cur["tools"] += 1
                    cur["last"] = "tool"
                    cur["last_tool"] = m["text"]
                elif m["who"] == "result":
                    cur["last"] = "result"
        cur.update(offset=off, size=st.st_size, mtime=st.st_mtime)
        _INDEX[path] = cur
        return cur


# ------------------------------------------------------------------ finding sessions
def _projects():
    d = os.path.join(root(), "projects")
    try:
        return sorted(os.path.join(d, p) for p in os.listdir(d) if os.path.isdir(os.path.join(d, p)))
    except OSError:
        return []


def session_files():
    """Every top-level session transcript: [(path, session id, project dir)]."""
    out = []
    for p in _projects():
        for f in glob.glob(os.path.join(glob.escape(p), "*.jsonl")):
            sid = os.path.basename(f)[:-6]
            if re.fullmatch(UUID_RE, sid):
                out.append((f, sid, p))
    return out


def job_files(session_dir):
    """A session's background jobs (subagents): [(jsonl path, agent id)]."""
    d = os.path.join(session_dir, "subagents")
    return [(f, os.path.basename(f)[6:-6]) for f in glob.glob(os.path.join(glob.escape(d), "agent-*.jsonl"))]


def _meta(job_path):
    try:
        with _open(job_path[:-6] + ".meta.json") as f:
            return json.load(f)
    except (OSError, ValueError, PermissionError):
        return {}


_CWD = {}


def _decoded(project_dir, cwd=None):
    """A project folder's real path: the transcript's cwd when known (the folder name's
    encoding loses dashes and dots), else sessions-index.json's, else the first cwd in its
    newest transcript, else a guess from the name."""
    if cwd:
        return cwd.replace(HOME, "~")
    if not project_dir:
        return None
    if project_dir in _CWD:
        return _CWD[project_dir]
    found = None
    try:
        with _open(os.path.join(project_dir, "sessions-index.json")) as f:
            found = json.load(f).get("originalPath")
    except (OSError, ValueError, PermissionError, AttributeError):
        pass
    if not found:
        js = sorted(glob.glob(os.path.join(glob.escape(project_dir), "*.jsonl")), key=os.path.getmtime)
        for path in js[-1:]:
            for n, (_, line) in enumerate(_lines_from(path, 0)):
                rec = _parse(line) or {}
                if rec.get("cwd") or n > 60:
                    found = rec.get("cwd")
                    break
    name = os.path.basename(project_dir)
    out = (found or name.replace("-", "/")).replace(HOME, "~")
    _CWD[project_dir] = out
    return out


def _short_project(path):
    p = (path or "").rstrip("/")
    return os.path.basename(p) or p


def _topic(ix):
    t = ix.get("ai_title") or ix.get("summary") or ix.get("first_prompt") or ""
    t = re.sub(r"\s+", " ", t).strip()
    return _clip(t, 80)


def _job_status(path, meta=None):
    """running | done | stopped, and the job's last line, from its transcript's tail."""
    try:
        st = os.stat(path)
    except OSError:
        return "gone", None
    items, _ = _tail(path, want=1, accept=lambda off, rec: [rec] if rec.get("type") in ("assistant", "user") else [],
                     cap=8 << 20)
    last = items[-1][1] if items else None
    fresh = time.time() - st.st_mtime < RUNNING_S
    if last and last.get("type") == "assistant" and (last.get("message") or {}).get("stop_reason") in ("end_turn", "stop_sequence"):
        txt = next((b.get("text") for b in (last["message"].get("content") or []) if isinstance(b, dict) and b.get("type") == "text"), "")
        return "done", _clip(redact(txt or ""), 160)
    return ("running" if fresh else "stopped"), None


def jobs(session_dir, limit=6):
    """A session's background jobs, newest first: id, type, description, status.
    -> (jobs, total, how many wrote in the last RUNNING_S seconds)."""
    files = [(f, a, os.path.getmtime(f)) for f, a in job_files(session_dir)]
    files.sort(key=lambda x: -x[2])
    recent = sum(1 for x in files if time.time() - x[2] < RUNNING_S)
    files = [(f, a) for f, a, _ in files]
    out = []
    for path, aid in files[:limit]:
        meta = _meta(path)
        status, said = _job_status(path, meta)
        out.append({"job": aid, "type": meta.get("agentType"), "description": redact(_clip(meta.get("description") or "", 120)),
                    "status": status, "last_activity": _ago(os.path.getmtime(path)),
                    **({"result": said} if said and status == "done" else {})})
    return out, len(files), recent


# ------------------------------------------------------------------ live sessions
def _pid_alive(pid):
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            return b"claude" in f.read()
    except (OSError, ValueError):
        return False


def _registry():
    """Claude Code's own record of its running processes (sessions/<pid>.json): session id
    -> {pid, cwd, status (busy or idle), name}. The .key files beside them are never read."""
    out = {}
    for f in glob.glob(os.path.join(glob.escape(root()), "sessions", "*.json")):
        try:
            with _open(f) as fh:
                d = json.load(fh)
        except (OSError, ValueError, PermissionError):
            continue
        sid, pid = d.get("sessionId"), d.get("pid")
        if sid and pid and _pid_alive(pid):
            out[sid] = {"pid": pid, "cwd": d.get("cwd"), "status": d.get("status"), "name": d.get("name"),
                        "kind": d.get("kind"), "started": d.get("startedAt")}
    return out


def _herdr(*args):
    exe = herdr_bin()
    if not os.path.exists(exe):
        return None
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=6, stdin=subprocess.DEVNULL)
        d = json.loads(r.stdout or "{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return d.get("result", d) if isinstance(d, dict) else None


def _position(layout, pane_id):
    """Where a pane sits in its tab: left, right, top, bottom (or a combination), or whole."""
    panes = layout.get("panes") or []
    if len(panes) < 2:
        return "whole tab"
    area = layout.get("area") or {}
    me = next((p.get("rect") or {} for p in panes if p.get("pane_id") == pane_id), None)
    if not me or not area:
        return None
    where = []
    if me.get("width", 0) < area.get("width", 0):
        where.append("left" if me.get("x") == area.get("x") else "right" if
                     me.get("x", 0) + me.get("width", 0) >= area.get("x", 0) + area.get("width", 0) else "middle")
    if me.get("height", 0) < area.get("height", 0):
        where.insert(0, "top" if me.get("y") == area.get("y") else "bottom")
    return " ".join(where) or "whole tab"


def herdr_panes():
    """herdr's panes running Claude Code (read only: `herdr agent list` and `api snapshot`):
    session id -> {pane, workspace, tab, position, focused, status, title}."""
    snap = (_herdr("api", "snapshot") or {}).get("snapshot") or {}
    agents = snap.get("agents") or (_herdr("agent", "list") or {}).get("agents") or []
    ws = {w.get("workspace_id"): w.get("label") for w in snap.get("workspaces") or []}
    tabs = {t.get("tab_id"): t.get("label") for t in snap.get("tabs") or []}
    layouts = {lay.get("tab_id"): lay for lay in snap.get("layouts") or []}
    focused = snap.get("focused_pane_id")
    out = {}
    for a in agents:
        s = a.get("agent_session") or {}
        sid = s.get("value") if isinstance(s, dict) else None
        if not sid or a.get("agent") not in (None, "claude"):
            continue
        tab = a.get("tab_id")
        name = f"{ws.get(a.get('workspace_id')) or a.get('workspace_id')} · tab {tabs.get(tab) or tab}"
        out[sid] = {"pane": name, "pane_id": a.get("pane_id"),
                    "position": _position(layouts.get(tab) or {}, a.get("pane_id")),
                    "focused": a.get("pane_id") == focused if focused else bool(a.get("focused")),
                    "status": a.get("agent_status"),
                    "title": re.sub(r"^[^\w]+", "", a.get("terminal_title_stripped") or "").strip() or None}
    return out


def _hypr_focus_pid():
    try:
        r = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=3,
                           stdin=subprocess.DEVNULL)
        return int(json.loads(r.stdout or "{}").get("pid") or 0) or None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _ancestors(pid):
    seen = []
    while pid and pid > 1 and len(seen) < 40:
        seen.append(pid)
        try:
            with open(f"/proc/{pid}/stat") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return seen


def _running_tasks(project_dir, sid):
    """Background shell tasks of a session still writing output: [(id, last lines)]."""
    d = os.path.join(tasks_root(), os.path.basename(project_dir), sid, "tasks")
    out = []
    for f in sorted(glob.glob(os.path.join(glob.escape(d), "*.output")), key=lambda p: -os.path.getmtime(p)):
        if os.path.islink(f) or time.time() - os.path.getmtime(f) > RUNNING_S:
            continue
        out.append({"task": os.path.basename(f)[:-7], "latest": _last_lines(f, 2)})
        if len(out) >= 4:
            break
    return out


def _last_lines(path, n):
    try:
        with _open(path) as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 8192))
            data = f.read().decode("utf-8", "replace")
    except (OSError, PermissionError):
        return ""
    lines = [x for x in re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", data).replace("\r", "\n").split("\n") if x.strip()]
    return redact("\n".join(x[:200] for x in lines[-n:]))


def _find(sid):
    """The transcript of a session id (or a job's agent id): (path, project dir, parent id or None)."""
    for path, s, p in session_files():
        if s == sid:
            return path, p, None
    hits = glob.glob(os.path.join(glob.escape(root()), "projects", "*", "*", "subagents", f"agent-{glob.escape(sid)}.jsonl"))
    if hits:
        parent = os.path.dirname(os.path.dirname(hits[0]))
        return hits[0], os.path.dirname(parent), os.path.basename(parent)
    legacy = os.path.join(root(), "transcripts", sid + ".jsonl")
    if sid.startswith("ses_") and os.path.exists(legacy):
        return legacy, None, None
    return None, None, None


def live_sessions():
    """The sessions running now: Claude Code's process registry, herdr's panes, and any
    transcript written in the last few minutes. Each with its project, pane, topic, what
    it's doing and whether it's waiting on the operator."""
    reg = _registry()
    panes = herdr_panes()
    files = {s: (path, p) for path, s, p in session_files()}
    ids = set(reg) | set(panes)
    for s, (path, _) in files.items():
        try:
            if time.time() - os.path.getmtime(path) < LIVE_S:
                ids.add(s)
        except OSError:
            pass
    focus = _hypr_focus_pid()
    out = []
    for sid in ids:
        if sid not in files:
            continue
        path, pdir = files[sid]
        ix = index(path) or _blank()
        r, pane = reg.get(sid, {}), panes.get(sid, {})
        busy = r.get("status") == "busy" or pane.get("status") == "working"
        blocked = pane.get("status") == "blocked" or r.get("status") == "waiting"
        waiting = blocked or (not busy and ix.get("last") == "claude_final")
        if blocked:
            doing = "waiting for your answer or permission"
        elif waiting:
            doing = "waiting for you: " + _clip(redact(re.sub(r"\s+", " ", ix.get("last_reply") or "")), 140)
        elif busy and ix.get("last_tool") and ix.get("last") in ("tool", "result"):
            doing = "running " + _clip(redact(ix["last_tool"]), 140)
        elif busy:
            doing = "thinking" if ix.get("last") == "you" else "writing"
        else:
            doing = "idle"
        on_screen = bool(focus and r.get("pid") and focus in _ancestors(r["pid"]))
        mtime = os.path.getmtime(path)
        out.append({"session": sid, "project": _decoded(pdir, ix.get("cwd") or r.get("cwd")),
                    "topic": redact(_topic(ix)) or r.get("name"), "pane": pane.get("pane"),
                    "position": pane.get("position"), "focused": bool(pane.get("focused")) or on_screen,
                    "doing": doing, "waiting_on_you": waiting, "last_activity": _ago(mtime), "_mtime": mtime,
                    "_path": path, "_pdir": pdir, "running": bool(r) or bool(pane)})
    out.sort(key=lambda x: -x["_mtime"])
    return out


def _public(s):
    return {k: v for k, v in s.items() if not k.startswith("_") and v not in (None, "")}


def label(s):
    """A session in a few words: its project and topic ("bromigos-org: VECTOR reads Claude")."""
    topic = re.sub(r"\s+", " ", s.get("topic") or "").strip()
    topic = " ".join(topic.split()[:7])
    return f"{_short_project(s.get('project'))}" + (f": {topic}" if topic else "")


class Ambiguous(Exception):
    def __init__(self, choices):
        super().__init__("several sessions match")
        self.choices = choices

    def answer(self):
        names = [label(c) for c in self.choices[:4]]
        n = len(self.choices)
        word = {2: "two", 3: "three", 4: "four"}.get(n, str(n))
        ask = f"There are {word}: " + (", ".join(names[:-1]) + ", and " + names[-1] if n > 1 else names[0]) + ". Which one?"
        return {"ambiguous": True, "ask": ask,
                "choices": [{"session": c["session"], "label": label(c), **({"pane": c["pane"]} if c.get("pane") else {}),
                             **({"position": c["position"]} if c.get("position") else {})} for c in self.choices[:6]],
                "note": "Ask Sir which one in one short line (the ask above), then call again with session set to "
                        "the one he names (an id or his words)."}


_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "one", "session", "claude", "in", "on", "a", "an", "of", "that", "this", "pane", "tab", "code",
         "about", "with", "for", "working", "is", "s", "what", "doing", "my", "me"}


def resolve(session=None):
    """-> (path, info dict, chosen_because). Raises Ambiguous (choices) or LookupError."""
    s = (session or "").strip()
    sl = s.lower()
    if sl in ("", "current", "live", "latest", "this", "now", "active", "it", "newest"):
        live = live_sessions()
        if len(live) == 1:
            return live[0]["_path"], live[0], "the one live session"
        if live:
            focused = [x for x in live if x["focused"]]
            if len(focused) == 1:
                return focused[0]["_path"], focused[0], "the focused pane"
            a, b = live[0], live[1]
            if a["_mtime"] - b["_mtime"] > 300:
                return a["_path"], a, "the most recently active"
            raise Ambiguous(live)
        files = sorted(session_files(), key=lambda x: -os.path.getmtime(x[0]))
        if not files:
            raise LookupError("no Claude Code sessions on this machine")
        path, sid, pdir = files[0]
        return path, _info(path, sid, pdir), "the most recent (none is live)"
    m = re.fullmatch(r"(?:agent-)?(a[0-9a-f]{6,})", sl)
    if re.fullmatch(r"[0-9a-f]{6,}(?:-[0-9a-f-]*)?", sl) or m or sl.startswith("ses_"):
        hits = [x for x in session_files() if x[1].startswith(sl)]
        if len(hits) == 1:
            path, sid, pdir = hits[0]
            return path, _info(path, sid, pdir), "the session you named"
        if m and not hits:
            path, pdir, parent = _find(m.group(1)) if len(m.group(1)) >= 17 else (None, None, None)
            if not path:
                jhits = glob.glob(os.path.join(glob.escape(root()), "projects", "*", "*", "subagents",
                                               f"agent-{glob.escape(m.group(1))}*.jsonl"))
                if len(jhits) == 1:
                    path = jhits[0]
                    parent = os.path.basename(os.path.dirname(os.path.dirname(path)))
                    pdir = os.path.dirname(os.path.dirname(os.path.dirname(path)))
            if path:
                aid = os.path.basename(path)[6:-6]
                meta = _meta(path)
                return path, {"session": aid, "job_of": parent, "project": _decoded(pdir), "topic":
                              redact(meta.get("description") or ""), "type": meta.get("agentType")}, "the job you named"
        if sl.startswith("ses_"):
            path, _, _ = _find(s)
            if path:
                return path, {"session": s, "project": None, "topic": "an older transcript"}, "the session you named"
        if len(hits) > 1:
            raise Ambiguous([_info(*h) for h in hits[:6]])
    # a hint: words against live sessions first, then the 30 most recent
    words = [w for w in _WORD.findall(sl) if w not in _STOP]
    if not words:
        return resolve("")
    live = live_sessions()
    cands = live + [_info(p, sid, pd) for p, sid, pd in sorted(session_files(), key=lambda x: -os.path.getmtime(x[0]))[:30]
                    if sid not in {x["session"] for x in live}]
    scored = []
    for c in cands:
        hay = " ".join(str(c.get(k) or "") for k in ("project", "topic", "pane", "position", "doing")).lower()
        hay_words = set(_WORD.findall(hay))
        score = sum(2 if w in hay_words else 1 if w in hay else 0 for w in words)
        if score:
            scored.append((score + (0.5 if c in live else 0), c))
    if not scored:
        raise LookupError(f"no Claude Code session matches {s!r}")
    scored.sort(key=lambda x: (-x[0], -x[1].get("_mtime", 0)))
    top = [c for sc, c in scored if sc == scored[0][0]]
    if len(top) > 1:
        raise Ambiguous(top)
    return top[0]["_path"], top[0], f"it matches {s!r}"


def _info(path, sid, pdir):
    ix = index(path) or _blank()
    mtime = os.path.getmtime(path)
    return {"session": sid, "project": _decoded(pdir, ix.get("cwd")), "topic": redact(_topic(ix)),
            "last_activity": _ago(mtime), "_mtime": mtime, "_path": path, "_pdir": pdir}


# ------------------------------------------------------------------ the tools
def claude_sessions(project=None, live_only=False, session=None, limit=10, legacy=False):
    lim = max(1, min(int(limit or 10), 25))
    live = live_sessions()
    live_by = {x["session"]: x for x in live}
    if session:
        path, info, why = resolve(session)
        if info.get("job_of"):
            st, said = _job_status(path)
            return {"job": info["session"], "of_session": info["job_of"], "type": info.get("type"),
                    "description": info.get("topic"), "status": st, **({"result": said} if said else {}),
                    "_read": {"session": info["session"]}}
        ix = index(path) or _blank()
        js, total, recent = jobs(os.path.join(os.path.dirname(path), info["session"]), limit=20)
        lv = live_by.get(info["session"], {})
        return {**_public(_info(path, info["session"], info.get("_pdir") or os.path.dirname(path))),
                **{k: v for k, v in _public(lv).items() if k in ("pane", "position", "focused", "doing", "waiting_on_you")},
                "live": bool(lv), "started": _local(ix.get("first_ts")), "branch": ix.get("branch"),
                "messages": {"you": ix["you"], "claude": ix["claude"], "tool_calls": ix["tools"]},
                "jobs": js, "jobs_total": total, "_read": {"session": info["session"]}}
    rows = []
    files = sorted(session_files(), key=lambda x: -os.path.getmtime(x[0]))
    if project:
        p = project.lower()
        files = [f for f in files if p in f[2].lower() or p in ((index(f[0]) or {}).get("cwd") or "").lower()]
    if live_only:
        files = [f for f in files if f[1] in live_by]
    for path, sid, pdir in files[:lim]:
        ix = index(path) or _blank()
        lv = live_by.get(sid)
        js, total, recent = jobs(os.path.join(pdir, sid), limit=3 if len(rows) < 4 else 0)
        row = {"session": sid, "project": _decoded(pdir, ix.get("cwd")), "title": redact(_topic(ix)),
               "started": _local(ix.get("first_ts")), "last_activity": _ago(os.path.getmtime(path)),
               "messages": ix["you"] + ix["claude"], "live": bool(lv)}
        if lv:
            row.update({k: v for k, v in _public(lv).items() if k in ("pane", "position", "focused", "doing", "waiting_on_you")})
        if total:
            row["jobs"] = {"total": total, "running": recent,
                           **({"latest": [{k: j[k] for k in ("job", "description", "status")} for j in js]} if js else {})}
        rows.append(row)
    if legacy:
        for f in sorted(glob.glob(os.path.join(glob.escape(root()), "transcripts", "ses_*.jsonl")),
                        key=lambda p: -os.path.getmtime(p))[:5]:
            rows.append({"session": os.path.basename(f)[:-6], "legacy": True, "last_activity": _ago(os.path.getmtime(f))})
    out = {"sessions": rows, "live": [label(x) for x in live],
           "waiting_on_you": [label(x) for x in live if x["waiting_on_you"]],
           "note": "Cloud sessions (claude.ai/code) aren't on this machine and aren't listed.",
           "_read": {"listed": len(rows)}}
    return out


def _budget(msgs, from_end):
    """Messages within the answer's size budget: the newest kept (latest view) or the oldest
    (paging forward). -> (kept, how many were dropped)."""
    total, kept = 0, []
    seq = reversed(msgs) if from_end else msgs
    for m in seq:
        n = len(m["text"]) + 40
        if kept and total + n > OUT_BUDGET:
            break
        total += n
        kept.append(m)
    if from_end:
        kept.reverse()
    return kept, len(msgs) - len(kept)


def _cap(m):
    lim = {"claude": 1500, "you": 900, "result": 900, "tool": 220, "summary": 800, "recap": 600, "job": 400}.get(m["who"], 600)
    return redact(_clip(m["text"], lim))


def _shape(off, m):
    return {"at": off, "t": _local(m.get("ts")), "who": m["who"], "text": _cap(m), **({"error": True} if m.get("error") else {})}


def claude_read(session=None, since=None, before=None, limit=20, include_tools=False, latest=None, result_lines=8):
    lim = max(1, min(int(limit or 20), 60))
    path, info, why = resolve(session)
    size = os.path.getsize(path)
    sid = info["session"]

    def accept(off, rec):
        return [m for m in messages(rec, include_tools, int(result_lines or 8))]
    cursor = None
    if isinstance(since, str) and re.fullmatch(r"b?\d+", since.strip()):
        cursor = int(since.strip().lstrip("b"))
    elif isinstance(since, int):
        cursor = since
    if latest is None:
        latest = since in (None, "")
    out = {"session": sid, "project": info.get("project"), "topic": info.get("topic"), "chosen": why}
    if info.get("job_of"):
        out["job_of"] = info["job_of"]
    if latest:
        end = int(str(before).lstrip("b")) if before not in (None, "") else size
        items, first = _tail(path, end=end, want=lim, accept=accept)
        msgs = [_shape(off, m) for off, m in items]
        msgs, _ = _budget(msgs, from_end=True)
        older = msgs[0]["at"] if msgs else first
        out.update(messages=msgs, older=f"b{older}" if older > 0 else None,
                   range={"before": end, "from": msgs[0]["at"] if msgs else end, "count": len(msgs)})
        lv = next((x for x in live_sessions() if x["session"] == sid), None) if not info.get("job_of") else None
        if lv:
            pdir = lv["_pdir"]
            js, _, _ = jobs(os.path.join(pdir, sid), limit=8)
            out["now"] = {"doing": lv["doing"], "waiting_on_you": lv["waiting_on_you"], "pane": lv.get("pane"),
                          "jobs_running": [{k: j[k] for k in ("job", "description")} for j in js if j["status"] == "running"],
                          "tasks_running": _running_tasks(pdir, sid)}
        elif info.get("job_of"):
            st, _ = _job_status(path)
            out["status"] = st
    else:
        t_since = None if cursor is not None else _when(since)
        start = cursor or 0
        kept, total, next_off = [], 0, None
        for off, line in _lines_from(path, start):
            rec = _parse(line)
            if not rec:
                continue
            if t_since is not None:
                e = _epoch(rec.get("timestamp")) if rec.get("timestamp") else None
                if e is None or e < t_since:
                    continue
            ms = [_shape(off, m) for m in accept(off, rec)]
            if not ms:
                continue
            n = sum(len(m["text"]) + 40 for m in ms)
            if kept and (len(kept) + len(ms) > lim or total + n > OUT_BUDGET):
                next_off = off                   # the next page starts at this record
                break
            kept += ms
            total += n
        out.update(messages=kept, next=f"b{next_off}" if next_off and next_off < size else None,
                   range={"from": start, "to": next_off or size, "count": len(kept)})
    for m in out.get("messages", []):
        m.pop("at", None)
    out["_read"] = {"session": sid, "range": out.get("range")}
    return out


def _final_replies(path, n, end=None):
    """The last n final replies (Claude's text that ended a turn), each joined from its blocks."""
    def accept(off, rec):
        if rec.get("type") != "assistant":
            return []
        return [m for m in messages(rec) if m["who"] == "claude" and m.get("final")]
    items, _ = _tail(path, end=end, want=max(1, n) * 4, accept=accept)
    groups = []
    for off, m in items:
        if groups and groups[-1]["id"] == m.get("id") and m.get("id"):
            groups[-1]["text"] += "\n\n" + m["text"]
        else:
            groups.append({"id": m.get("id"), "text": m["text"], "ts": m.get("ts"), "at": off})
    return groups[-n:]


def claude_search(query, project=None, days=30, limit=10, include_tools=False):
    out, _ = _search(query, project, days, limit, include_tools)
    return out


def _search(query, project=None, days=30, limit=10, include_tools=False):
    """-> (the answer, the shown hits with where they are)."""
    q = (query or "").strip()
    if len(q) < 2:
        raise ValueError("query too short")
    phrase = q.startswith('"') and q.endswith('"') and len(q) > 2
    q = q.strip('"').strip()
    words = [w for w in re.findall(r"[\w'-]+", q.lower()) if w]
    if not words:
        raise ValueError("query needs a word")
    # the raw prefilter: the rarest-looking word, whole (JSON keeps words intact; \n and quotes are escaped)
    pre = max(words, key=len)
    pre_typed = next((w for w in re.findall(r"[\w'-]+", q) if w.lower() == pre), pre)
    if phrase:
        match = re.compile(r"(?i)" + r"\s+".join(re.escape(w) for w in q.split())).search
    else:
        rxs = [re.compile(r"(?i)\b" + re.escape(w) + r"\b") for w in words]
        match = lambda t: rxs[0].search(t) if all(r.search(t) for r in rxs) else None  # noqa: E731
    lim = max(1, min(int(limit or 10), 30))
    cutoff = time.time() - max(1, min(int(days or 30), 3650)) * 86400
    files = []
    for path, sid, pdir in session_files():
        files.append((path, sid, None, pdir))
        for jp, aid in job_files(os.path.join(pdir, sid)):
            files.append((jp, aid, sid, pdir))
    for f in glob.glob(os.path.join(glob.escape(root()), "transcripts", "ses_*.jsonl")):
        files.append((f, os.path.basename(f)[:-6], None, None))
    files = [f for f in files if os.path.getmtime(f[0]) >= cutoff]
    if project:
        p = project.lower()
        files = [f for f in files if f[3] and (p in f[3].lower() or p in (_decoded(f[3]) or "").lower())]
    files.sort(key=lambda f: -os.path.getmtime(f[0]))
    t0 = time.monotonic()
    hits, scanned, partial = [], 0, False
    for path, sid, parent, pdir in files:
        if time.monotonic() - t0 > SEARCH_BUDGET_S:
            partial = True
            break
        scanned += 1
        for off, line in _matching_lines(path, pre_typed):
            rec = _parse(line)
            if not rec:
                continue
            for m in messages(rec, include_tools, 40):
                if not match(m["text"]):
                    continue
                text = redact(m["text"])                    # redacted whole, before any slicing
                mm = match(text)
                if not mm:
                    continue
                a, b = max(0, mm.start() - 140), min(len(text), mm.end() + 160)
                snip = ("…" if a else "") + re.sub(r"\s+", " ", text[a:b]).strip() + ("…" if b < len(text) else "")
                hits.append({"session": parent or sid, **({"job": sid} if parent else {}),
                             "project": _decoded(pdir) if pdir else None, "t": _local(m.get("ts")),
                             "who": m["who"], "snippet": snip, "_e": _epoch(m.get("ts")) or 0, "_at": off,
                             "_path": path})
            if time.monotonic() - t0 > SEARCH_BUDGET_S:
                partial = True
                break
    hits.sort(key=lambda h: -h["_e"])
    projects = {}
    for h in hits:
        if h["project"]:
            projects[h["project"]] = projects.get(h["project"], 0) + 1
    shown = hits[:lim]
    out = {"query": query, "matches": len(hits), "results": [{k: v for k, v in h.items() if not k.startswith("_")} for h in shown],
           "files_scanned": scanned, "files_in_range": len(files)}
    if partial:
        out["partial"] = f"stopped after {SEARCH_BUDGET_S:.0f} s; narrow it with project= or days="
    out["_read"] = {"search": True, "matches": len(hits), "files": scanned}
    return out, shown


def claude_memory(project=None, file=None, query=None):
    rows = []
    for pdir in _projects():
        mem = os.path.join(pdir, "memory")
        if not os.path.isdir(mem):
            continue
        name = _decoded(pdir)
        if project and project.lower() not in (pdir + " " + (name or "")).lower():
            continue
        files = sorted(os.path.basename(f) for f in glob.glob(os.path.join(glob.escape(mem), "*.md")))
        rows.append((pdir, name, mem, files))
    if not rows:
        raise LookupError("no Claude Code memory" + (f" for {project!r}" if project else ""))
    if project:                                  # the project named exactly comes first
        rows.sort(key=lambda r: not (r[1] or "").rstrip("/").lower().endswith("/" + project.lower().strip("/")))

    def text(mem, fn):
        with _open(os.path.join(mem, fn)) as f:
            return f.read().decode("utf-8", "replace")
    if file:
        want = file if file.endswith(".md") else file + ".md"
        for pdir, name, mem, files in rows:
            if want in files:
                return {"project": name, "file": want, "text": redact(_clip(text(mem, want), OUT_BUDGET)),
                        "_read": {"memory": f"{os.path.basename(pdir)}/{want}"}}
        raise LookupError(f"no memory file {file!r}" + (f" in {project!r}" if project else ""))
    if query:
        rxs = [re.compile(r"(?i)\b" + re.escape(w)) for w in re.findall(r"[\w'-]+", query)]
        hits = []
        for pdir, name, mem, files in rows:
            for fn in files:
                for line in text(mem, fn).split("\n"):
                    if rxs and all(r.search(line) for r in rxs):
                        hits.append({"project": name, "file": fn, "line": redact(_clip(line.strip(), 300))})
        return {"query": query, "matches": len(hits), "results": hits[:20],
                "_read": {"memory_search": len(hits)}}
    if len(rows) > 1 and not project:
        return {"projects": [{"project": name, "files": len(files), "has_index": "MEMORY.md" in files}
                             for _, name, _, files in rows],
                "note": "Name a project (project=) for its index, a file (file=) to read one, or query= to search them all.",
                "_read": {"memory": "list"}}
    pdir, name, mem, files = rows[0]
    idx = text(mem, "MEMORY.md") if "MEMORY.md" in files else ""
    others = [f for f in files if f != "MEMORY.md"]
    return {"project": name, "index": redact(_clip(idx, OUT_BUDGET - 1200)),
            "files": others[:40] + ([f"… {len(others) - 40} more"] if len(others) > 40 else []),
            "_read": {"memory": f"{os.path.basename(pdir)}/MEMORY.md"}}


def claude_logs(kind="history", since=None, session=None, limit=30):
    lim = max(1, min(int(limit or 30), 100))
    t_since = _when(since)
    kind = (kind or "history").lower()
    if kind == "history":
        path = os.path.join(root(), "history.jsonl")
        if not os.path.exists(path):
            return {"history": [], "note": "no prompt history"}

        def accept(off, rec):
            if t_since and (rec.get("timestamp") or 0) / 1000 < t_since:
                return []
            if session and rec.get("sessionId", "").find(session) != 0:
                return []
            return [rec]
        items, _ = _tail(path, want=lim, accept=accept, cap=16 << 20)
        rows = [{"t": _local(r.get("timestamp")), "project": (r.get("project") or "").replace(HOME, "~"),
                 "prompt": redact(_clip(r.get("display") or "", 300)),
                 **({"pasted": len(r["pastedContents"])} if r.get("pastedContents") else {})} for _, r in items]
        return {"history": rows, "_read": {"history": len(rows)}}
    if kind == "debug":
        files = [f for f in glob.glob(os.path.join(glob.escape(root()), "debug", "*.txt")) if not os.path.islink(f)]
        if session:
            files = [f for f in files if os.path.basename(f).startswith(session)]
        if t_since:
            files = [f for f in files if os.path.getmtime(f) >= t_since]
        if not files:
            return {"debug": None, "note": "no debug logs (Claude Code writes them only when started with --debug)"}
        f = max(files, key=os.path.getmtime)
        return {"file": os.path.basename(f), "modified": _ago(os.path.getmtime(f)),
                "lines": _last_lines(f, lim).split("\n"), "_read": {"debug": os.path.basename(f)}}
    if kind == "tasks":
        out = []
        for f in glob.glob(os.path.join(glob.escape(tasks_root()), "*", "*", "tasks", "*.output")):
            if os.path.islink(f) or not _readable(f):
                continue                     # a link is a job's transcript: claude_read reads those
            m = os.path.getmtime(f)
            if t_since and m < t_since:
                continue
            sid = os.path.basename(os.path.dirname(os.path.dirname(f)))
            if session and not sid.startswith(session):
                continue
            out.append((m, f, sid))
        out.sort(key=lambda x: -x[0])
        rows = [{"task": os.path.basename(f)[:-7], "session": sid, "project": _decoded(os.path.dirname(os.path.dirname(os.path.dirname(f)))),
                 "status": "running" if time.time() - m < RUNNING_S else "finished", "modified": _ago(m),
                 "last_lines": _last_lines(f, 5)} for m, f, sid in out[:min(lim, 12)]]
        return {"tasks": rows, "_read": {"tasks": len(rows)}}
    raise ValueError("kind: history, debug or tasks")


# ------------------------------------------------------------------ read aloud
SPEAKER = None           # the desktop daemon sets this: callable(text, label) -> spoken (bool)
MAX_SPOKEN = 3000


def speech(text):
    from .text import read_aloud
    t = read_aloud(redact(text))
    if len(t) > MAX_SPOKEN:
        cut = t[:MAX_SPOKEN]
        cut = cut[:max(cut.rfind(". "), 200) + 1]
        t = cut + " That's the first part; the rest is longer."
    return t


def _say(text, who):
    if SPEAKER is None:
        return False
    try:
        return bool(SPEAKER(text, who))
    except Exception:
        return False


def claude_say_latest(session=None, n=1, about=None):
    n = max(1, min(int(n or 1), 5))
    if about:
        _, hits = _search(about, days=30, limit=30)
        hits = [h for h in hits if h["who"] == "claude"]
        if session:
            path, info, _ = resolve(session)
            hits = [h for h in hits if h["_path"] == path]
        if not hits:
            return {"found": False, "note": f"Claude hasn't said anything about {about!r} in the last 30 days that I can find."}
        h = hits[0]
        reply = None
        for off, line in _lines_from(h["_path"], h["_at"]):
            rec = _parse(line)
            reply = " ".join(m["text"] for m in messages(rec or {}) if m["who"] == "claude")
            break
        replies = [{"text": reply or h["snippet"], "ts": None}]
        who = h["project"] or "a session"
        sid = h["session"]
        rng = {"at": h["_at"]}
    else:
        path, info, why = resolve(session)
        replies = _final_replies(path, n)
        if not replies:
            return {"found": False, "session": info["session"], "note": "No finished reply from Claude in that session yet."}
        who = label(info)
        sid = info["session"]
        rng = {"replies": len(replies), "from": replies[0]["at"]}
    text = " ".join(speech(r["text"]) for r in replies)
    spoken = _say(text, who)
    out = {"session": sid, "from": who, "spoken": spoken, "chars": len(text), "_read": {"session": sid, **rng}}
    if spoken:
        out["note"] = ("Already read aloud in your voice. Don't repeat it; at most one short line, such as which "
                       "session it came from.")
    else:
        out["text"] = text
        out["note"] = "Read this out as it is (it's already speech-friendly), in your own voice."
    return out


# ------------------------------------------------------------------ follow mode
class Follow:
    """Watches the chosen session's transcript (or every live one), polling its size every
    1.5 s, and reads each new final reply aloud once its turn has ended: Claude's text
    that closed the turn, never tool chatter or a half-written answer."""
    POLL_S = 1.5
    SETTLE_S = 2.5          # a final reply with no turn marker after it: spoken this long after its last block
    IDLE_OFF_S = 15 * 60    # one session that stops being live: off after this

    def __init__(self):
        self.on = False
        self.target = None          # a session id, or "all"
        self.labels = {}            # path -> label
        self.offsets = {}           # path -> byte offset read so far
        self.pending = {}           # path -> {"id", "text", "since"}
        self.thread = None
        self.on_change = None
        self.spoken = 0
        self.last_seen = time.time()
        self.lock = threading.Lock()

    def state(self):
        names = sorted(set(self.labels.values()))
        return {"on": self.on, "target": self.target, "sessions": names[:4], "spoken": self.spoken}

    def _changed(self):
        if self.on_change:
            try:
                self.on_change(self.state())
            except Exception:
                pass

    def _watch(self, path, lbl):
        if path not in self.offsets:
            self.offsets[path] = os.path.getsize(path)
        self.labels[path] = lbl

    def start(self, session=None):
        with self.lock:
            if (session or "").strip().lower() in ("all", "every", "everything", "all sessions"):
                live = live_sessions()
                self.target = "all"
                self.offsets, self.labels, self.pending = {}, {}, {}
                for s in live:
                    self._watch(s["_path"], label(s))
            else:
                path, info, why = resolve(session)
                self.target = info["session"]
                self.offsets, self.labels, self.pending = {}, {}, {}
                self._watch(path, label(info))
            self.last_seen = time.time()
            self.spoken = 0
            if not self.on:
                self.on = True
                self.thread = threading.Thread(target=self._loop, daemon=True, name="claude-follow")
                self.thread.start()
        self._changed()
        return self.state()

    def stop(self):
        self.on = False
        self.pending = {}
        self._changed()
        return {"on": False}

    def _loop(self):
        tick = 0
        while self.on:
            try:
                self.poll()
                tick += 1
                if tick % 7 == 0:
                    self._refresh()
            except Exception:
                pass
            time.sleep(self.POLL_S)

    def _refresh(self):
        """Every ~10 s: in "all" mode new live sessions join; a single session that isn't
        live any more for a while switches follow mode off."""
        live = live_sessions()
        if self.target == "all":
            for s in live:
                if s["_path"] not in self.offsets:
                    self._watch(s["_path"], label(s))
            if live:
                self.last_seen = time.time()
        elif any(s["session"] == self.target for s in live):
            self.last_seen = time.time()
        if time.time() - self.last_seen > self.IDLE_OFF_S:
            self.stop()

    def poll(self):
        """One look at each followed transcript: new records read, finished replies spoken."""
        for path in list(self.offsets):
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            off = self.offsets[path]
            if size < off:
                off = 0
            if size > off:
                for o, line in _lines_from(path, off):
                    off = o + len(line)
                    rec = _parse(line)
                    if rec:
                        self._record(path, rec)
                self.offsets[path] = off
            p = self.pending.get(path)
            if p and time.monotonic() - p["since"] > self.SETTLE_S:
                self._flush(path)

    def _record(self, path, rec):
        t = rec.get("type")
        if t == "assistant":
            msg = rec.get("message") or {}
            if msg.get("stop_reason") in ("end_turn", "stop_sequence"):
                for m in messages(rec):
                    if m["who"] != "claude":
                        continue
                    p = self.pending.get(path)
                    if p and p["id"] == m.get("id"):
                        p["text"] += "\n\n" + m["text"]
                        p["since"] = time.monotonic()
                    else:
                        if p:
                            self._flush(path)
                        self.pending[path] = {"id": m.get("id"), "text": m["text"], "since": time.monotonic()}
            elif self.pending.get(path):
                self.pending.pop(path, None)       # the turn went on: that text wasn't the last word
        elif t == "system" and rec.get("subtype") == "turn_duration":
            self._flush(path)
        elif t == "user" and not rec.get("isMeta") and self.pending.get(path):
            self._flush(path)

    def _flush(self, path):
        p = self.pending.pop(path, None)
        if not p or not p["text"].strip():
            return
        text = speech(p["text"])
        many = self.target == "all" and len(self.offsets) > 1
        lbl = self.labels.get(path, "Claude")
        if many:
            text = f"From {_short_project(lbl.split(':')[0])}: " + text
        self.spoken += 1
        _say(text, lbl)
        try:
            from .tools import _audit
            _audit("claude_follow.say", {"session": os.path.basename(path)[:-6]}, True, 0, len(text))
        except Exception:
            pass
        self._changed()


FOLLOW = Follow()


def claude_follow(mode="on", session=None):
    m = (mode or "on").lower()
    if m in ("on", "start"):
        if SPEAKER is None:
            raise RuntimeError("follow mode reads aloud on the desktop; it isn't available here")
        st = FOLLOW.start(session)
        return {**st, "note": "Each new final reply is read aloud as its turn ends. A ◉ READING CLAUDE indicator "
                              "shows while it's on; 'stop reading Claude' turns it off.",
                "_read": {"follow": st["target"]}}
    if m in ("off", "stop"):
        return FOLLOW.stop()
    return FOLLOW.state()


# ------------------------------------------------------------------ every tool: ambiguity is an answer, not an error
def _asks(fn):
    import functools

    @functools.wraps(fn)
    def run(*a, **kw):
        try:
            return fn(*a, **kw)
        except Ambiguous as e:
            return e.answer()
    return run


claude_sessions = _asks(claude_sessions)
claude_read = _asks(claude_read)
claude_say_latest = _asks(claude_say_latest)
claude_follow = _asks(claude_follow)
