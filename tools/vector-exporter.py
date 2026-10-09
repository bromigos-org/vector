#!/usr/bin/env python3
"""VECTOR's health exporter: Prometheus metrics from his logs and event feed, for the
homelab Prometheus (job `vector-workstation`) and the Grafana board "VECTOR health".

    ~/.local/share/bromigos/venv-exporter/bin/python tools/vector-exporter.py [--port 9478]
    (bromigos-vector-exporter.service; prometheus_client in its own small venv)

Sources (all local, read only; nothing here ever reads memory text, commands or secrets):
  ~/.local/state/bromigos/vector-chat.log      replies with their stats (first token, total,
                                               recall, model, lane), reroutes, errors
  $XDG_RUNTIME_DIR/bromigos-vector-events.jsonl tool calls, failures and refusals; memory ops;
                                               voice.stt and voice.first_audio timings
  ~/.local/state/bromigos/nolgia-spend.jsonl   nolgia credits spent
  ~/.local/state/bromigos/evals/latest.json    the newest eval run
  nvidia-smi, /proc                            VRAM, voice server and daemon up/down
The chat log is read from its start, so counters are all-time and survive a restart of the
exporter; the event feed lives on tmpfs and starts over at boot (Prometheus handles that).
Evals never appear in the live series: they write their own chat log and event feed.
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import threading
import time

from prometheus_client import Counter, Histogram, start_http_server
from prometheus_client.core import REGISTRY, GaugeMetricFamily

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".local/state/bromigos")
RUN = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
CHAT = os.path.join(STATE, "vector-chat.log")
FEED = os.path.join(RUN, "bromigos-vector-events.jsonl")
SPEND = os.path.join(STATE, "nolgia-spend.jsonl")
EVALS = os.path.join(STATE, "evals")
HOLO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICE_SOCK = os.path.join(RUN, "bromigos-holo-voice.sock")
HOLO_SOCK = os.path.join(RUN, "bromigos-holo.sock")

BUCKETS = (0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 2.5, 3, 4, 5, 7, 9, 12, 15, 20, 30, 45, 60, 120)
STAGE = Histogram("vector_stage_seconds", "VECTOR's latency by stage: stt (speech to text), recall (memory "
                  "recall before the turn), first_token (turn start to the first word), first_audio (a "
                  "sentence's speech to its first audio), reply (the whole turn)", ["stage"], buckets=BUCKETS)
REPLIES = Counter("vector_replies_total", "Replies VECTOR finished, by model and lane", ["model", "lane"])
FALLBACK = Counter("vector_fallback_replies_total", "Replies answered by a model other than the lane's first", ["model"])
REROUTES = Counter("vector_reroutes_total", "A model gave up mid-turn (no first byte in time, silence, an error) and "
                   "the turn moved on", ["model", "why"])
ERRORS = Counter("vector_errors_total", "Turns that ended in an error (every model failed, or something broke)", ["kind"])
INTERRUPTED = Counter("vector_interrupted_total", "Turns cut short by a barge-in or STOP")
TOOLS = Counter("vector_tool_calls_total", "Tool calls, by tool", ["tool"])
TOOL_FAIL = Counter("vector_tool_failures_total", "Tool calls that failed (not counting refusals)", ["tool"])
REFUSALS = Counter("vector_refusals_total", "Requests his code refused (secrets, privilege, real money, RBAC)", ["tool"])
MEMORY = Counter("vector_memory_ops_total", "Long-term memory operations (recall, file, forget)", ["op", "source"])
SKILLS = Counter("vector_skill_loads_total", "Skills attached to a turn", ["skill"])
LINES = Counter("vector_exporter_lines_total", "Log lines the exporter has read", ["source"])

_REFUSED = re.compile(r"^(refused=|PermissionError|Refused)")


def lanes():
    try:
        with open(os.path.join(HOLO, "voice.json")) as f:
            c = json.load(f).get("lanes", {})
    except (OSError, ValueError):
        c = {}
    return (c.get("voice") or ["hive"])[0], (c.get("deep") or ["hive"])[0]


class Tail:
    """Follow a JSONL file across appends and rotation (to PATH.1)."""

    def __init__(self, path, handle, rotated=None):
        self.path, self.handle, self.rotated = path, handle, rotated
        self.ino, self.pos, self.buf = None, 0, b""
        if rotated and os.path.exists(rotated):          # what the feed held before its last rotation
            self._read(rotated, 0)

    def _read(self, path, pos):
        try:
            with open(path, "rb") as f:
                f.seek(pos)
                data = f.read()
                end = f.tell()
        except OSError:
            return pos
        data = self.buf + data
        lines = data.split(b"\n")
        self.buf = lines.pop()
        for ln in lines:
            if ln.strip():
                try:
                    self.handle(json.loads(ln))
                except (ValueError, TypeError, KeyError, AttributeError):
                    pass
        return end

    def poll(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return False
        if self.ino is not None and (st.st_ino != self.ino or st.st_size < self.pos):
            if self.rotated and os.path.exists(self.rotated) and os.stat(self.rotated).st_ino == self.ino:
                self._read(self.rotated, self.pos)          # the rest of the old file
            self.pos, self.buf = 0, b""
        self.ino = st.st_ino
        self.pos = self._read(self.path, self.pos)
        return True


def on_chat(r):
    LINES.labels("chat").inc()
    role = r.get("role")
    if role in ("vector", "pilot"):
        s = r.get("stats") or {}
        model, lane = s.get("model") or "unknown", s.get("lane") or "voice"
        REPLIES.labels(model, lane).inc()
        first = FIRST[1] if lane == "deep" else FIRST[0]
        if model != "unknown" and model != first:
            FALLBACK.labels(model).inc()
        if s.get("first_token_s") is not None:
            STAGE.labels("first_token").observe(float(s["first_token_s"]))
        if s.get("total_s") is not None:
            STAGE.labels("reply").observe(float(s["total_s"]))
        if s.get("recall_ms") is not None:
            STAGE.labels("recall").observe(float(s["recall_ms"]) / 1000)
    elif role == "reroute":
        why = (r.get("why") or "").lower()
        why = "timeout" if "time" in why else "silent" if "silent" in why else "cooling" if "cool" in why else "error"
        REROUTES.labels(r.get("model") or "unknown", why).inc()
    elif role == "error":
        t = (r.get("text") or "")
        ERRORS.labels("all_models" if re.search(r"Fallback|ModelAPIError|ExceptionGroup|quiet", t) else "other").inc()
    elif role == "interrupted":
        INTERRUPTED.inc()


def on_event(e):
    LINES.labels("feed").inc()
    t = e.get("type")
    if t == "tool.end":
        name = e.get("name") or "unknown"
        TOOLS.labels(name).inc()
        if _REFUSED.match(e.get("outcome") or ""):
            REFUSALS.labels(name).inc()
        elif not e.get("ok", True) or (e.get("outcome") or "").startswith("error="):
            TOOL_FAIL.labels(name).inc()
    elif t == "memory.recall":
        MEMORY.labels("recall", e.get("source") or "none").inc()
    elif t == "memory.file":
        MEMORY.labels("file", "gnosis").inc()
    elif t == "memory.forget":
        MEMORY.labels("forget", "gnosis").inc()
    elif t == "skill.load":
        SKILLS.labels(str(e.get("name") or "unknown")).inc()
    elif t == "voice.stt" and e.get("ms") is not None:
        STAGE.labels("stt").observe(float(e["ms"]) / 1000)
    elif t == "voice.first_audio" and e.get("ms") is not None:
        STAGE.labels("first_audio").observe(float(e["ms"]) / 1000)


def _procs():
    """-> {"voice": pid, "daemon": pid} for VECTOR's processes (cmdline match in /proc)."""
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if "voice_server.py" in cmd or "holo.voice_server" in cmd:
            out["voice"] = int(d)
        elif "-m holo.app" in cmd:
            out["daemon"] = int(d)
    return out


