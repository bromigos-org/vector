"""VECTOR's act tools: operational actions on the homelab, as ServiceAccount
`vector-operator` (the lab's vector chart: edit on workloads, nothing on Secrets/RBAC/
tokens/nodes; admission policies keep him out of privileged namespaces' templates and
off the real-money workloads). Each action is a fixed kubectl/gh argv (never a shell
string), waits for the result where there is one, and is audited and put on the event feed.

Real money stays out in code here too: no workload whose name starts with a real-money
service the user names in vector.toml's [real_money] services (money.py).
"""
import json
import os
import re
import subprocess
import time
import urllib.request

from . import events, money
from ..private import PRIV

HOME = os.path.expanduser("~")
KUBECONFIG = os.path.join(HOME, ".local/share/bromigos/vector-operator-kubeconfig")
KB_TOKEN = os.path.join(HOME, ".local/share/bromigos/gnosis-kb-ingest-token")
GNOSIS = PRIV.url("gnosis_gate")               # private: endpoints.gnosis_gate
NAME = re.compile(r"[a-z0-9]([a-z0-9.\-]{0,251}[a-z0-9])?$")
KINDS = {"deployment": "deployment", "deploy": "deployment", "statefulset": "statefulset", "sts": "statefulset",
         "daemonset": "daemonset", "ds": "daemonset"}
OWNERS = ("bromigos-org", "nolgiainc", "blackflame007")
KB = {"bromigos": "kb-bromigos", "nolgia": "kb-nolgia", "personal": "kb-personal", "desktop": "kb-desktop",
      "homelab": "kb-homelab"}


def _name(v, what="name"):
    if not NAME.match(v or ""):
        raise ValueError(f"bad {what}: {v!r}")
    why = money.service_refusal(v) if what != "namespace" else None
    if why:
        raise PermissionError(why)
    return v


def _kubectl(args, timeout=60):
    if not os.path.exists(KUBECONFIG):
        raise RuntimeError("no vector-operator kubeconfig on this machine")
    r = subprocess.run(["kubectl", "--kubeconfig", KUBECONFIG, "--context", "default", "--request-timeout=20s"] + args,
                       capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-400:])
    return r.stdout


def _kind(kind):
    k = KINDS.get((kind or "deployment").lower().rstrip("s") if kind not in KINDS else kind)
    if not k:
        raise ValueError("kind must be deployment, statefulset or daemonset")
    return k


def _locate(ns, kind, name):
    """The namespace a workload really lives in: the one given if it's there, else the single
    namespace that has one by that name (so "searxng in the homelab" finds namespace searxng)."""
    try:
        _kubectl(["get", kind, name, "-n", ns, "-o", "name"])
        return ns, None
    except RuntimeError as e:
        if "NotFound" not in str(e) and "not found" not in str(e):
            raise
    rows = _kubectl(["get", kind, "-A", "--field-selector", f"metadata.name={name}", "-o",
                     "jsonpath={range .items[*]}{.metadata.namespace}{'\\n'}{end}"]).split()
    if len(rows) == 1:
        return rows[0], f"{kind} {name} isn't in namespace {ns}; found it in {rows[0]}"
    if not rows:
        raise ValueError(f"no {kind} named {name} in any namespace (k8s_get {kind}s to look)")
    raise ValueError(f"{kind} {name} exists in several namespaces ({', '.join(rows)}); say which")


def _ready(ns, kind, name):
    d = json.loads(_kubectl(["get", kind, name, "-n", ns, "-o", "json"]))
    st, spec = d.get("status") or {}, d.get("spec") or {}
    want = st.get("desiredNumberScheduled") if kind == "daemonset" else spec.get("replicas", 1)
    ready = st.get("numberReady") if kind == "daemonset" else st.get("readyReplicas", 0)
    return {"ready": f"{ready or 0}/{want}", "updated": st.get("updatedReplicas", st.get("updatedNumberScheduled"))}


