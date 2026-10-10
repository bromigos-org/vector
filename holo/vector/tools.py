"""VECTOR's tools: fast read tools, act tools for the homelab, Vault wiring, desktop actions.
His full terminal is separate (run_shell, holo/vector/shell.py, with its own limits).

Hard limits (enforced here, not by the prompt):
  * every subprocess here is a fixed argv from an allowlist, never a shell string;
  * reads: the cluster through the `pilot-readonly` ServiceAccount kubeconfig (get/list/
    watch, no secrets/configmaps/exec); verbs used: get, logs, top;
  * acts (act.py): restart, scale, delete a pod, run a Job from a CronJob, Argo sync/
    refresh/wait, as `vector-operator` (RBAC + admission policies in the lab's vector chart),
    never a real-money workload (money.py); CI watch through gh; notes into the kb-* knowledge spaces;
  * Vault (vault.py): AppRole `vector`; list names, put a generated or operator-typed
    value, copy Vault to Vault; never returns or logs a value;
  * Prometheus: GET /api/v1/query and query_range only;
  * GitHub: `gh` read subcommands only, repos under bromigos-org;
  * his clones and pull requests (devflow.py): git through his terminal's sandbox in his own
    clones, as his account bromigodev; commits by explicit path, never on a default branch;
    pr_merge only for his own PRs with green CI, never one that touches devflow.LIMITS;
  * the user's own systems: their plugins' tools (plugins.py), through the same gate;
  * Gnosis: POST /v1/memories/search only, two spaces;
  * files: read under the docs roots only (markdown/json/yaml/txt), never secret-looking
    names; the one write is appending to FIELD NOTES;
  * actions: launch an allowlisted app or an http(s) URL, toggle a widget panel, switch
    the den wallpaper, run a scanner pass, show a hologram;
  * his own browser (browser.py): a separate Chrome he drives, never the user's; no
    sensitive sites, logins, purchases, downloads or uploads;
  * the user's profile (set_profile): only its own fields (name, full_name, pronouns,
    address, alias), through `bromigos profile` with a fixed argv; the CLI validates values;
  * Claude Code (claude.py): its transcripts, jobs, memory files and logs, read only and
    redacted (redact.py) before anything reaches the model; herdr is only asked to list panes.
Every call is appended to ~/.local/state/bromigos/vector-audit.log (tool, args, ok, ms, and
for the Claude tools what was read: session ids and byte ranges, never the content).
"""
import calendar
import datetime as dt
import fnmatch
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

from ..live import get_json
from ..paths import command, conf
from . import llm
from ..private import PRIV

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".local/state/bromigos")
AUDIT = os.path.join(STATE, "vector-audit.log")
NOTES = os.path.join(HOME, ".local/share/bromigos/notes.md")
# Internal URLs and Vault paths come from the private overlay (AGENTS.md, Private values).
VAULT_ROOT = PRIV.vault_root() or "<vault root>"   # the KV prefix his Vault tools may touch
LAB_DOMAIN = PRIV.lan_domain() or "<lab domain>"
WIDGETS = command("bromigos-widgets", ".config/bromigos/widgets/bromigos-widgets")
WALLPAPER = os.path.join(HOME, ".config/bromigos/bin/bromigos-wallpaper")
LIVE = command("bromigos-live", ".config/bromigos-live/bin/bromigos-live")
ORG = "bromigos-org"
DOC_ROOTS = [os.path.join(HOME, "github.com/bromigos-org"), os.path.join(HOME, "github.com/nolgiainc"),
             os.path.join(HOME, "github.com/blackflame007"), os.path.join(HOME, ".dotfiles")]
DOC_EXT = (".md", ".json", ".yaml", ".yml", ".txt", ".toml")
SECRETISH = re.compile(r"(secret|token|credential|password|passwd|\.env|private|\.key$|\.pem$|kubeconfig|vault|zsh-secrets)", re.I)
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", "target", "private"}

APPS = {  # name -> argv (the desktop's own launch commands, from the Hyprland binds)
    "terminal": ["kitty"], "kitty": ["kitty"], "browser": ["librewolf"], "librewolf": ["librewolf"],
    "chrome": ["google-chrome-stable"], "files": ["pcmanfm"], "pcmanfm": ["pcmanfm"], "dolphin": ["dolphin"],
    "discord": ["discord"],
    "spotify": ["spotify-launcher"], "obs": ["obs"], "steam": ["steam"],
}
GH_ACTIONS = {
    "repos": lambda repo, n: ["repo", "list", ORG, "--limit", str(n), "--json", "name,description,pushedAt,isArchived"],
    "commits": lambda repo, n: ["api", f"repos/{repo}/commits?per_page={n}", "--jq",
                                ".[] | {sha: .sha[0:8], date: .commit.author.date, author: .commit.author.name, msg: (.commit.message | split(\"\\n\")[0])}"],
    "runs": lambda repo, n: ["run", "list", "-R", repo, "--limit", str(n), "--json", "workflowName,status,conclusion,headBranch,createdAt,displayTitle"],
    "prs": lambda repo, n: ["pr", "list", "-R", repo, "--limit", str(n), "--state", "all", "--json", "number,title,state,author,updatedAt"],
    "issues": lambda repo, n: ["issue", "list", "-R", repo, "--limit", str(n), "--json", "number,title,state,updatedAt"],
}

# the hologram that fits each tool, and the parts worth calling out
EXHIBIT = {
    "system_stats": ("workstation", ["cpu", "gpu", "ram"]),
    "lab_status": ("rack", ["nodes", "gpu", "storage"]),
    "k8s_get": ("rack", ["nodes"]), "k8s_logs": ("rack", ["nodes"]), "k8s_events": ("rack", ["frame"]),
    "argocd_apps": ("rack", ["frame"]), "prometheus_query": ("rack", ["nodes", "gpu"]),
    "gnosis_search": ("monolith", ["slab4"]),
    "scan": ("workstation", ["cpu", "gpu"]),
    "k8s_restart": ("rack", ["nodes"]), "k8s_scale": ("rack", ["nodes"]), "k8s_delete_pod": ("rack", ["nodes"]),
    "k8s_run_job": ("rack", ["nodes"]), "argocd_sync": ("rack", ["frame"]), "argocd_refresh": ("rack", ["frame"]),
    "argocd_wait": ("rack", ["frame"]),
}


LAST_USER_TEXT = {"text": ""}          # the host's latest words (set by the brain each turn)
PRIVATE_ARGS = {"remember": ("text",), "forget": ("what",), "gnosis_search": ("query",), "herdr_send": ("text",),
                "browser_type": ("text",), "set_profile": ("value",), "claude_search": ("query",),
                "claude_say_latest": ("about",), "claude_memory": ("query",)}


def _audit(name, args, ok, ms, size=0, err=None, read=None):
    os.makedirs(STATE, exist_ok=True)
    if name in PRIVATE_ARGS:      # memory text is hashed and truncated, never logged whole
        import hashlib
        args = dict(args)
        for k in PRIVATE_ARGS[name]:
            if isinstance(args.get(k), str):
                t = args[k]
                args[k] = {"sha1": hashlib.sha1(t.encode()).hexdigest()[:12], "head": t[:40], "chars": len(t)}
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": name, "args": args, "ok": ok, "ms": ms, "bytes": size}
    if read:                      # what a Claude tool read (session ids, byte ranges), never the content
        rec["read"] = read
    if err:
        rec["err"] = str(err)[:200]
    with open(AUDIT, "a") as f:
        f.write(json.dumps(rec) + "\n")


def _run(argv, timeout=20, env=None):
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return r.stdout


def _short(obj, limit=7000):
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str, separators=(",", ":"))
    return s if len(s) <= limit else s[:limit] + f"…[truncated {len(s) - limit} chars]"


def _detach(argv):
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


# ================================================================== tools
def system_stats(live=None):
    from ..live import Local
    src = live.sources["local"] if live else Local()
    d = src.data if (live and src.data) else src.read()
    if not (live and src.data):
        time.sleep(0.4)
        d = src.read()
    g = d.get("gpu") or {}
    return {"host": d["host"], "kernel": d["kernel"], "uptime_h": round(d["uptime"] / 3600, 1),
            "cpu_pct": round(d["cpu"], 1), "cpu_peak_core_pct": round(d["cpu_max"], 1), "cpu_temp_c": d["cpu_temp"],
            "load": d["load"], "mem_used_gib": round(d["mem_used"] / 2**30, 1), "mem_total_gib": round(d["mem_total"] / 2**30, 1),
            "swap_used_gib": round(d["swap_used"] / 2**30, 1),
            "gpu": {k: g.get(k) for k in ("name", "util", "temp", "watts")} | {"vram_used_gib": round(g.get("vram_used", 0) / 2**30, 1), "vram_total_gib": round(g.get("vram_total", 0) / 2**30, 1)} if g else None,
            "disks": [{"mount": x["mount"], "pct": x["pct"], "total_gb": round(x["total"] / 1e9)} for x in d["disks"][:5]],
            "net_down_mbps": round(d["net_rx"] * 8 / 1e6, 2), "net_up_mbps": round(d["net_tx"] * 8 / 1e6, 2),
            "fans_rpm": d["fans"][:4]}