def _gpu(pids):
    g = {}
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
        used, total = [float(x) for x in out[0].split(",")]
        g["used"], g["total"] = used * 2**20, total * 2**20
        apps = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
                              capture_output=True, text=True, timeout=5).stdout.strip().splitlines()
        per = {}
        for ln in apps:
            p, m = [x.strip() for x in ln.split(",")]
            if p.isdigit():
                per[int(p)] = float(m) * 2**20
        g["voice"] = per.get(pids.get("voice"), 0.0)
    except Exception:
        pass
    return g


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Gauges:
    """Values read fresh at each scrape."""

    def collect(self):
        pids = _procs()
        up = GaugeMetricFamily("vector_process_up", "VECTOR's processes: daemon (the desktop app) and voice "
                               "(the speech server, spawned on demand and exiting when idle)", labels=["process"])
        up.add_metric(["daemon"], 1 if "daemon" in pids else 0)
        up.add_metric(["voice"], 1 if "voice" in pids else 0)
        yield up
        g = _gpu(pids)
        if g:
            v = GaugeMetricFamily("vector_gpu_vram_bytes", "Workstation GPU memory: used and total, and what "
                                  "the voice server holds", labels=["kind"])
            v.add_metric(["used"], g.get("used", 0))
            v.add_metric(["total"], g.get("total", 0))
            v.add_metric(["voice_server"], g.get("voice", 0))
            yield v
        yield from self._nolgia()
        yield from self._evals()

    def _nolgia(self):
        total = jobs = today = 0.0
        day = dt.date.today().isoformat()
        try:
            with open(SPEND) as f:
                for ln in f:
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    c = float(r.get("credits") or 0)
                    total, jobs = total + c, jobs + 1
                    if str(r.get("t", "")).startswith(day):
                        today += c
        except OSError:
            pass
        cap = (_load_json(os.path.join(HOLO, "nolgia.json")) or {}).get("daily_credits")
        m = GaugeMetricFamily("vector_nolgia_credits", "nolgia credits VECTOR spent: all time, today, and the daily cap",
                              labels=["window"])
        m.add_metric(["all_time"], total)
        m.add_metric(["today"], today)
        if cap is not None:
            m.add_metric(["daily_cap"], float(cap))
        yield m
        yield GaugeMetricFamily("vector_nolgia_jobs", "nolgia jobs VECTOR ran, all time", value=jobs)

    def _evals(self):
        d = _load_json(os.path.join(EVALS, "latest.json"))
        if not d:
            return
        s = d.get("summary") or {}
        if s.get("pass_rate") is not None:
            yield GaugeMetricFamily("vector_eval_pass_percent", "Eval pass rate of the newest full run", value=s["pass_rate"])
        prev = (d.get("diff") or {}).get("pass_rate_change")
        if prev is not None and s.get("pass_rate") is not None:
            yield GaugeMetricFamily("vector_eval_previous_pass_percent", "Eval pass rate of the run before the newest",
                                    value=s["pass_rate"] - prev)
        c = GaugeMetricFamily("vector_eval_category_pass_percent", "Eval pass rate by category", labels=["category"])
        for k, v in (s.get("by_category") or {}).items():
            if v.get("rate") is not None:
                c.add_metric([k], v["rate"])
        yield c
        n = GaugeMetricFamily("vector_eval_tasks", "Eval tasks in the newest run, by result", labels=["status"])
        for k in ("passed", "failed", "skipped"):
            n.add_metric([k], s.get(k) or 0)
        yield n
        lat = GaugeMetricFamily("vector_eval_latency_seconds", "Eval latency over every turn", labels=["stage", "quantile"])
        for stage, key in (("first_token", "first_token_s"), ("reply", "total_s")):
            for q in ("p50", "p90", "max"):
                x = ((s.get("latency") or {}).get(key) or {}).get(q)
                if x is not None:
                    lat.add_metric([stage, q], x)
        yield lat
        t = GaugeMetricFamily("vector_eval_task_passed", "1 when a task passed in the newest run, 0 when it failed",
                              labels=["task", "category"])
        for r in d.get("tasks") or []:
            if r.get("status") in ("pass", "fail", "error"):
                t.add_metric([r["id"], r["category"]], 1 if r["status"] == "pass" else 0)
        yield t
        try:
            ts = dt.datetime.fromisoformat(d["finished"]).timestamp()
            yield GaugeMetricFamily("vector_eval_last_run_timestamp_seconds", "When the newest eval run finished", value=ts)
        except (KeyError, ValueError):
            pass
        yield GaugeMetricFamily("vector_eval_duration_seconds", "How long the newest eval run took",
                                value=d.get("duration_s") or 0)
        yield GaugeMetricFamily("vector_eval_tool_calls", "Tool calls in the newest eval run", value=s.get("tool_calls") or 0)


FIRST = lanes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9478)
    ap.add_argument("--addr", default="0.0.0.0")
    a = ap.parse_args()
    tails = [Tail(CHAT, on_chat), Tail(FEED, on_event, rotated=FEED + ".1")]
    for t in tails:
        t.poll()
    REGISTRY.register(Gauges())
    start_http_server(a.port, addr=a.addr)
    print(f"vector-exporter: :{a.port}/metrics", flush=True)

    while True:
        time.sleep(2)
        for t in tails:
            t.poll()


if __name__ == "__main__":
    main()
