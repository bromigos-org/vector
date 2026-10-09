"""VECTOR's event feed for the desktop's holograms: JSON lines appended to
$XDG_RUNTIME_DIR/bromigos-vector-events.jsonl (tmpfs; rotated to .1 at about 5 MB).

Every line: {"v": 1, "t": ISO-8601 local with ms, "ts": epoch seconds, "type": ..., ...}
Types and fields (schema agreed in holo/README.md, "Event feed"):
  memory.recall   space, ids[], n, ms, source (mirror|gnosis|none)
  memory.file     space, category
  memory.forget   space, n
  tool.start      id, name, args (short summary, never secrets or memory text)
  tool.end        id, name, ok, ms, outcome (one short line)
  task.progress   task, title, step, note, state (running|done|failed|stopped), pct?
  git.push        repo, branch, sha, ok
  ci.result       repo, sha, workflow, status, conclusion, url
  argo.sync       app, action (sync|refresh|wait), sync, health, revision
  k8s.action      action (restart|scale|delete_pod|run_job), namespace, kind, name, replicas?, ok
  vault.op        op (list|put|copy), path, key?  (never a value)
Readers tail the file and must ignore unknown types and fields.
"""
import datetime as dt
import itertools
import json
import os
import threading
import time

PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "bromigos-vector-events.jsonl")
MAX = 5 * 1024 * 1024
_lock = threading.Lock()
_ids = itertools.count(1)


def next_id():
    return f"{os.getpid()}-{next(_ids)}"


def emit(type_, **fields):
    rec = {"v": 1, "t": dt.datetime.now().astimezone().isoformat(timespec="milliseconds"), "ts": round(time.time(), 3),
           "type": type_, **fields}
    line = json.dumps(rec, default=str, separators=(",", ":")) + "\n"
    with _lock:
        try:
            if os.path.exists(PATH) and os.path.getsize(PATH) > MAX:
                os.replace(PATH, PATH + ".1")
            with open(PATH, "a") as f:
                f.write(line)
        except OSError:
            pass


def summary(args, limit=120):
    """A short, safe one-line summary of tool arguments."""
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        s = v if isinstance(v, str) else json.dumps(v, default=str)
        parts.append(f"{k}={s[:60]}")
    out = " ".join(parts)
    return out if len(out) <= limit else out[:limit - 1] + "…"