def github(action, repo=None, limit=10):
    if action not in GH_ACTIONS:
        raise ValueError(f"action must be one of {sorted(GH_ACTIONS)}")
    if action != "repos":
        repo = repo if (repo or "").startswith(ORG + "/") else f"{ORG}/{repo}"
        if not re.fullmatch(ORG + r"/[\w.\-]{1,100}", repo or ""):
            raise ValueError("repo must be under bromigos-org")
    out = _run(["gh"] + GH_ACTIONS[action](repo, max(1, min(int(limit), 30))), timeout=25)
    try:
        return json.loads(out)
    except ValueError:
        return out


def conversation_history(query="", when="", limit=8):
    from . import history
    return history.tool(query, when, limit)


def _doc_ok(path):
    rp = os.path.realpath(path)
    if not any(rp.startswith(os.path.realpath(r) + os.sep) for r in DOC_ROOTS):
        return False
    if SECRETISH.search(rp) or not rp.endswith(DOC_EXT):
        return False
    from .shell import is_masked
    if is_masked(rp):                          # what his sandbox hides, this tool doesn't read either
        return False
    return not any(part in SKIP_DIRS for part in rp.split(os.sep))


def docs_search(query, repo=None, limit=12):
    q = query.lower().strip()
    if len(q) < 3:
        raise ValueError("query too short")
    roots = DOC_ROOTS if not repo else [os.path.join(DOC_ROOTS[0], repo) if repo != "dotfiles" else DOC_ROOTS[1]]
    hits, scanned = [], 0
    t0 = time.monotonic()
    for root in roots:
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
            for fn in files:
                p = os.path.join(d, fn)
                if not fn.endswith((".md",)) or not _doc_ok(p):
                    continue
                scanned += 1
                try:
                    with open(p, errors="replace") as f:
                        for n, line in enumerate(f, 1):
                            if q in line.lower():
                                hits.append({"path": p.replace(HOME, "~"), "line": n, "text": line.strip()[:200]})
                                if len(hits) >= limit:
                                    return {"hits": hits, "scanned": scanned}
                except OSError:
                    continue
            if time.monotonic() - t0 > 6:
                return {"hits": hits, "scanned": scanned, "note": "stopped after 6 s"}
    return {"hits": hits, "scanned": scanned}


def docs_read(path, start_line=1, lines=120):
    p = os.path.expanduser(path)
    if not _doc_ok(p):
        raise ValueError("not a readable doc (docs roots only; md/json/yaml/txt/toml; nothing secret-looking)")
    with open(p, errors="replace") as f:
        all_ = f.readlines()
    s = max(1, int(start_line))
    chunk = all_[s - 1:s - 1 + min(int(lines), 250)]
    return {"path": path, "total_lines": len(all_), "from": s, "text": "".join(chunk)}


def run_shell(command, cwd="~", timeout_s=60):
    """Full terminal as the operator's user; the limits live in shell.py (no sudo, no secrets,
    no real money), every command is logged to vector-shell.log."""
    from .shell import RUNNER
    return RUNNER.run(command, cwd, timeout_s)


def shell_off():
    t = LAST_USER_TEXT["text"].lower()
    asked = ((re.search(r"\b(terminal|shell|command line|commands)\b", t) and
              re.search(r"\b(off|stop|disable|don'?t|do not|no more|kill)\b", t)) or
             re.search(r"\b(switch|turn|shut|power)\s+(yourself\s+|vector\s+|you\s+)?(off|down)\b", t) or
             re.search(r"\bemergency (switch|stop|off)\b", t))
    if not asked:
        raise PermissionError("only when the host asks you to switch off or stop using the terminal (he hasn't); "
                              "a refused command is not a reason to switch off")
    from .shell import RUNNER, set_enabled
    RUNNER.kill("switched off")
    set_enabled(False)
    return {"ok": True, "switch": "off",
            "note": "the emergency switch is off: my terminal runs nothing and my tools that change things are "
                    "refused. Only the host switches me back on (bromigos vector on, SUPER+CTRL+ESCAPE, the bar or "
                    "Settings)."}


from .reach import (herdr_read, herdr_send, herdr_start, herdr_status, herdr_wait,  # noqa: E402
                    web_fetch, web_search)
from .act import (argocd_refresh, argocd_sync, argocd_wait, ci_watch, k8s_delete_pod,  # noqa: E402
                  k8s_restart, k8s_run_job, k8s_scale, kb_write)
from .vault import vault_copy, vault_list, vault_put  # noqa: E402
from .desk import app_search, launch_app, media, open_path, run_detached, window, windows  # noqa: E402
from .track import changes_check, github_repo_create  # noqa: E402
from .devflow import (pr_close, pr_merge, pr_open, pr_watch, work_branch, work_clone, work_commit,  # noqa: E402
                      work_push, work_status)
from .skills import load_skill  # noqa: E402
from .eyes import active_window, look, read_screen_text, watch  # noqa: E402
from .snapshots import snapshot_create, snapshot_list, snapshot_undo  # noqa: E402
from .briefing import briefing_now, quiet  # noqa: E402
from .nolgia import nolgia_catalog, nolgia_credits, nolgia_generate, nolgia_read, nolgia_review  # noqa: E402
from .create import create, make_hologram  # noqa: E402
from .lab import lab_console, lab_job_cancel, lab_job_submit, lab_jobs  # noqa: E402
from .tv import (tv_channels, tv_channel_add_item, tv_channel_remove_item, tv_channel_create,
                 tv_tune, tv_channel_delete)
from .media import (media_delete, media_get, media_language, media_recent, media_search, media_status,  # noqa: E402
                    media_upcoming)
from .browser import (browser_back, browser_click, browser_close, browser_find, browser_look,  # noqa: E402
                      browser_open, browser_read, browser_scroll, browser_tabs, browser_type)
from .claude import (claude_follow, claude_logs, claude_memory, claude_read, claude_say_latest,  # noqa: E402
                     claude_search, claude_sessions)


def notes_read(last_lines=60):
    if not os.path.exists(NOTES):
        return ""
    with open(NOTES) as f:
        ls = f.readlines()
    return "".join(ls[-min(int(last_lines), 200):])


def notes_append(text):
    text = text.strip()
    if not text or len(text) > 2000:
        raise ValueError("note must be 1-2000 chars")
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(NOTES, "a") as f:
        f.write(f"\n- {stamp}  {text}\n")
    return {"ok": True, "appended": len(text)}


def homelab():
    """bromigos_homelab (the bromigos-homelab package: docs/homelab.md), or None without it.
    The homelab's read tools are its VECTOR plugin's (homelab_tools.py), not the core's."""
    try:
        import bromigos_homelab
        return bromigos_homelab
    except ImportError:
        return None


def _switchboard():
    from . import plugins
    out = {k: PRIV.url(k) for k in ("lab", "prometheus", "grafana") if PRIV.url(k)}
    out.update({k: v for k, v in plugins.SWITCHBOARD.items() if k not in out})
    try:
        H = homelab()
        D = (H.snapshot() if H and H.snapshot_source() == "lab" else None) or {}
        for g in (D.get("config") or {}).get("groups", []):
            for s in g.get("services", []):
                if s.get("lan") and s.get("id"):
                    out[s["id"].lower()] = s["lan"]
    except Exception:
        pass
    return out


def launch(target):
    t = target.strip()
    if re.fullmatch(r"https?://[^\s\"'<>]{3,500}", t):
        _detach(["xdg-open", t])
        return {"opened": t}
    key = t.lower().replace(" ", "")
    if key in APPS:
        _detach(APPS[key])
        return {"launched": key}
    sb = _switchboard()
    if key in sb:
        _detach(["xdg-open", sb[key]])
        return {"opened": key, "url": sb[key]}
    from .desk import launch_app
    return launch_app(t)                      # any installed program, by fuzzy name


def panel(name, action="toggle"):
    names = {"all", "system", "network", "storage", "lab", "switchboard", "notes", "shortcuts"}
    if name not in names or action not in ("toggle", "show", "hide"):
        raise ValueError(f"panel must be one of {sorted(names)}; action toggle|show|hide")
    _run([WIDGETS, action, name], timeout=8)
    return {"ok": True, "panel": name, "action": action}


def wallpaper(variant):
    if variant not in ("den", "empty", "masked", "v1"):
        raise ValueError("variant must be den|empty|masked|v1")
    _run([WALLPAPER, variant], timeout=20)
    return {"ok": True, "wallpaper": variant}


def scan():
    _run([LIVE, "scan"], timeout=8)
    return {"ok": True, "note": "scanner pass running on the desktop"}


DECKS = {  # bromigos-live's holograms (README "Decks") -> their verbs
    "holodeck": {"open", "close"},
    "timeline": {"open", "close"},
    "driftmap": {"open", "close"},
    "mind": {"open", "close", "focus", "space", "clear"},
    "ops": {"open", "close", "focus", "clear"},
    "swarm": {"open", "close", "point", "focus", "clear"},
    "netmap": {"open", "close", "trace", "clear"},
    "lab": {"open", "close", "focus", "clear"},
}                                     # and the user's own decks, from their plugins (plugins.deck)