def k8s_restart(namespace, name, kind="deployment", wait=True):
    ns, n, k = _name(namespace or "default", "namespace"), _name(name), _kind(kind)
    ns, moved = _locate(ns, k, n)
    try:
        _kubectl(["rollout", "restart", f"{k}/{n}", "-n", ns])
        out = {"ok": True, "restarted": f"{ns}/{k}/{n}", **({"note": moved} if moved else {})}
        if wait:
            try:
                _kubectl(["rollout", "status", f"{k}/{n}", "-n", ns, "--timeout=240s"], timeout=260)
                out["rollout"] = "complete"
            except RuntimeError as e:
                out["rollout"] = f"not complete: {str(e)[-160:]}"
        out.update(_ready(ns, k, n))
        events.emit("k8s.action", action="restart", namespace=ns, kind=k, name=n, ok=True, ready=out.get("ready"))
        return out
    except Exception:
        events.emit("k8s.action", action="restart", namespace=ns, kind=k, name=n, ok=False)
        raise


def k8s_scale(namespace, name, replicas, kind="deployment", wait=True):
    ns, n, k = _name(namespace, "namespace"), _name(name), _kind(kind)
    reps = int(replicas)
    if not 0 <= reps <= 20:
        raise ValueError("replicas must be 0-20")
    if k == "daemonset":
        raise ValueError("a daemonset can't be scaled")
    ns, moved = _locate(ns, k, n)
    try:
        _kubectl(["scale", f"{k}/{n}", "-n", ns, f"--replicas={reps}"])
        out = {"ok": True, "scaled": f"{ns}/{k}/{n}", "replicas": reps, **({"note": moved} if moved else {})}
        if wait and reps:
            try:
                _kubectl(["rollout", "status", f"{k}/{n}", "-n", ns, "--timeout=240s"], timeout=260)
            except RuntimeError as e:
                out["rollout"] = f"not complete: {str(e)[-160:]}"
        out.update(_ready(ns, k, n))
        events.emit("k8s.action", action="scale", namespace=ns, kind=k, name=n, replicas=reps, ok=True)
        return out
    except Exception:
        events.emit("k8s.action", action="scale", namespace=ns, kind=k, name=n, replicas=reps, ok=False)
        raise


def k8s_delete_pod(namespace, pod):
    ns, p = _name(namespace, "namespace"), _name(pod, "pod")
    try:
        _kubectl(["delete", "pod", p, "-n", ns, "--wait=true", "--timeout=90s"], timeout=110)
        events.emit("k8s.action", action="delete_pod", namespace=ns, kind="pod", name=p, ok=True)
        return {"ok": True, "deleted": f"{ns}/{p}", "note": "its controller (if any) starts a replacement"}
    except Exception:
        events.emit("k8s.action", action="delete_pod", namespace=ns, kind="pod", name=p, ok=False)
        raise


def k8s_run_job(namespace, cronjob, wait=False):
    ns, cj = _name(namespace, "namespace"), _name(cronjob, "cronjob")
    job = f"{cj[:40]}-vector-{time.strftime('%H%M%S')}"
    try:
        _kubectl(["create", "job", job, f"--from=cronjob/{cj}", "-n", ns])
        out = {"ok": True, "job": f"{ns}/{job}", "from": cj}
        if wait:
            try:
                _kubectl(["wait", f"job/{job}", "-n", ns, "--for=condition=complete", "--timeout=540s"], timeout=560)
                out["finished"] = "complete"
            except RuntimeError as e:
                out["finished"] = f"not complete: {str(e)[-160:]}"
        events.emit("k8s.action", action="run_job", namespace=ns, kind="job", name=job, ok=True)
        return out
    except Exception:
        events.emit("k8s.action", action="run_job", namespace=ns, kind="job", name=job, ok=False)
        raise


# ------------------------------------------------------------------ Argo CD
def _app(app):
    d = json.loads(_kubectl(["get", "application", _name(app, "app"), "-n", "argocd", "-o", "json"]))
    st = d.get("status") or {}
    op = st.get("operationState") or {}
    return {"app": app, "sync": (st.get("sync") or {}).get("status"), "health": (st.get("health") or {}).get("status"),
            "revision": ((st.get("sync") or {}).get("revision") or "")[:8], "operation": op.get("phase"),
            "message": (op.get("message") or "")[:200]}


def argocd_wait(app, timeout_s=300):
    t0 = time.monotonic()
    s = _app(app)
    while time.monotonic() - t0 < min(int(timeout_s), 900):
        if s["sync"] == "Synced" and s["health"] == "Healthy" and s["operation"] not in ("Running",):
            break
        time.sleep(5)
        s = _app(app)
    s["waited_s"] = round(time.monotonic() - t0)
    events.emit("argo.sync", app=app, action="wait", sync=s["sync"], health=s["health"], revision=s["revision"])
    return s


