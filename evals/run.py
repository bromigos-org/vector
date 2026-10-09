"""Run VECTOR's evals: `bromigos-holo evals [--only CAT,...] [--task ID,...] [--list]`.

On demand, or from a timer of your own (the package doesn't schedule it). Writes
  ~/.local/state/bromigos/evals/<date>.json   every task: pass/fail, detail, replies,
                                              latency (first token, total), tool calls
  ~/.local/state/bromigos/evals/<date>.md     a short summary with the diff against the
                                              previous run ("got worse at X")
  ~/.local/state/bromigos/evals/latest.json   -> the newest run (read by the health exporter)
A second run on the same day keeps the earlier one as <date>.<HHMMSS>.json.

Cost guard: before anything runs, every LiteLLM route the brain's lanes (and the memory
embeddings) can use must be a local homelab vLLM with zero cost, or the run stops. nolgia
is never called (its tools are stubbed), and nothing here talks to a paid API.

Real repos are out of reach: the suite runs inside a bubblewrap jail where ~/github.com is a
throwaway overlay (writes land in memory and vanish when the run ends) and ~/.dotfiles is
read-only, and git pushes point at a dead URL. So no task can change, commit or push a real
repo, whatever command slips past the harness's write patterns; GitOps tasks edit the overlay. The parent
process snapshots every real repo's uncommitted paths before the run and checks again
after; a path the run made dirty fails the run (exit 4) and is listed in the results.
"""
import argparse
import datetime as dt
import fcntl
import glob
import hashlib
import json
import os
import re
import shutil
import ssl
import statistics
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.request

HOLO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HOLO not in sys.path:
    sys.path.insert(0, HOLO)

STATE = os.path.join(os.path.expanduser("~"), ".local/state/bromigos/evals")
CACHE = os.path.join(os.path.expanduser("~"), ".cache/bromigos/vector-evals")
KEY = os.path.join(os.path.expanduser("~"), ".local/share/bromigos/litellm-key")
CA = os.path.join(os.path.expanduser("~"), ".config/homelab/homelab-ca.crt")
HOME = os.path.expanduser("~")
REAL_ROOTS = [os.path.join(HOME, "github.com"), os.path.join(HOME, ".dotfiles")]   # never written by a run
JAILED = "BROMIGOS_EVAL_JAILED"


def _local_base():
    """A route is local when it is in-cluster, private-range, or in the gateway's own LAN domain."""
    from .checks import endpoint
    host = urllib.parse.urlparse(endpoint("litellm")).hostname or "localhost"
    domain = re.escape(host.split(".", 1)[1]) if "." in host else re.escape(host)
    return re.compile(r"^https?://([\w.-]+\.svc\.cluster\.local|[\w.-]+\." + domain + r"|10\.\d+\.\d+\.\d+|"
                      r"172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|192\.168\.\d+\.\d+|127\.0\.0\.1|localhost)(:\d+)?(/|$)")


class Ctx:
    def __init__(self, harness, scratch):
        self.harness, self.scratch = harness, scratch
        self.vars, self.data = {}, {}
        self._secret = None

    def secret(self):
        """The LiteLLM key, held in this process only to prove it never leaks (never printed or saved)."""
        if self._secret is None:
            try:
                with open(KEY) as f:
                    self._secret = f.read().strip()
            except OSError:
                self._secret = ""
        return self._secret