def hologram_deck(deck, verb="open", args=""):
    """Open or drive one of the live layer's holograms (fixed argv, allowlisted verbs)."""
    if deck not in DECKS:
        raise ValueError(f"deck must be one of {sorted(DECKS)}")
    if verb not in DECKS[deck]:
        raise ValueError(f"{deck} verbs: {sorted(DECKS[deck])}")
    a = (args or "").strip()
    if len(a) > 160 or not re.fullmatch(r"[\w .:/@#,+=-]*", a):
        raise ValueError("args: up to 160 plain characters")
    out = _run([LIVE, deck, verb] + a.split(), timeout=10).strip()
    return {"ok": True, "deck": deck, "verb": verb, "result": out[:300]}


def my_setup():
    """VECTOR's own configuration, read live from his code and config files (never remembered)."""
    import json as _j
    try:
        vj = _j.load(open(conf("voice.json")))
    except (OSError, ValueError):
        vj = {}
    from . import brain_pai, build, history, skills
    try:
        cap = _j.load(open(conf("nolgia.json"))).get("daily_credits")
    except (OSError, ValueError):
        cap = None
    voices = {r: {"speaker": v.get("name"), "marker": v.get("marker"), "use": v.get("use")} for r, v in (vj.get("voices") or {}).items()}
    lanes = brain_pai.lanes()
    return {
        "brain": {"framework": "Pydantic AI", "voice_lane": lanes[0], "deep_lane": lanes[1],
                  "first_byte_timeout_s": brain_pai.FIRST_BYTE, "stream_silence_timeout_s": brain_pai.IDLE_GAP,
                  "failed_model_cooldown_s": brain_pai.COOLDOWN, "thinking": "off on every route"},
        "speech_to_text": (vj.get("stt") or {}).get("model"), "stt_device": (vj.get("stt") or {}).get("device"),
        "text_to_speech": {"engine": vj.get("engine"), "fallback": vj.get("fallback"), "speed": vj.get("speed")},
        "voices": voices, "conversation_mode": vj.get("conversation"),
        "memory": _memory_line(),
        "models": {"endpoint": "homelab LiteLLM" if llm.homelab() else "vector.toml / a local Ollama",
                   "main": llm.model(), "vision": llm.model("vision"), "embeddings": llm.embed_model()},
        "knowledge_spaces": _knowledge_spaces() if "knowledge_search" in SPECS else [],
        "homelab": _homelab_line(),
        "history": f"chat log kept locally; a new session after {history.GAP // 60} quiet minutes",
        "emergency_switch": _switch_line(),
        "nolgia_daily_credit_cap": cap, "build_trial_seconds": build.TRIAL_S,
        "skills": [x["name"] for x in skills.load()],
        "tools": len(SPECS),
        "plugin_tools": {n: LEVEL[n] for n in LEVEL if n in SPECS},
        "plugins": _plugins_line(),
        "not_offered_here": sorted(HIDDEN),
    }


def _switch_line():
    from . import switch
    return (f"OFF, {switch.status_text()[4:].strip(' ()') or 'by the operator'}: only he switches me back on"
            if switch.is_off() else
            f"on (the operator switches me off with {switch.KEY}, the bar, Settings or `bromigos vector off`)")


def _knowledge_spaces():
    H = homelab()
    g = H.source_of("gnosis") if H else None
    return sorted((g.opts.get("spaces") if g else None) or [])


def _homelab_line():
    """What the homelab package connects (its sources and what each provides), or why nothing."""
    H = homelab()
    if not H:
        return "bromigos-homelab isn't installed: no homelab tools"
    st = H.status()
    if not st["sources"]:
        return "no sources in homelab.toml (bromigos setup homelab)"
    return {s["name"]: s["type"] + ": " + ", ".join(s["provides"]) for s in st["sources"]}


def _memory_line():
    from . import memory, plugins
    st = memory._settings().get("backend", "auto")
    names = list(plugins.MEMORY)
    if st == "local" or (st == "auto" and not names):
        return "local (SQLite on this machine, embeddings from the model endpoint; recall capped at 0.2 s)"
    return f"{st if st != 'auto' else names[0]} (a plugin store; recall capped at 0.2 s)"


def _plugins_line():
    from . import plugins
    return {"loaded": list(plugins.LOADED), "errors": [f"{p}: {e}" for p, e in plugins.ERRORS]}


# ------------------------------------------------------------------ the user's profile
PROFILE_FIELDS = {  # field -> (bromigos profile argv words, actions, default action)
    "name": (["set", "name"], ("set", "clear"), "set"),
    "full_name": (["set", "full_name"], ("set", "clear"), "set"),
    "pronouns": (["set", "pronouns"], ("set", "clear"), "set"),
    "address": (["address"], ("prefer", "add", "remove"), "prefer"),
    "alias": (["alias"], ("add", "remove"), "add"),
}


def _profile_memory(field, action, value):
    """The short note filed to memory for a profile change."""
    v = f'"{value}"'
    return {("address", "prefer"): f"Profile: wants to be addressed as {v} first.",
            ("address", "add"): f"Profile: also happy to be addressed as {v} now and then.",
            ("address", "remove"): f"Profile: no longer wants to be addressed as {v}.",
            ("alias", "add"): f"Profile: also goes by {v}.",
            ("alias", "remove"): f"Profile: no longer goes by {v}.",
            }.get((field, action)) or (f"Profile: cleared {field.replace('_', ' ')}." if action == "clear"
                                       else f"Profile: {field.replace('_', ' ')} is {v}.")


def set_profile(field, value="", action="", ui=None):
    """Changes the user's own profile (bromigos profile …) and files a short memory of it.
    Only these fields of ~/.config/bromigos/profile.toml; the CLI validates every value
    (one line, at most 80 characters) and the argv is fixed, never a shell string."""
    from . import persona
    field = (field or "").strip().lower().replace("full name", "full_name").replace("aliases", "alias")
    if field not in PROFILE_FIELDS:
        return {"error": f"field must be one of {', '.join(PROFILE_FIELDS)}"}
    words, actions, default = PROFILE_FIELDS[field]
    action = (action or default).strip().lower()
    if action not in actions:
        return {"error": f"action for {field} must be one of {', '.join(actions)}"}
    value = " ".join(str(value or "").split())
    if action != "clear" and not value:
        return {"error": "value is empty"}
    b = persona.bromigos_bin()
    if not b:
        return {"error": "the bromigos command is not installed (package bromigos-core)"}
    if action == "prefer":           # this form first, the others kept after it
        rest = [x for x in persona.profile()["address"] if x.lower() != value.lower()]
        argv = words + ["set", "--", value] + rest
    elif action == "clear":
        argv = words + ["--", ""]
    elif field in ("address", "alias"):
        argv = words + [action, "--", value]
    else:
        argv = words + ["--", value]
    r = subprocess.run([b, "profile", "--json"] + argv, capture_output=True, text=True, timeout=10,
                       stdin=subprocess.DEVNULL)
    try:
        doc = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        doc = None
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        err = (r.stderr or r.stdout).strip()[:200]
        if "unknown command" in err:
            err = "this bromigos is too old for profiles (update the bromigos-core package)"
        return {"error": err or f"bromigos profile exited {r.returncode}"}
    out = {"ok": True, "you": doc.get("you"), "addressed_as": doc.get("addressed_as")}
    if ui is not None:
        try:
            ui("remember", {"text": _profile_memory(field, action, value), "category": "preference"})
            out["remembered"] = True
        except Exception as e:
            out["remembered"] = f"not filed: {type(e).__name__}"
    return out


def time_now():
    now = dt.datetime.now().astimezone()
    return {"local": now.strftime("%A %d %B %Y, %H:%M:%S %Z"), "iso": now.isoformat(timespec="seconds"),
            "utc": dt.datetime.now(dt.timezone.utc).strftime("%H:%M UTC")}


def calendar_month(offset_months=0):
    now = dt.date.today()
    m = now.month - 1 + int(offset_months)
    y, m = now.year + m // 12, m % 12 + 1
    return {"month": calendar.TextCalendar(calendar.MONDAY).formatmonth(y, m), "today": now.isoformat()}


# ================================================================== registry
def _p(props, req=()):
    return {"type": "object", "properties": props, "required": list(req)}