def argocd_refresh(app, hard=False):
    _kubectl(["annotate", "application", _name(app, "app"), "-n", "argocd",
              f"argocd.argoproj.io/refresh={'hard' if hard else 'normal'}", "--overwrite"])
    time.sleep(3)
    s = _app(app)
    events.emit("argo.sync", app=app, action="refresh", sync=s["sync"], health=s["health"], revision=s["revision"])
    return {"ok": True, **s}


def argocd_sync(app, wait=True):
    a = _name(app, "app")
    body = {"operation": {"initiatedBy": {"username": "vector"}, "sync": {"syncStrategy": {"hook": {}}}}}
    _kubectl(["patch", "application", a, "-n", "argocd", "--type", "merge", "-p", json.dumps(body)])
    events.emit("argo.sync", app=a, action="sync", sync=None, health=None, revision=None)
    return argocd_wait(a, 300) if wait else {"ok": True, "app": a, "note": "sync started"}


# ------------------------------------------------------------------ CI
def _repo(repo):
    r = (repo or "").strip()
    if "/" not in r:
        r = f"bromigos-org/{r}"
    owner, _, name = r.partition("/")
    if owner not in OWNERS or not re.fullmatch(r"[\w.\-]{1,100}", name):
        raise ValueError(f"repo must be under {', '.join(OWNERS)}")
    return r


def ci_watch(repo, sha=None, branch=None, timeout_s=900):
    """Wait for the GitHub Actions runs of a commit (or a branch's latest) to finish."""
    r = _repo(repo)
    if sha and not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        raise ValueError("sha must be hex")
    args = ["run", "list", "-R", r, "--limit", "10", "--json",
            "databaseId,workflowName,status,conclusion,headSha,headBranch,url,createdAt"]
    if sha:
        args += ["--commit", sha]
    elif branch:
        args += ["--branch", branch]
    t0 = time.monotonic()
    runs = []
    while time.monotonic() - t0 < min(int(timeout_s), 1800):
        p = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip()[-300:])
        runs = json.loads(p.stdout or "[]")
        if not sha and runs:
            head = runs[0]["headSha"]
            runs = [x for x in runs if x["headSha"] == head]
        if runs and all(x["status"] == "completed" for x in runs):
            break
        if not runs and time.monotonic() - t0 > 120:
            break
        time.sleep(15)
    out = [{"workflow": x["workflowName"], "status": x["status"], "conclusion": x["conclusion"], "sha": x["headSha"][:8],
            "url": x["url"]} for x in runs]
    for x in out:
        events.emit("ci.result", repo=r, sha=x["sha"], workflow=x["workflow"], status=x["status"],
                    conclusion=x["conclusion"], url=x["url"])
    green = bool(out) and all(x["conclusion"] == "success" for x in out)
    return {"repo": r, "runs": out, "all_green": green, "waited_s": round(time.monotonic() - t0),
            **({} if out else {"note": "no runs for that commit (yet)"})}


# ------------------------------------------------------------------ knowledge base
def kb_write(space, title, text):
    """Add a note to a knowledge-base space (what he learned, verified, for later)."""
    sp = KB.get((space or "").replace("kb-", ""))
    if not sp:
        raise ValueError(f"space must be one of {sorted(KB)}")
    text, title = (text or "").strip(), (title or "").strip()[:120]
    if not text or len(text) > 6000 or not title:
        raise ValueError("title and text (up to 6000 chars) are required")
    from .shell import redact
    if redact(text) != text:
        raise ValueError("that text looks like it contains a secret; not stored")
    with open(KB_TOKEN) as f:
        tok = f.read().strip()
    body = {"scope": {"tenant_id": "bromigos", "space_id": sp, "agent_id": "kb-ingest", "session_id": "kb-sync",
                      "user_id": sp, "visibility": "agent_shared"},
            "content": f"[vector notes — {title}]\n{text}", "infer": False,
            "metadata": {"repo": "vector", "path": "notes", "heading": title, "source": "vector",
                         "date": time.strftime("%Y-%m-%d")}}
    from ..live import ssl_ctx
    req = urllib.request.Request(GNOSIS + "/v1/memories", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx()) as r:
        d = json.load(r)
    events.emit("memory.file", space=sp, category="knowledge")
    return {"ok": True, "space": sp, "title": title, "ids": [x.get("memory_id") or x.get("id") for x in (d.get("results") or [])][:3]}
