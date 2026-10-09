"""Mood from the facts: a tool result that shows trouble pushes VECTOR concerned or alarmed,
even if the reply forgets to say so. Returns calm | concerned | alarmed (or None: no opinion)."""
import json
import re


def _load(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _plugin_mood(name):
    try:
        from . import plugins
        return (plugins.TOOLS.get(name) or {}).get("mood")
    except Exception:
        return None


def from_tool(name, result):
    d = _load(result)
    t = result if isinstance(result, str) else ""
    if isinstance(d, dict) and "error" in d and len(d) == 1:
        return None                                  # a failed lookup isn't news about the systems
    if name == "lab_status" and isinstance(d, dict):
        ready, total = d.get("nodes_ready"), d.get("nodes_total")
        if ready is not None and total and ready < total:
            return "alarmed"
        down = d.get("services_down") or []
        if len(down) >= 3:
            return "alarmed"
        if down or (d.get("pods_not_running") or 0) > 0:
            return "concerned"
        return "calm"
    if name == "k8s_get":
        if re.search(r"\bNotReady\b", t):
            return "alarmed"
        if re.search(r"CrashLoopBackOff|ImagePullBackOff|\bError\b|OOMKilled|Evicted", t):
            return "concerned"
        return None
    if name == "k8s_events":
        return "concerned" if re.search(r"Warning", t) and re.search(r"BackOff|Failed|Unhealthy|OOM", t) else None
    if name == "argocd_apps" and isinstance(d, dict):
        bad = d.get("problems") or []
        if any((p.get("health") in ("Missing", "Degraded")) for p in bad):
            return "concerned"
        return "calm" if not bad else None
    if name == "github":
        if isinstance(d, list) and d and isinstance(d[0], dict) and "conclusion" in d[0]:
            latest = d[0].get("conclusion")
            return "concerned" if latest in ("failure", "timed_out", "startup_failure") else "calm"
        return None
    if name == "prometheus_query" and isinstance(d, dict):
        if "ALERTS" in t and re.search(r'"severity"\s*:\s*"critical"', t) and '"firing"' in t:
            return "alarmed"
        return None
    own = _plugin_mood(name)
    if own:                                          # a plugin tool's own reading of its result (plugins.tool mood=)
        try:
            m = own(d if d is not None else result)
        except Exception:
            return None
        return m if m in ("calm", "concerned", "alarmed") else None
    if name == "system_stats" and isinstance(d, dict):
        g = d.get("gpu") or {}
        if (d.get("cpu_temp_c") or 0) >= 90 or (g.get("temp") or 0) >= 88:
            return "alarmed"
        full = [x for x in d.get("disks") or [] if (x.get("pct") or 0) >= 95]
        return "concerned" if full else None
    return None