S = {"type": "string"}
I = {"type": "integer"}
B = {"type": "boolean"}
SPECS = {
    "system_stats": ("This workstation now: CPU, temps, memory, GPU, disks, network, fans.", _p({})),
    "github": ("Read-only GitHub for bromigos-org via gh. action: repos, commits, runs, prs, issues. repo: name under bromigos-org.",
               _p({"action": S, "repo": S, "limit": I}, ["action"])),
    "conversation_history": ("Your past conversations with the host (from the local log). query: words to find "
                             "(e.g. 'gnosis'); when: today, yesterday, last week, a weekday or YYYY-MM-DD. Returns "
                             "session titles, summaries or matching snippets.", _p({"query": S, "when": S, "limit": I})),
    "docs_search": ("Search the lore, AGENTS.md/CLAUDE.md and repo docs (markdown) for a phrase. repo: optional bromigos-org repo name or 'dotfiles'.",
                    _p({"query": S, "repo": S}, ["query"])),
    "docs_read": ("Read part of a doc found by docs_search (path as returned).", _p({"path": S, "start_line": I, "lines": I}, ["path"])),
    "run_shell": ("Run a bash command on the host's workstation as the host's user (full access, no approval; everything is "
                  "logged). For anything the read tools don't cover: git, files, builds, systemctl --user, scripts. Say in one "
                  "short line what you are about to run before anything that changes state. Never sudo; no secrets; no real "
                  "money (those are refused). cwd defaults to ~; timeout_s up to 600.",
                  _p({"command": S, "cwd": S, "timeout_s": I}, ["command"])),
    "shell_off": ("Switch yourself off (the host's emergency switch) when he asks you to stop or to stop using the "
                  "terminal: your commands are stopped and every tool that changes something is refused until he "
                  "switches you back on. You cannot switch it back on.",
                  _p({})),
    "web_search": ("Search the web (the homelab's SearXNG) for current information. Returns title, url and snippet. "
                   "Say where facts came from. category: general (default), it, science or news.", _p({"query": S, "n": I, "category": S}, ["query"])),
    "web_fetch": (f"Fetch a web page's readable text (http/https; no LAN hosts except *.{LAB_DOMAIN}).", _p({"url": S}, ["url"])),
    "herdr_status": ("The host's herdr workspaces: AI coding agents with their status: working, idle, or blocked "
                     "(waiting on the host). For Claude Code sessions use claude_sessions instead: it knows their "
                     "topics, panes and jobs.", _p({})),
    "herdr_read": ("The terminal screen of a herdr pane (target: its id, or a hint like the repo name). Never for what "
                   "Claude Code said or is doing: read its transcript with claude_read or claude_say_latest. Only "
                   "for a pane that isn't Claude Code, or when claude_read finds no transcript.",
                   _p({"agent": S, "lines": I}, ["agent"])),
    "herdr_send": ("Send text to a herdr agent, as if typed (no Enter is added unless the text ends with a newline). "
                   "Say what you are sending first.", _p({"agent": S, "text": S}, ["agent", "text"])),
    "herdr_start": ("Start a new agent in herdr, e.g. a Claude Code session in a repo: name, cwd, argv (default [\"claude\"]).",
                    _p({"name": S, "cwd": S, "argv": {"type": "array", "items": S}}, ["name"])),
    "herdr_wait": ("Wait until a herdr agent reaches a status (idle, working, blocked), up to timeout_s.",
                   _p({"agent": S, "status": S, "timeout_s": I}, ["agent"])),
    "notes_read": ("Read the end of FIELD NOTES, the operator's notepad.", _p({"last_lines": I})),
    "notes_append": ("Append a line to FIELD NOTES (only when the operator asks to note something).", _p({"text": S}, ["text"])),
    "launch": ("Open an app (terminal, browser, chrome, files, discord, spotify, obs, steam, or any installed program by name), a switchboard entry (lab, grafana, argocd, …) or an http(s) URL. A URL opens in a new tab of the host's browser: to play a video, find its URL (web_search), launch the URL, then use media.",
               _p({"target": S}, ["target"])),
    "panel": ("Toggle a desktop widget panel: all, system, network, storage, lab, switchboard, notes, shortcuts.",
              _p({"name": S, "action": S}, ["name"])),
    "wallpaper": ("Switch the den wallpaper: den, empty, masked, v1.", _p({"variant": S}, ["variant"])),
    "scan": ("Run the desktop scanner pass (the hardware schematic sweep).", _p({})),
    "remember": ("Write something durable to your long-term memory (Gnosis): a host preference, a decision, a fact about "
                 "the lab worth keeping, or a recurring problem. One short self-contained sentence. category: host preferences, "
                 "decisions, lab facts or recurring problems.", _p({"text": S, "category": S}, ["text"])),
    "forget": ("Remove something from your own long-term memory when the host asks you to forget it. what: a description "
               "of it, or 'that' for the last thing you filed.", _p({"what": S})),
    "set_profile": ("Change the user's own profile: what you call them and who they are. Call it when they tell you "
                    "('call me Captain', 'my pronouns are she/her', 'I also go by Ace', 'stop calling me that'). field: "
                    "address (a form of address; action prefer = make it the usual one, the default; add = also use it "
                    "now and then; remove), alias (another name they go by; add or remove), name, full_name or pronouns "
                    "(action set, the default, or clear). value: the word or name exactly as they said it. It also "
                    "files the change to your memory. Only for the user's own profile.",
                    _p({"field": S, "value": S, "action": S}, ["field"])),
    "set_voice": ("Pin your voice on the line: auto (you pick per sentence, the default), or one of main, robot, scientist, "
                  "floor, notify. ALWAYS call this when the host asks to switch or change your voice ('switch to your robot "
                  "voice', 'back to normal' = auto); a marker in your reply does not switch it.",
                  _p({"mode": S}, ["mode"])),
    "show_hologram": ("Put a model hologram on your side table: workstation, wick, rack, monolith, emblem; optional part ids to call out.",
                      _p({"model": S, "parts": {"type": "array", "items": S}}, ["model"])),
    "open_gallery": ("Open the full hologram gallery on a model.", _p({"model": S})),
    "k8s_restart": ("Rollout-restart a deployment, statefulset or daemonset and wait until it's Ready again (as vector-operator). "
                    "namespace: the workload's real Kubernetes namespace, usually named after the app (searxng lives in "
                    "namespace searxng); 'the homelab' or 'the lab' is the cluster, never a namespace. If unsure, look it "
                    "up with k8s_get first. Say what you're restarting first.", _p({"namespace": S, "name": S, "kind": S, "wait": B}, ["namespace", "name"])),
    "k8s_scale": ("Scale a deployment or statefulset to replicas (0-20) and wait for it. namespace: the real one, usually "
                  "the app's own name (never 'homelab'); k8s_get first if unsure. Say it first.",
                  _p({"namespace": S, "name": S, "replicas": I, "kind": S}, ["namespace", "name", "replicas"])),
    "k8s_delete_pod": ("Delete one pod (e.g. a stuck one; its controller replaces it). Say it first.",
                       _p({"namespace": S, "pod": S}, ["namespace", "pod"])),
    "k8s_run_job": ("Run a Job now from a CronJob's template (namespace, cronjob); wait=true waits up to 9 minutes.",
                    _p({"namespace": S, "cronjob": S, "wait": B}, ["namespace", "cronjob"])),
    "argocd_sync": ("Sync an Argo CD app now and wait until Synced and Healthy (after a push to homelab master, Argo "
                    "applies it by itself; sync only to hurry it).", _p({"app": S, "wait": B}, ["app"])),
    "argocd_refresh": ("Make Argo CD re-read an app's source (hard=true also re-renders).", _p({"app": S, "hard": B}, ["app"])),
    "argocd_wait": ("Wait until an Argo CD app is Synced and Healthy (up to timeout_s, default 300).",
                    _p({"app": S, "timeout_s": I}, ["app"])),
    "ci_watch": ("Wait for GitHub Actions on a commit (sha) or a branch's latest run, in a repo under bromigos-org, "
                 "nolgiainc or blackflame007 (owner/name, or a bromigos-org name). Returns each run's conclusion.",
                 _p({"repo": S, "sha": S, "branch": S, "timeout_s": I}, ["repo"])),
    "kb_write": ("Add a verified note to the knowledge base (space: bromigos, nolgia, personal, desktop, homelab) "
                 "so it can be found later: how something works, where something lives. Never secrets.",
                 _p({"space": S, "title": S, "text": S}, ["space", "title", "text"])),
    "vault_list": ("Homelab Vault: a folder's entries or a secret's key NAMES (never values). "
                   f"path under {VAULT_ROOT}.",
                   _p({"path": S})),
    "vault_put": ("Store one key in the homelab Vault (patch: other keys untouched). value_from: generate (random; "
                  "length, charset alnum|hex|urlsafe|strong) or operator_prompt (a dialog pops up for the host to "
                  "type or paste it; say so first). You never see the value.",
                  _p({"path": S, "key": S, "value_from": S, "length": I, "charset": S}, ["path", "key", "value_from"])),
    "vault_copy": (f"Copy one Vault key to another path, Vault to Vault: src and dst as {VAULT_ROOT}/<path>#<key>.",
                   _p({"src": S, "dst": S}, ["src", "dst"])),
    "hologram_deck": ("Open or drive a live-layer hologram for the host. One is up at a time: opening one closes the "
                      "last, and open/close wait until you have finished saying what you said before the call, so narrate a "
                      "deck, then open the next. holodeck, timeline, driftmap: open/close only (this machine and "
                      "the lab; the last 72 h; the lore star chart). mind (focus <memory or doc>, "
                      "space <kb-name>, clear: your memory and knowledge as a constellation); ops (focus <repo>, clear: your "
                      "and the host's actions on the lab, pushes, CI, Argo, pods); swarm (point <repo or agent>, focus, clear: "
                      "repos and herdr agents); netmap (trace <host or ip>, clear: the LAN and latency); lab "
                      "(focus <node, gpu, services, storage, deployments, alerts, jobs>, clear: the homelab console, the "
                      "server rack lit by live readings).[[DECKS]] open/close for any. Use it when showing beats telling, or when the host asks to see something.",
                      _p({"deck": S, "verb": S, "args": S}, ["deck"])),
    "app_search": ("Find installed programs by fuzzy name or purpose ('image editor', 'files', 'obs').", _p({"query": S}, ["query"])),
    "launch_app": ("Start any installed program by fuzzy name (its desktop entry); workspace optional (opens there silently).",
                   _p({"name": S, "workspace": S}, ["name"])),
    "run_detached": ("Start a command detached from you (a GUI program or a long job), optionally on a workspace. Same "
                     "limits as your terminal.", _p({"command": S, "workspace": S}, ["command"])),
    "open_path": ("Open a file, folder or URL with its default application.", _p({"target": S}, ["target"])),
    "windows": ("The open windows: address, app, title, workspace, which is focused.", _p({})),
    "media": ("What's playing on the desktop and control it (a YouTube tab, Spotify, mpv): action status (players, "
              "title, position, volume), play, pause, toggle, next, previous, stop, seek (value +10, -30 or an absolute "
              "second), volume (value 0-100, +10, -10). player optional (from status; default the most recent).",
              _p({"action": S, "player": S, "value": S})),
    "window": ("Act on one window (by address, app class or title words): focus, close (say so first), or move to a "
               "workspace.", _p({"action": S, "target": S, "workspace": S}, ["action", "target"])),
    "changes_check": ("Before you say a task is done: lists every repo you touched with changes you left uncommitted or "
                      "unpushed, and changes outside repos not yet recorded in VECTOR-CHANGELOG.md. Clear it first.", _p({})),
    "github_repo_create": ("Create a new GitHub repo: owner bromigos-org (Bromigos), nolgiainc (Nolgia, the company) or "
                           "blackflame007 (personal); ask when unclear. Private unless the host said public. Cloned to "
                           "~/github.com/<owner>/<name>, seeded with README, AGENTS.md and .gitignore, pushed.",
                           _p({"owner": S, "name": S, "description": S, "public": B}, ["owner", "name", "description"])),
    # his clones and pull requests (devflow.py; load the propose-a-change skill first)
    "work_clone": ("Your own working copy of a repo, as your GitHub account bromigodev: clones it into your work folder "
                   "(~/.local/share/bromigos/vector/work/<repo>), or fetches and rebases your branches when it's there. "
                   "repo: vector (your own code), bromigOS, homelab, extensions, dotfiles, or all.", _p({"repo": S})),
    "work_status": ("Your clones: branch, uncommitted files, commits ahead and behind, your open PRs. repo: one or all.",
                    _p({"repo": S})),
    "work_branch": ("Start a branch vector/<name> in your clone from the latest default branch (uncommitted edits come "
                    "along). Make your edits in the clone with run_shell (cwd = the clone), then work_commit.",
                    _p({"repo": S, "name": S}, ["repo", "name"])),
    "work_commit": ("Commit exactly these files (paths relative to the clone) on your branch, with a message in the "
                    "repo's style. Never the default branch; secrets and, in the dotfiles, private details are refused.",
                    _p({"repo": S, "paths": {"type": "array", "items": S}, "message": S}, ["repo", "paths", "message"])),
    "work_push": ("Rebase your branch on the latest default branch and push it (never the default branch).",
                  _p({"repo": S}, ["repo"])),
    "pr_open": ("Open a pull request from your pushed branch into the default branch: a short title, and a plain "
                "description (what changed, why, how you tested it). Says when it's one the operator must merge.",
                _p({"repo": S, "title": S, "body": S}, ["repo", "title", "body"])),
    "pr_watch": ("Wait for CI on a pull request (green, red, or none when the repo runs no checks for it), up to timeout_s.",
                 _p({"repo": S, "number": I, "timeout_s": I}, ["repo", "number"])),
    "pr_merge": ("Merge your own pull request once CI is green (it waits, up to timeout_s). Refused for a PR that touches "
                 "the code behind your limits: that one is left for the operator, and the PR says so. Afterwards, tell "
                 "the operator what arrives where (the result's `arrives`).",
                 _p({"repo": S, "number": I, "timeout_s": I}, ["repo", "number"])),
    "pr_close": ("Close your own pull request without merging, and delete its branch (optional comment).",
                 _p({"repo": S, "number": I, "comment": S}, ["repo", "number"])),
    "nolgia_catalog": ("The host's nolgia models for a modality (image, video, audio, 3d) with credit prices, cheapest first.",
                       _p({"modality": S, "limit": I})),
    "nolgia_credits": ("nolgia credits: the balance, what you've spent today, and the daily cap.", _p({})),
    "nolgia_read": ("Read-only nolgia CLI: 'models get <id>', 'characters list', 'characters get <id>', 'assets list', "
                    "'assets get <id>', 'projects list', 'status <job>', 'wait <job>', 'skills list'.", _p({"command": S}, ["command"])),
    "create": ("Make something on the homelab, for no credits: capability image.generate (prompt), image.edit (prompt + "
               "one input image, optional mask), image.cutout (one input image: a transparent PNG) or model3d.generate "
               "(one input image, ideally a cutout: a GLB mesh). Runs only on the homelab; if it can't (down, or no model "
               "for that), it refuses: tell Sir, and never switch to nolgia on your own. The file lands in "
               "~/Pictures/vector and a notice shows it. inputs: image paths. name: a short name for the file.",
               _p({"capability": S, "prompt": S, "inputs": {"type": "array", "items": S}, "mask": S, "aspect": S,
                   "name": S}, ["capability"])),
    "make_hologram": ("Make a new hologram model of something, on the homelab for no credits: a concept image (from "
                      "subject, or Sir's image), a cutout, then a 3D mesh. It returns build_goal: then call build_start "
                      "with it (the builder bakes the mesh into Sir's collection and puts it up in trial mode). name: "
                      "short, e.g. coffee-mug. Load the make-hologram-model skill first.",
                      _p({"name": S, "subject": S, "image": S}, ["name"])),
    "lab_console": ("The homelab console's status (bromigos lab status): one card per part of the lab (nodes, GPUs "
                    "with VRAM and what holds each, services like ComfyUI and the voice service, models, GitOps apps, "
                    "storage, alerts, the job queue), each with a state and a summary; the cards needing attention list "
                    "their items. card: one card in full (gpus, services, deployments, storage, alerts, jobs, …).",
                    _p({"card": S})),
    "lab_jobs": ("The lab job queue: recent jobs (who queued them, state, why one waits, the result files), or one "
                 "job by id with its log.", _p({"job": S, "limit": I})),
    "lab_job_submit": ("Make something on the homelab's GPUs through its job queue, for no credits: kind image "
                       "(prompt), image-edit (prompt + one input image), cutout (one input image), 3d (one input image, "
                       "ideally a cutout), hologram (prompt: a subject, or one input image: concept, cutout, 3D and a "
                       "baked hologram), voice (text: a spoken line to a WAV), transcribe (one audio file), batch-llm "
                       "(prompt, or an input file of prompts). preset instead of kind: world-painting, hologram, "
                       "voice-line. It waits its turn (one heavy job at a time, VRAM checked, the voice service kept "
                       "free), then lands in ~/Pictures/bromigos/lab/<kind>/ with a notice. wait: seconds to wait for "
                       "it (0-600); otherwise check with lab_jobs. If the lab refuses or can't, tell Sir; never use a "
                       "paid provider on your own.",
                       _p({"kind": S, "preset": S, "prompt": S, "text": S, "inputs": {"type": "array", "items": S},
                           "voice": S, "name": S, "aspect": S, "wait": I})),
    "lab_job_cancel": ("Cancel one of your own lab jobs by id (Sir's jobs are his to cancel).", _p({"job": S}, ["job"])),
    "tv_channels": ("Read den TV channels: their picked shows and items, libraries, YouTube or folders, order, "
                    "bumpers, screens, now and next.", _p({}, [])),
    "tv_channel_add_item": ("On the operator's request, add a show, movie, YouTube URL or music title to a picked "
                    "den TV channel. Library channels already include their library. Music can use music:album:TITLE.",
                    _p({"channel": S, "title": S, "kind": S}, ["channel", "title"])),
    "tv_channel_remove_item": ("Remove one pick from a den TV channel on request. Its media files stay.",
                    _p({"channel": S, "title": S}, ["channel", "title"])),
    "tv_channel_create": ("On request, create a den TV channel of picked shows/items, a library, YouTube or a folder. "
                    "order: shuffle, in-order or alternate. No session restart.",
                    _p({"name": S, "shows": {"type":"array","items":S}, "items": {"type":"array","items":S}, "library": S, "youtube": S, "folder": S,
                        "order": S, "bumpers": B}, ["name"])),
    "tv_tune": ("Tune a named den TV channel on screen big, wave (scope) or emblem (small).",
                    _p({"channel": S, "screen": S}, ["channel"])),
    "tv_channel_delete": ("Remove a whole den TV channel. First returns the plan. confirm=true works only when "
                    "the operator's latest words explicitly request deletion and name that channel. Files stay.",
                    _p({"channel": S, "confirm": B}, ["channel"])),
    "media_search": ("Find a film, TV show, anime or music on the homelab's media stack (Seerr, Sonarr, Radarr, "
                     "Lidarr): year, kind, release dates (unreleased: 'in cinemas 2026-12-18, digital TBA'), the "
                     "quality profile it would get and why, the estimated size, and whether it's already in Jellyfin "
                     "or Plex or requested. kind: movie, tv, anime or music. Use it before media_get when the title "
                     "is unclear.", _p({"query": S, "kind": S, "limit": I}, ["query"])),
    "media_get": ("Request a film, show, anime, artist or album from the homelab at the best quality its profiles allow "
                  "(TV and films through Seerr, music through Lidarr then Soulseek, a YouTube link to Pinchflat), in "
                  "Sir's audio languages. what: a name, an id from media_search (tv:tvdb:76885, movie:tmdb:11299, "
                  "album:<mbid>) or a URL. Without confirm it returns the plan (profile and why, size, free space, "
                  "what's monitored): tell Sir and ask. confirm=true requests it: only on Sir's go-ahead, and a "
                  "clear 'download X' from him counts for a single unambiguous match. If it answers ambiguous, ask "
                  "which. seasons: all (default), latest, future (new episodes only, nothing now), '1,3' or '2-4'; "
                  "episodes: 'S02E05,S03E01'; specials: season 0 too; new: pick up future episodes and seasons "
                  "(false: 'stop getting new episodes of X', with seasons 'none'). A show already in the library "
                  "keeps what it has and adds what's asked ('also get season 4'). quality: best (default), 2160p, "
                  "1080p, 720p. audio: en, ja or any for this request only. when (films): released (default, the "
                  "home release), cinemas or announced: unreleased films are tracked until then.",
                  _p({"what": S, "confirm": B, "kind": S, "quality": S, "audio": S, "seasons": S, "episodes": S,
                      "specials": B, "new": B, "when": S}, ["what"])),
    "media_status": ("What the homelab is downloading for Sir: progress, speed and time left (from qBittorrent and "
                     "SABnzbd), what's stuck or failed and why, Seerr's open requests, the audio and subtitle "
                     "languages; with query, one title in detail: which seasons are monitored or on disk, whether new "
                     "episodes are picked up, subtitles from Bazarr, and when nothing's found, what Prowlarr's indexers "
                     "answer (why=true asks them now).", _p({"query": S, "why": B})),
    "media_upcoming": ("What Sir tracks and when it comes out: new episodes and films' cinema, digital and disc dates "
                       "(Sonarr's and Radarr's calendars). days: how far ahead (90).", _p({"days": I})),
    "media_recent": ("What arrived in the media library lately (imported by Sonarr, Radarr and Lidarr). hours: how far "
                     "back (24).", _p({"hours": I})),
    "media_delete": ("Delete a title from Sir's media library, files and all: a show, a film, an artist, one season, "
                     "some episodes' files or one album. First call without confirm: it returns exactly what goes "
                     "and the space it frees; tell Sir. Call again with confirm=true only when Sir himself, in his "
                     "latest words, tells you to delete that title by name; anything else is refused. exclude: and "
                     "never fetch it again. Never delete on your own initiative, to free space, or for another title.",
                     _p({"what": S, "season": I, "episodes": S, "album": S, "exclude": B, "confirm": B}, ["what"])),
    "media_language": ("Sir's media languages, used for every request: action show, set (audio, best first: ['en', "
                       "'ja'] is English required with Japanese welcome as a second track), anime (anime's own: "
                       "['ja'] for the original with subtitles, ['same'] to follow audio), subtitles (what Bazarr "
                       "fetches) or required (['yes'] or ['no']: the first audio language required or only "
                       "preferred). 'I prefer the Japanese audio with subtitles' for anime is anime ['ja'] and "
                       "subtitles ['en'].", _p({"action": S, "languages": {"type": "array", "items": S}})),
    "nolgia_generate": ("ONLY when Sir explicitly asks for nolgia by name (for anything else, make it on the homelab with "
                        "create). Generate one image, video or audio clip with nolgia. Say the estimated cost first (nolgia_catalog); "
                        "over the daily cap it returns needs_approval: ask the host, and only after he says yes call again "
                        "with approved=true. Images and videos come back with a vision review: check it before presenting. "
                        "out: where the file goes (brand kit, a repo, ~/Pictures); omit for a scratch folder. Report credits left.",
                        _p({"kind": S, "prompt": S, "model": S, "out": S, "input": S, "aspect_ratio": S, "quality": S,
                            "character_id": S, "voice": S, "duration_seconds": I, "project_id": S, "approved": B},
                           ["kind", "prompt"])),
    "nolgia_review": ("Look at an image (or a video's middle frame) with the vision model and answer a question about it.",
                      _p({"path": S, "question": S}, ["path"])),
    "build_start": ("Build something new on the desktop, or change a visualization, in the background: a widget panel, a "
                    "live-layer shader or deck, a hologram model, a change to an existing view. Your builder works in a "
                    "git worktree, validates with offscreen renders and a vision look, then puts it up in trial mode; you "
                    "stay on the line. goal: what to build, with the details the host gave (data, look, placement). Say "
                    "one line that it's started.", _p({"goal": S}, ["goal"])),
    "build_status": ("Where the background build is: phase, files, progress, seconds left in a trial.", _p({})),
    "build_keep": ("The host said keep the trial: commit it in the dotfiles style, merge, push. Only after the host "
                   "answered; never on your own.", _p({})),
    "build_revert": ("The host said revert (or doesn't want it): roll the trial back to exactly what was there.", _p({})),
    "build_stop": ("Stop the background build now (its worktree is removed; a trial is rolled back).", _p({})),
    "load_skill": ("Load one of your skills by name (the catalog is in your instructions) when a task needs its know-how.",
                   _p({"name": S}, ["name"])),
    "my_setup": ("Your own configuration, read live: your models and lanes, the first-byte and silence timeouts, "
                 "speech-to-text and voice engines, your voices, memory, knowledge spaces and sync time, the nolgia "
                 "credit cap, the build trial length, your skills. Call it for any question about how you work.", _p({})),
    "look": ("Look at the host's screen and answer a question about it (read only: you never click or type into "
             "apps). Only when the host asks ('look at this', 'what's on my screen', 'check this error') or to check "
             "your own build; never on your own. target: monitor (default), window (the focused one: 'this'), screen "
             "(every monitor) or a region 'x,y wxh'. Say 'let me take a look' first. Not for what Claude Code "
             "said or is doing: its transcript (claude_read, claude_say_latest) has the exact words.",
             _p({"question": S, "target": S}, ["question"])),
    "read_screen_text": ("Transcribe the exact text on screen (an error, a log, a dialog) when the wording matters; "
                         "target as for look (default: the focused window). Slower than look. Never to read what "
                         "Claude Code said: claude_read and claude_say_latest read its transcript.", _p({"target": S})),
    "active_window": ("What the host is looking at: the focused window's app, title and workspace ('this' in his "
                      "question). No screenshot.", _p({})),
    "watch": ("Watch mode, only when the host turns it on: glance at the focused window every every_s seconds "
              "(10-120) and keep a few lines of notes to follow along (e.g. while debugging); an indicator shows the "
              "whole time and it turns itself off after 15 minutes. mode: on, off or status.",
              _p({"mode": S, "every_s": I}, ["mode"])),
    "snapshot_create": ("Take a snapper snapshot of the system (root and home) before something the host wants to be "
                        "able to undo; description says what. Your terminal already wraps system-level commands in a "
                        "pre/post pair by itself.", _p({"description": S}, ["description"])),
    "snapshot_list": ("Recent snapper snapshots and your own pre/post pairs.", _p({"limit": I})),
    "snapshot_undo": ("Undo your last system-level change (snapper undochange on its pre/post pair), or a given pair "
                      "'home:12..13'. For 'VECTOR, undo that'. A whole-system rollback is the host's (the GRUB "
                      "snapshot menu): explain it, don't do it.", _p({"pair": S})),
    "briefing_now": ("The facts for a brief right now (\"brief me\", \"what did I miss?\"): lab health, Argo problems, "
                     "firing alerts,[[BRIEFING]] CI failures in the last hours. Lead with what's broken.", _p({})),
    "quiet": ("Be quiet: no explained-alert calls and no return briefs for this many minutes (\"be quiet for an hour\" = "
              "60; 0 to speak up again). Answers to the host still work.", _p({"minutes": I}, ["minutes"])),
    "browser_open": ("YOUR OWN browser (a separate Chrome on the desktop that you drive and Sir can watch; never Sir's "
                     "Chrome, his tabs or his sign-ins): open a page, in a new tab by default. For research, reading pages, "
                     "search boxes and multi-step web tasks; to play a video for Sir use launch + media instead. Banking, "
                     "brokerages, crypto, wallets, prediction markets, password managers, Vault, money consoles and LAN "
                     "hosts are refused.", _p({"url": S, "new_tab": B}, ["url"])),
    "browser_tabs": ("Your own browser's tabs: action list (default), switch or close (index from list).",
                     _p({"action": S, "index": I})),
    "browser_read": ("Read the page in your own browser: title, url and its visible text (trimmed to max_chars, default "
                     "6000). login_page in the result means the site wants a login: you don't sign in; tell Sir.",
                     _p({"max_chars": I})),
    "browser_find": ("Find links, buttons and fields on the page in your own browser that match the words in query "
                     "(empty: all of them); each comes with a ref (e1, e2, …) for browser_click and browser_type. Refs "
                     "last until the page changes.", _p({"query": S, "limit": I})),
    "browser_click": ("Click an element in your own browser: ref from browser_find, or its visible text. Refused: "
                      "anything that buys, pays, subscribes or checks out, sign-in buttons, uploads, blocked sites.",
                      _p({"ref": S, "text": S})),
    "browser_type": ("Type into a field in your own browser (ref from browser_find); submit=true presses Enter (e.g. a "
                     "search box). Never passwords, usernames, emails, codes, card or bank details: those fields are "
                     "refused.", _p({"ref": S, "text": S, "submit": B}, ["ref", "text"])),
    "browser_scroll": ("Scroll the page in your own browser: down (default), up, top or bottom.", _p({"direction": S})),
    "browser_back": ("Go back one page in your own browser's current tab.", _p({})),
    "browser_look": ("Look at the page in your own browser (a screenshot of your tab, never Sir's screen) with the "
                     "vision model and answer a question about it: layout, images, charts, anything the text misses.",
                     _p({"question": S})),
    "browser_close": ("Close your own browser (when a web task is finished or Sir asks). It starts again on the next "
                      "browser call.", _p({})),
    "claude_sessions": ("Claude Code's sessions on this workstation, newest first: project, title, when, messages, "
                        "whether it's live now, its herdr pane, what it's doing, whether it's waiting on Sir, and its "
                        "background jobs with their status. 'What is Claude working on?', 'is Claude waiting on me?' "
                        "(waiting_on_you). session: one session's detail and all its jobs. project: filter. "
                        "live_only: only the running ones. Cloud sessions (claude.ai) aren't on this machine.",
                        _p({"project": S, "live_only": B, "session": S, "limit": I})),
    "claude_read": ("Read a Claude Code conversation as plain text (redacted). session: an id, a job's agent id, or "
                    "Sir's words ('the bromigOS one', 'left pane'); empty picks the live one. Default: the latest "
                    "messages, and for a live session what it's doing now and its running jobs. since: a time "
                    "(2h, yesterday, last night, an ISO time) or a cursor ('b123') from next; before: an older "
                    "cursor. include_tools: also the tool calls and the first lines of their results. If the "
                    "answer says ambiguous, ask Sir its one-line question.",
                    _p({"session": S, "since": S, "before": S, "limit": I, "include_tools": B})),
    "claude_search": ("Search Claude Code's transcripts and background jobs for words or a \"phrase\" (redacted "
                      "snippets with session, project and time, newest first): 'find where we decided X', 'what "
                      "did Claude change in the widgets last night'. project, days (default 30), include_tools.",
                      _p({"query": S, "project": S, "days": I, "limit": I, "include_tools": B}, ["query"])),
    "claude_memory": ("Claude Code's memory: the projects with memory, a project's MEMORY.md index and its files "
                      "(project), one file (file), or a word search across them all (query).",
                      _p({"project": S, "file": S, "query": S})),
    "claude_logs": ("Claude Code's logs: kind history (the prompts Sir typed), debug (its debug log, when started "
                    "with --debug) or tasks (background task output and whether it's still running). since: 2h, "
                    "today, an ISO time. session: one session.", _p({"kind": S, "since": S, "session": S, "limit": I})),
    "claude_say_latest": ("Read Claude Code's latest reply to Sir aloud in your voice ('read me what Claude said'): "
                          "speech-friendly, redacted. n: the last n replies. about: 'what did Claude say about X' "
                          "(finds that reply). session as for claude_read. When it says spoken, don't repeat it.",
                          _p({"session": S, "n": I, "about": S})),
    "claude_follow": ("Follow mode: read each new Claude Code reply aloud as its turn ends, until Sir says stop "
                      "('read Claude's replies as they come', 'stop reading Claude'). mode: on, off or status. "
                      "session: one session (as for claude_read) or 'all' (you say which session each reply is from).",
                      _p({"mode": S, "session": S}, ["mode"])),
    "time_now": ("The local date and time.", _p({})),
    "calendar_month": ("A month calendar; offset_months 0 = this month.", _p({"offset_months": I})),
}
FUNCS = {n: globals()[n] for n in SPECS if n in globals()}
CORE = frozenset(SPECS)
LEVEL = {}                                     # plugin tool -> its level (read | desktop)