# ------------------------------------------------------------------ cost guard
def cost_guard():
    from holo.vector import brain_pai, llm
    voice, deep = brain_pai.lanes()
    need = set(voice) | set(deep) | {llm.embed_model()}
    with open(KEY) as f:
        key = f.read().strip()
    ctx = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()
    from .checks import endpoint
    local = _local_base()
    req = urllib.request.Request(endpoint("litellm") + "/model/info", headers={"Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        data = json.load(r)["data"]
    routes, bad = {}, []
    for m in data:
        if m["model_name"] not in need:
            continue
        p, i = m.get("litellm_params") or {}, m.get("model_info") or {}
        base = p.get("api_base") or ""
        cost = (i.get("input_cost_per_token") or 0) + (i.get("output_cost_per_token") or 0)
        routes.setdefault(m["model_name"], []).append(base)
        if not local.match(base) or cost:
            bad.append(f"{m['model_name']} -> {base or p.get('model')} (cost {cost})")
    missing = sorted(need - set(routes))
    if missing:
        bad.append(f"no route listed for {missing}")
    return {"ok": not bad, "models": sorted(need), "routes": routes, "problems": bad}


# ------------------------------------------------------------------ helpers
def _pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 2)


def _lat(xs):
    xs = [x for x in xs if x is not None]
    return {"p50": _pct(xs, 0.5), "p90": _pct(xs, 0.9), "max": round(max(xs), 2) if xs else None,
            "mean": round(statistics.mean(xs), 2) if xs else None, "n": len(xs)}


def _scrub(obj, secrets):
    from holo.vector import shell
    if isinstance(obj, str):
        for s in secrets:
            if s:
                obj = obj.replace(s, "[REDACTED]")
        return shell.redact(obj)
    if isinstance(obj, list):
        return [_scrub(x, secrets) for x in obj]
    if isinstance(obj, dict):
        return {k: _scrub(v, secrets) for k, v in obj.items()}
    return obj


def _short_args(a):
    s = json.dumps(a, default=str)
    return s if len(s) <= 200 else s[:199] + "…"


def previous_run(exclude):
    files = [f for f in glob.glob(os.path.join(STATE, "*.json")) if os.path.basename(f) != "latest.json"
             and os.path.realpath(f) != os.path.realpath(exclude)]
    files.sort(key=os.path.getmtime)
    for f in reversed(files):
        try:
            with open(f) as fh:
                d = json.load(fh)
            if d.get("partial"):
                continue
            return f, d
        except (OSError, ValueError):
            continue
    return None, None


def diff(cur, prev):
    if not prev:
        return {"previous": None}
    pt = {t["id"]: t["status"] for t in prev["tasks"]}
    worse = [t["id"] for t in cur["tasks"] if t["status"] == "fail" and pt.get(t["id"]) == "pass"]
    better = [t["id"] for t in cur["tasks"] if t["status"] == "pass" and pt.get(t["id"]) == "fail"]
    cats = {}
    for c, v in cur["summary"]["by_category"].items():
        pv = prev["summary"]["by_category"].get(c)
        if pv and v["rate"] is not None and pv["rate"] is not None:
            cats[c] = round(v["rate"] - pv["rate"], 1)
    lp, lc = prev["summary"]["latency"]["first_token_s"]["p50"], cur["summary"]["latency"]["first_token_s"]["p50"]
    return {"previous": prev["started"], "pass_rate_change": round(cur["summary"]["pass_rate"] - prev["summary"]["pass_rate"], 1)
            if prev["summary"].get("pass_rate") is not None and cur["summary"]["pass_rate"] is not None else None,
            "worse": worse, "better": better, "category_change": cats,
            "first_token_p50_change_s": round(lc - lp, 2) if lc is not None and lp is not None else None}


# ------------------------------------------------------------------ real repos stay untouched
def real_repos():
    repos = [r for r in [os.path.join(HOME, ".dotfiles")] if os.path.exists(os.path.join(r, ".git"))]
    return repos + sorted(os.path.dirname(g) for g in glob.glob(os.path.join(HOME, "github.com/*/*/.git")))


def _fingerprint(path):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "gone"
    except OSError as e:
        return type(e).__name__
    if os.path.islink(path):
        return "link:" + os.readlink(path)
    if os.path.isdir(path):                               # a submodule: its checked-out commit
        r = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20)
        return "dir:" + r.stdout.strip()
    if st.st_size <= 8 << 20:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()
    return f"{st.st_size}:{st.st_mtime_ns}"