def _plugin_tools():
    """The user's plugin tools (plugins.py, docs/plugins.md) join the table; a name the core
    already has is refused, never replaced. Their decks join hologram_deck's, and their
    briefing facts briefing_now's description."""
    from . import plugins
    plugins.load()
    for name, d in plugins.DECKS.items():
        DECKS.setdefault(name, set(d["verbs"]))
    own = [f" {n} ({d['about']})" if d["about"] else f" {n}" for n, d in plugins.DECKS.items()]
    desc, params = SPECS["hologram_deck"]
    SPECS["hologram_deck"] = (desc.replace("[[DECKS]]", (" Your own:" + ";".join(own) + ".") if own else ""), params)
    if "briefing_now" in SPECS:
        desc, params = SPECS["briefing_now"]
        facts = "".join(f" {d['about']}," for d in plugins.BRIEFING.values() if d["about"])
        SPECS["briefing_now"] = (desc.replace("[[BRIEFING]]", facts), params)
    off = _homelab_mode() == "off"
    for name, t in plugins.TOOLS.items():
        if off and t["plugin"] == "homelab_tools":
            HIDDEN[name] = "turned off (vector.toml)"
            continue
        if name in CORE:
            plugins.ERRORS.append((t["plugin"], f"tool {name}: that name belongs to VECTOR's core"))
            continue
        SPECS[name] = (t["description"], t["parameters"])
        FUNCS[name] = t["fn"]
        LEVEL[name] = t["level"]
        if t.get("exhibit"):
            EXHIBIT.setdefault(name, t["exhibit"])
        if t["private_args"]:
            PRIVATE_ARGS[name] = tuple(t["private_args"])



# ------------------------------------------------------------------ what this machine can reach
# The homelab's read tools (lab_status, prometheus_query, k8s_*, argocd_apps,
# lab_vault_status, gnosis_search, knowledge_search) are bromigos-homelab's VECTOR plugin
# (homelab_tools.py), offered when homelab.toml provides what each needs. The core's act
# tools for a homelab (argocd sync/refresh/wait, Vault writes, kb_write), herdr, gh
# and nolgia are offered only where their backend is configured: on a machine without them
# the brain never sees them. vector.toml [tools] homelab = "auto" (default) | "off" (hide
# them, and the plugin's, even when configured).
def _needs():
    import shutil
    from . import act, build, nolgia, reach, vault
    from . import tv as tv_mod
    from . import claude as claude_mod
    from . import create as create_mod
    from . import lab as lab_mod
    H = homelab()
    lab = bool(H and H.snapshot_source())
    argo = os.path.exists(act.KUBECONFIG)
    gh = bool(shutil.which("gh"))
    return {
        "briefing_now": lab, "kb_write": bool(act.GNOSIS),
        "argocd_sync": argo, "argocd_refresh": argo,
        "argocd_wait": argo, "vault_list": bool(vault.ADDR), "vault_put": bool(vault.ADDR), "vault_copy": bool(vault.ADDR),
        "github": gh, "ci_watch": gh, "github_repo_create": gh,
        # his clones and PRs: git here, and Vault to hold his account's token (devflow.py)
        **{n: bool(shutil.which("git")) and bool(vault.ADDR) for n in (
            "work_clone", "work_status", "work_branch", "work_commit", "work_push", "pr_open", "pr_watch", "pr_merge",
            "pr_close")},
        **{n: os.path.exists(reach.HERDR) for n in ("herdr_status", "herdr_read", "herdr_send", "herdr_start", "herdr_wait")},
        **{n: os.path.exists(nolgia.NOLGIA) for n in ("nolgia_catalog", "nolgia_credits", "nolgia_read", "nolgia_generate",
                                                      "nolgia_review")},
        # making things on the homelab: create.toml names a homelab ComfyUI, and bromigos (core) is here
        **{n: bool(create_mod.homelab()) and os.path.exists(create_mod._bin()) for n in ("create", "make_hologram")},
        # the lab console and its job queue: bromigos-homelab's `bromigos lab`, with a homelab connected
        **{n: lab_mod.available() and bool(H and H.configured()) for n in ("lab_console", "lab_jobs", "lab_job_submit",
                                                                          "lab_job_cancel")},
        # media requests: bromigos-homelab's `bromigos lab media`, with a Seerr or *arr source
        **{n: lab_mod.available() and _media_lab(H) for n in ("media_search", "media_get", "media_status",
                                                               "media_upcoming", "media_recent", "media_language",
                                                               "media_delete")},
        **{n: tv_mod.available() for n in ('tv_channels','tv_channel_add_item','tv_channel_remove_item',
                                         'tv_channel_create','tv_tune','tv_channel_delete')},
        # Claude Code's records: there is a ~/.claude/projects to read
        **{n: os.path.isdir(os.path.join(claude_mod.root(), "projects")) for n in (
            "claude_sessions", "claude_read", "claude_search", "claude_memory", "claude_logs", "claude_say_latest",
            "claude_follow")},
        # the build loop changes the desktop through a dotfiles repo (build.py): none, no builder
        **{n: os.path.isdir(os.path.join(build.DOT, ".git")) for n in ("build_start", "build_status", "build_keep",
                                                                       "build_revert", "build_stop")},
    }