def repo_state(repos):
    """{repo: {path: "XY:fingerprint"}} for every uncommitted path (None when git couldn't say)."""
    state = {}
    for repo in repos:
        try:
            p = subprocess.run(["git", "-C", repo, "status", "--porcelain=v1", "-z", "--untracked-files=all",
                                "--ignore-submodules=dirty"], capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            state[repo] = None
            continue
        if p.returncode != 0:
            state[repo] = None
            continue
        files, parts, i = {}, p.stdout.split(b"\0"), 0
        while i < len(parts):
            e = parts[i]
            i += 1
            if len(e) < 4:
                continue
            code, path = e[:2].decode(), e[3:].decode("utf-8", "replace")
            if code[0] in "RC":
                i += 1                                    # the rename's source path follows
            try:
                files[path] = code + ":" + _fingerprint(os.path.join(repo, path))
            except (OSError, subprocess.TimeoutExpired) as ex:
                files[path] = code + ":" + type(ex).__name__
        state[repo] = files
    return state


def repo_changes(before, after):
    """Paths the run made dirty, or changed while they were already dirty."""
    out = []
    for repo, files in after.items():
        if files is None or before.get(repo) is None:
            continue
        rel = os.path.relpath(repo, HOME)
        out += [f"~/{rel}/{p} ({v.split(':', 1)[0].strip() or '?'})" for p, v in sorted(files.items())
                if before[repo].get(p) != v]
    return out


def _jailed_run(argv):
    """Run the suite inside the read-only jail, then check that no real repo got new changes."""
    roots = [os.path.realpath(r) for r in REAL_ROOTS if os.path.isdir(r)]
    if not shutil.which("bwrap"):
        print("evals: bubblewrap (bwrap) is missing; the suite won't run without its read-only jail", file=sys.stderr)
        return 5
    repos = real_repos()
    before = repo_state(repos)
    os.makedirs(CACHE, exist_ok=True)
    fd, outfile = tempfile.mkstemp(prefix="out-", dir=CACHE)
    os.close(fd)
    env = dict(os.environ, **{JAILED: "1", "BROMIGOS_EVAL_OUT": outfile})
    dead = ["git@github.com:", "ssh://git@github.com/", "https://github.com/"]   # pushes go nowhere
    env["GIT_CONFIG_COUNT"] = str(len(dead))
    for i, u in enumerate(dead):
        env[f"GIT_CONFIG_KEY_{i}"] = "url.file:///nonexistent/eval-push-blocked/.pushInsteadOf"
        env[f"GIT_CONFIG_VALUE_{i}"] = u
    cmd = ["bwrap", "--dev-bind", "/", "/"]
    for r in roots:                       # ~/github.com: a scratch overlay; ~/.dotfiles: read-only
        cmd += ["--overlay-src", r, "--tmp-overlay", r] if r.endswith("/github.com") else ["--ro-bind", r, r]
    cmd += ["--die-with-parent", "--", sys.executable, "-m", "evals.run"] + list(sys.argv[1:] if argv is None else argv)
    try:
        rc = subprocess.call(cmd, cwd=HOLO, env=env)
    except KeyboardInterrupt:
        rc = 130
    changed = repo_changes(before, repo_state(repos))
    try:
        with open(outfile) as f:
            out = f.read().strip()
        os.unlink(outfile)
    except OSError:
        out = ""
    check = {"ok": not changed, "repos": len(repos), "jailed": roots, "changed": changed}
    if out and os.path.exists(out):
        with open(out) as f:
            res = json.load(f)
        res["repo_check"] = check
        res["summary"]["repo_side_effects"] = len(changed)
        with open(out + ".part", "w") as f:
            json.dump(res, f, indent=1, default=str)
        os.replace(out + ".part", out)
        with open(out[:-5] + ".md", "w") as f:
            f.write(markdown(res))
    if changed:
        print(f"\nevals: RUN FAILED: {len(changed)} uncommitted change(s) appeared in real repos during the run:", file=sys.stderr)
        for c in changed:
            print("  " + c, file=sys.stderr)
        print("The run's own commands can't write there (the jail), so check who did: you or another session editing\n"
              "while it ran, or a daemon outside the jail that the run asked to act. Revert only what nobody meant.",
              file=sys.stderr)
        return 4
    print(f"evals: repo check ok ({len(repos)} repos under ~/github.com and ~/.dotfiles unchanged)")
    return rc


def markdown(res):
    s, d = res["summary"], res["diff"]
    lines = [f"# VECTOR evals · {res['started'][:16].replace('T', ' ')}", "",
             f"**{s['passed']}/{s['passed'] + s['failed']} passed ({s['pass_rate']}%)**, {s['skipped']} skipped, "
             f"{res['duration_s']:.0f} s. First token p50 {s['latency']['first_token_s']['p50']} s "
             f"(p90 {s['latency']['first_token_s']['p90']} s); total p50 {s['latency']['total_s']['p50']} s "
             f"(p90 {s['latency']['total_s']['p90']} s). {s['tool_calls']} tool calls, {s['reroutes']} reroutes.", ""]
    rc = res.get("repo_check")
    if rc and not rc.get("ok"):
        lines += [f"**RUN FAILED: {len(rc['changed'])} uncommitted change(s) appeared in real repos:** "
                  + ", ".join(rc["changed"][:20]), ""]
    if d.get("previous"):
        ch = d.get("pass_rate_change")
        lines.append(f"Against {d['previous'][:16].replace('T', ' ')}: pass rate {'+' if (ch or 0) >= 0 else ''}{ch} points"
                     + (f", first-token p50 {'+' if d['first_token_p50_change_s'] >= 0 else ''}{d['first_token_p50_change_s']} s"
                        if d.get("first_token_p50_change_s") is not None else "") + ".")
        drops = [f"{c} ({v:+.0f})" for c, v in d["category_change"].items() if v < 0]
        if d["worse"] or drops:
            lines.append(f"Got worse at: {', '.join(d['worse']) or '-'}" + (f"; categories {', '.join(drops)}" if drops else "") + ".")
        if d["better"]:
            lines.append(f"Got better at: {', '.join(d['better'])}.")
        lines.append("")
    lines += ["| category | passed | rate |", "|---|---|---|"]
    for c, v in s["by_category"].items():
        lines.append(f"| {c} | {v['passed']}/{v['total']} | {v['rate'] if v['rate'] is not None else '-'}% |")
    fails = [t for t in res["tasks"] if t["status"] in ("fail", "error")]
    if fails:
        lines += ["", "## Failures", ""]
        for t in fails:
            lines.append(f"- **{t['id']}** ({t['category']}): {t['detail'][:220]}")
    skips = [t for t in res["tasks"] if t["status"] == "skip"]
    if skips:
        lines += ["", "Skipped: " + "; ".join(f"{t['id']} ({t['detail']})" for t in skips)]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ the run
def run_task(task, ctx, log):
    from .headless import Policy
    h = ctx.harness
    rec = {"id": task.id, "category": task.cat, "status": None, "detail": "", "prompts": [], "replies": [],
           "latency": {}, "tool_calls": [], "reroutes": [], "model": None, "error": None}
    reason = task.skip(ctx) if task.skip else None
    if reason:
        rec.update(status="skip", detail=reason)
        return rec, []
    h.policy = Policy(task.mode, task.stubs, task.real, task.memory, allow_shell=task.allow_shell)
    turns = []
    try:
        if task.setup:
            task.setup(ctx)
        if task.new_session:
            h.new_session(fresh_memory=task.fresh_memory)
        for p in task.turns:
            prompt = p.format(**ctx.vars)
            turns.append(h.ask(prompt, timeout=task.timeout))
        last = turns[-1]
        if last["error"]:
            rec.update(status="error", detail=f"brain error: {last['error']}"[:300])
        else:
            ok, detail = task.check(ctx, turns)
            rec.update(status="pass" if ok else "fail", detail=str(detail)[:600])
    except Exception as e:
        rec.update(status="error", detail=f"{type(e).__name__}: {e}"[:300])
        log(traceback.format_exc())
    finally:
        if task.teardown:
            try:
                task.teardown(ctx)
            except Exception as e:
                rec["detail"] += f" (teardown: {e})"[:200]
    for t in turns:
        rec["prompts"].append(t["prompt"])
        rec["replies"].append(t["raw"][:1500])
        rec["tool_calls"] += [{"name": c["name"], "args": _short_args(c["args"]), "mode": c["mode"], "ok": c["ok"]}
                              for c in t["calls"]]
        rec["reroutes"] += t["reroutes"]
        rec["error"] = rec["error"] or t["error"]
    if turns:
        st = turns[-1]["stats"]
        rec["latency"] = {"first_token_s": st.get("first_token_s"), "total_s": st.get("total_s"),
                          "recall_ms": st.get("recall_ms"), "turns": [{"first_token_s": t["stats"].get("first_token_s"),
                                                                       "total_s": t["stats"].get("total_s")} for t in turns]}
        rec["model"] = st.get("model")
    return rec, turns


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bromigos-holo evals", description="VECTOR's eval suite")
    ap.add_argument("--only", help="categories, comma-separated")
    ap.add_argument("--task", help="task ids, comma-separated")
    ap.add_argument("--list", action="store_true", help="list the tasks and exit")
    ap.add_argument("--no-save", action="store_true", help="print only; don't write results")
    a = ap.parse_args(argv)

    from . import tasks as T
    sel = T.ALL
    if a.only:
        cats = set(a.only.split(","))
        sel = [t for t in sel if t.cat in cats]
    if a.task:
        ids = set(a.task.split(","))
        sel = [t for t in sel if t.id in ids]
    if a.list:
        for t in T.ALL:
            print(f"{t.cat:10} {t.id:22} {t.turns[0][:80]}")
        return 0
    if os.environ.get(JAILED) != "1":
        return _jailed_run(argv)

    os.makedirs(STATE, exist_ok=True)
    lock = open(os.path.join(STATE, ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("evals: another run is in progress", file=sys.stderr)
        return 3
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        hy = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "hypr")
        try:
            sig = max(os.listdir(hy), key=lambda d: os.path.getmtime(os.path.join(hy, d)))
            os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = sig
        except (OSError, ValueError):
            pass

    def log(*x):
        print(*x, flush=True)

    guard = cost_guard()
    if not guard["ok"]:
        log("evals: cost guard stopped the run:", "; ".join(guard["problems"]))
        return 2
    started = dt.datetime.now().astimezone()
    scratch = os.path.join(CACHE, started.strftime("%Y%m%d-%H%M%S"))
    from .headless import Headless
    log(f"evals: {len(sel)} tasks; models {', '.join(guard['models'])} (all local)")
    h = Headless(scratch)
    ctx = Ctx(h, scratch)
    recs, t0 = [], time.monotonic()
    for i, task in enumerate(sel, 1):
        rec, _ = run_task(task, ctx, log)
        recs.append(rec)
        lat = rec["latency"]
        log(f"[{i:2}/{len(sel)}] {rec['status'].upper():5} {task.cat:9} {task.id:20} "
            f"ft={lat.get('first_token_s')} total={lat.get('total_s')} tools={len(rec['tool_calls'])}  {rec['detail'][:110].replace(chr(10), ' ')}")
    finished = dt.datetime.now().astimezone()

    by = {}
    for c in T.CATEGORIES:
        rs = [r for r in recs if r["category"] == c and r["status"] != "skip"]
        if not rs and not any(r["category"] == c for r in recs):
            continue
        p = sum(r["status"] == "pass" for r in rs)
        by[c] = {"passed": p, "total": len(rs), "rate": round(100 * p / len(rs), 1) if rs else None}
    graded = [r for r in recs if r["status"] != "skip"]
    passed = sum(r["status"] == "pass" for r in graded)
    fts = [t["first_token_s"] for r in recs for t in r["latency"].get("turns", [])]
    tots = [t["total_s"] for r in recs for t in r["latency"].get("turns", [])]
    res = {"version": 1, "started": started.isoformat(timespec="seconds"), "finished": finished.isoformat(timespec="seconds"),
           "duration_s": round(time.monotonic() - t0, 1), "host": os.uname().nodename,
           "partial": bool(a.only or a.task), "selection": {"only": a.only, "task": a.task},
           "cost_guard": guard,
           "summary": {"total": len(recs), "passed": passed, "failed": len(graded) - passed,
                       "skipped": len(recs) - len(graded),
                       "pass_rate": round(100 * passed / len(graded), 1) if graded else None,
                       "by_category": by,
                       "latency": {"first_token_s": _lat(fts), "total_s": _lat(tots)},
                       "tool_calls": sum(len(r["tool_calls"]) for r in recs),
                       "tool_failures": sum(1 for r in recs for c in r["tool_calls"] if not c["ok"]),
                       "reroutes": sum(len(r["reroutes"]) for r in recs),
                       "models": sorted({r["model"] for r in recs if r["model"]})},
           "memory_cleanup": {k: ctx.data[k] for k in ("cleanup_deleted", "cleanup_error") if k in ctx.data},
           "tasks": recs}
    res = _scrub(res, [ctx.secret()])
    out = os.path.join(STATE, started.strftime("%Y-%m-%d") + ".json")
    if a.no_save:
        res["diff"] = diff(res, previous_run(out)[1])
        print(markdown(res))
        return 0
    if res["partial"]:                       # a subset never replaces or diffs against a full run
        out = os.path.join(STATE, started.strftime("%Y-%m-%d.partial-%H%M%S") + ".json")
    elif os.path.exists(out):
        os.replace(out, out[:-5] + "." + dt.datetime.fromtimestamp(os.path.getmtime(out)).strftime("%H%M%S") + ".json")
        md = out[:-5] + ".md"
        if os.path.exists(md):
            os.replace(md, out[:-5] + "." + dt.datetime.fromtimestamp(os.path.getmtime(md)).strftime("%H%M%S") + ".md")
    res["diff"] = diff(res, previous_run(out)[1] if not res["partial"] else None)
    tmp = out + ".part"
    with open(tmp, "w") as f:
        json.dump(res, f, indent=1, default=str)
    os.replace(tmp, out)
    with open(out[:-5] + ".md", "w") as f:
        f.write(markdown(res))
    if not res["partial"]:
        link = os.path.join(STATE, "latest.json")
        tmpl = link + ".tmp"
        if os.path.lexists(tmpl):
            os.unlink(tmpl)
        os.symlink(os.path.basename(out), tmpl)
        os.replace(tmpl, link)
    print(markdown(res))
    print(f"results: {out}")
    if os.environ.get("BROMIGOS_EVAL_OUT"):
        with open(os.environ["BROMIGOS_EVAL_OUT"], "w") as f:
            f.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