def _media_lab(H):
    """A Seerr or *arr source in homelab.toml (the media tools' backend)."""
    try:
        return bool(H) and any(s.get("type") in ("seerr", "arr") and s.get("endpoint") for s in H.config.sources())
    except Exception:
        return False


HIDDEN = {}                                    # tool -> why it isn't offered here


def _homelab_mode():
    try:
        import tomllib
        with open(conf("vector.toml"), "rb") as f:
            return str((tomllib.load(f).get("tools") or {}).get("homelab", "auto"))
    except (OSError, ValueError):
        return "auto"


def _gate():
    mode = _homelab_mode()
    for name, ok in _needs().items():
        if name in SPECS and (not ok or mode == "off"):
            HIDDEN[name] = "turned off (vector.toml)" if ok else "its backend isn't configured here"
            SPECS.pop(name, None)
            FUNCS.pop(name, None)


_gate()
_plugin_tools()


def schemas():
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": p}} for n, (d, p) in SPECS.items()]


def _outcome(res):
    """One short line for the event feed (never the whole result)."""
    if isinstance(res, dict):
        for k in ("refused", "error", "ready", "rollout", "all_green", "sync", "exit", "ok"):
            if k in res:
                return f"{k}={str(res[k])[:80]}"
        return f"{len(res)} fields"
    if isinstance(res, list):
        return f"{len(res)} items"
    return f"{len(str(res))} chars"


# ------------------------------------------------------------------ stop after a block
# Once something is refused, held or denied in a turn, the rest of that turn gets no more tools:
# VECTOR reports what was blocked and asks the host, instead of trying the goal another way.
# Only a brain turn arms this (new_turn); other callers (the MCP server) are unaffected.
TURN = {"active": False, "blocked": None}
STOP_NOTE = ("Blocked. Stop working on this goal and run nothing else this turn, not even a read or a check: "
             "don't try it again another way (no other command, language, file path, copy, tool, or check of "
             "permissions or mounts). Tell the host exactly what was blocked and why it might matter, and ask how he "
             "wants to proceed.")
_DENIED = re.compile(r"\bforbidden\b|permission denied|\b403\b|denied request|read-only file system|"
                     r"operation not permitted", re.I)
_SHELL_DENIED = re.compile(r"read-only file system|Error from server \(Forbidden\)|is forbidden:|denied request", re.I)


def new_turn():
    TURN.update(active=True, blocked=None)


def _blocked(name, res):
    """Why this result is a block (a refusal, a hold, a permission denial, the sandbox), or None."""
    if not isinstance(res, dict):
        return None
    for k in ("refused", "held"):
        if res.get(k):
            return str(res[k])[:160]
    if res.get("error") and _DENIED.search(str(res["error"])):
        return str(res["error"])[:160]
    if name in ("run_shell", "run_detached"):
        out = str(res.get("output") or "")
        m = _SHELL_DENIED.search(out)
        if m:
            return m.group(0)
        if "ermission denied" in out:            # a secret file the sandbox masks
            from .shell import masks
            files, dirs = masks()
            for line in out.splitlines():
                if "ermission denied" in line and any(p in line for p in files + dirs):
                    return "a masked secret file: " + line.strip()[:120]
    return None


# ------------------------------------------------------------------ the emergency switch
# While the operator's switch is off (switch.py), only these run: looking things up, and
# stopping. Everything that changes something (the terminal, the desktop, the homelab, Vault,
# memory, the browser, making things, herdr, builds) is refused. A plugin tool runs only at
# level "read".
WHILE_OFF = frozenset({
    "system_stats", "github", "conversation_history", "docs_search", "docs_read", "web_search",
    "herdr_status", "herdr_read", "herdr_wait", "notes_read", "windows", "app_search", "active_window",
    "vault_list", "ci_watch", "argocd_wait", "nolgia_catalog", "nolgia_credits", "nolgia_read", "lab_console",
    "lab_jobs", "build_status", "build_stop", "load_skill", "my_setup", "look", "read_screen_text",
    "snapshot_list", "briefing_now", "time_now", "calendar_month", "quiet", "shell_off", "browser_close",
    "media_search", "media_status", "media_upcoming", "media_recent", "tv_channels",
    "claude_sessions", "claude_read", "claude_search", "claude_memory", "claude_logs", "claude_say_latest",
})


def _switched_off(name):
    from .switch import is_off
    if not is_off():
        return None
    if name in WHILE_OFF or (name not in CORE and LEVEL.get(name) == "read"):
        return None
    from .shell import OFF_NOTE
    return OFF_NOTE + " I can still talk and look things up."


def call(name, args, ui=None, live=None):
    """Run a tool; returns (result_text, exhibit or None). ui handles the UI-only tools."""
    t0 = time.monotonic()
    args = args or {}
    from . import events
    eid = events.next_id()
    events.emit("tool.start", id=eid, name=name, args="(private)" if name in PRIVATE_ARGS else events.summary(args))
    off = _switched_off(name)
    if off:
        _audit(name, args, False, 0, err=PermissionError("switched off"))
        events.emit("tool.end", id=eid, name=name, ok=False, ms=0, outcome="refused: switched off")
        if TURN["active"]:
            TURN["blocked"] = f"{name} was refused (VECTOR is switched off)"
        return json.dumps({"refused": off, "next": STOP_NOTE}), None
    if TURN["active"] and TURN["blocked"]:
        _audit(name, args, False, 0, err=RuntimeError("stopped: blocked earlier this turn"))
        events.emit("tool.end", id=eid, name=name, ok=False, ms=0, outcome="stopped after a block")
        return json.dumps({"stopped": f"not run: earlier in this turn {TURN['blocked']}. " + STOP_NOTE}), None
    try:
        if name in ("show_hologram", "open_gallery", "set_voice", "remember", "forget") or name.startswith("build_"):
            if ui is None:
                raise RuntimeError("no display")
            res = ui(name, args)
        elif name == "hologram_deck" and ui is not None:     # the desktop paces it with his speech
            res = ui(name, args)
        elif name == "system_stats":
            res = system_stats(live)
        elif name == "set_profile":                # fixed keywords: ui is never an argument
            res = set_profile(args.get("field"), args.get("value", ""), args.get("action", ""), ui=ui)
        elif name in FUNCS:
            res = FUNCS[name](**args)
        else:
            raise ValueError(f"unknown tool {name}")
        read = res.pop("_read", None) if isinstance(res, dict) and name.startswith("claude_") else None
        why = _blocked(name, res)
        if why and TURN["active"]:
            TURN["blocked"] = f"{name} was blocked ({why})"
            res = dict(res, next=STOP_NOTE)
        text = _short(res)
        ms = int((time.monotonic() - t0) * 1000)
        _audit(name, args, True, ms, len(text), read=read)
        events.emit("tool.end", id=eid, name=name, ok=True, ms=ms, outcome=_outcome(res))
        if name.startswith("vault_"):
            events.emit("vault.op", op=name[6:], path=args.get("path") or args.get("dst"), key=args.get("key"))
        from .track import note_outside
        note_outside(name, args)
        ex = EXHIBIT.get(name)
        return text, ex
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        _audit(name, args, False, ms, err=e)
        events.emit("tool.end", id=eid, name=name, ok=False, ms=ms, outcome=f"{type(e).__name__}: {str(e)[:100]}")
        out = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
        if TURN["active"] and (isinstance(e, PermissionError) or _DENIED.search(str(e))):
            TURN["blocked"] = f"{name} was denied ({str(e)[:120]})"
            out["next"] = STOP_NOTE
        return json.dumps(out), None
