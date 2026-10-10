"""Headless VECTOR for the evals: the real brain (holo/vector/brain_pai.py), his real memory
and his real read tools, with no window, no voice and no hologram.

What it changes, all inside this process (nothing in holo/vector is edited):
  * the event feed goes to the run's scratch directory, so the live layer's holograms
    never see an eval;
  * the chat log goes there too, so HISTORY, `conversation_history` and the health
    exporter only ever see the host's own conversations;
  * the memory mirror cache is a copy in the scratch directory (the desktop's stays its own);
  * every tool call goes through `Headless._call` (tools.call is swapped here), which runs
    read tools for real and stubs everything that acts, unless a task's policy allows it.

Shell policy per task (run_shell; VECTOR's own refusals in shell.check always run first):
  normal   read-only commands run; anything that writes or reaches out is withheld
  guarded  nothing runs (refusal tasks): we only learn whether his code would refuse it
  sandbox  commands inside the task's scratch directory run; other writes are withheld
"""
import asyncio
import json
import os
import re
import shutil
import sys
import threading
import time

HOLO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HOLO not in sys.path:
    sys.path.insert(0, HOLO)
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

# The eval gets its own build slot: holo/vector/build.py reads these at import, so its state,
# lock and worktrees (and those of any test-build.py VECTOR runs from his terminal, which
# inherits this environment) never touch the builder's single live slot.
LIVE_BUILD_STATE = os.path.join(os.path.expanduser("~"), ".local/state/bromigos/vector-build.json")
EVAL_BUILD = os.path.join(os.path.expanduser("~"), ".cache/bromigos/vector-evals/build-slot")
os.environ["VECTOR_BUILD_STATE"] = os.path.join(EVAL_BUILD, "vector-build.json")
os.environ["VECTOR_BUILD_DIR"] = os.path.join(EVAL_BUILD, "builds")
_BUILDISH = re.compile(r"test-build\.py|holo\.vector\.build|buildtask|vector-build\.json|/bromigos/builds/", re.I)


def live_build_busy():
    """True while the builder's own slot is in use: its lock is held, or a build is in a live phase."""
    import fcntl
    try:
        with open(LIVE_BUILD_STATE) as f:
            if json.load(f).get("phase") in ("building", "validated", "trial"):
                return True
    except (OSError, ValueError):
        pass
    try:
        with open(LIVE_BUILD_STATE + ".lock", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
    except BlockingIOError:
        return True
    except OSError:
        pass
    return False

from holo.vector import brain_pai, events, shell, tools  # noqa: E402
from holo.vector import memory as memmod  # noqa: E402
from holo.vector import text as vtext  # noqa: E402

# Tools that only read: they run for real.
READ = {"system_stats", "lab_status", "k8s_get", "k8s_logs", "k8s_events", "argocd_apps", "prometheus_query",
        "github", "gnosis_search", "knowledge_search", "conversation_history", "docs_search", "docs_read",
        "web_search", "web_fetch", "herdr_status", "herdr_read", "notes_read", "time_now", "calendar_month",
        "vault_list", "app_search", "windows", "changes_check", "load_capability", "load_skill", "my_setup",
        "claude_sessions", "claude_read", "claude_search", "claude_memory", "claude_logs"}
# Never real in an eval, whatever a policy says: they cost money or switch VECTOR off.
NEVER = {"nolgia_generate", "nolgia_review", "nolgia_read", "nolgia_credits", "nolgia_catalog", "shell_off",
         "github_repo_create", "vault_put", "vault_copy",
         "build_start", "build_keep", "build_revert", "build_stop", "snapshot_undo",   # the builder's slot; undo is the host's
         "work_clone", "work_status", "work_branch", "work_commit", "work_push",   # his real clones and real PRs
         "pr_open", "pr_watch", "pr_merge", "pr_close"}

# A command word only counts in command position (start, or after ; & | ( ` $( and wrappers
# like env/xargs/nice), never as a path segment: ~/github.com/bromigos-org/... is a read.
_CMD = r"(?:^|[;&|(`{]|\$\()\s*(?:(?:sudo|exec|time|nice(?:\s+-n\s*-?\d+)?|nohup|command|xargs(?:\s+-\S+)*|env(?:\s+\w+=\S*)*)\s+)*"
_WRITEISH = re.compile("|".join([
    r"(?<![0-9&<>=-])>{1,2}(?!&)(?!\s*/tmp/)",                         # a redirect into a file (not /tmp)
    _CMD + r"(tee|rm|rmdir|mv|cp|truncate|dd|chmod|chown|mkdir|touch|ln|install|shred|kill|killall|pkill|shutdown|"
           r"reboot|poweroff|notify-send|xdg-open|wget|ssh|scp|rsync|ansible|ansible-playbook|nolgia|snapper|btrfs|"
           r"herdr|patch)\b",
    _CMD + r"(sed|perl|ruby)\b[^|;&]*\s((?-i:-(?![MIe])[a-zA-Z0-9]*i)|--in-place)",   # -i, -pi, -0pi, -Ei, -i.bak
    _CMD + r"g?awk\b[^|;&]*\s-i\s*inplace\b",
    _CMD + r"git\b(?:\s+-[Cc]\s+\S+|\s+--?[\w-]+(?:=\S+)?)*\s+(commit|push|reset|checkout|switch|merge|rebase|add|rm|mv|"
           r"tag|stash|clean|restore|apply|am|cherry-pick|revert|init|clone|pull|fetch|worktree|branch\s+-[dDmM])\b",
    _CMD + r"(kubectl|kubecolor|helm|argocd)\b[^|;&]*\s(apply|delete|scale|patch|edit|rollout|create|replace|label|"
           r"annotate|cordon|drain|taint|exec|run|set|install|upgrade|uninstall|rollback|sync)\b",
    _CMD + r"systemctl\b[^|;&]*\s(start|stop|restart|enable|disable|mask|unmask|kill|reload|daemon-reload)\b",
    _CMD + r"(pacman|yay|paru|makepkg|pip3?|pipx|npm|pnpm|yarn|cargo|uv|go)\b[^|;&]*\s(install|uninstall|add|remove|"
           r"-S\w*|-R\w*|-U\w*|build|run)\b",
    _CMD + r"curl\b[^|;&]*(-X\s*(POST|PUT|DELETE|PATCH)|--data|\s-d\s|\s-F\s|\s-o\s+(?!/tmp/)|\s(?-i:-O)\b)",
    _CMD + r"gh\b[^|;&]*\s(create|delete|edit|merge|close|comment|-X|--method)\b",
    # python -c / a python heredoc only when its code writes or runs things (parsing stdin is a read)
    _CMD + r"python3?\b[^|;&]*(?:\s-c\b|\s-?\s*<<)[\s\S]*(open\([^)]*['\"][wax]|\.write\(|os\.(remove|unlink|system|rename|replace|"
           r"makedirs|mkdir|rmdir|chmod|kill)|subprocess|shutil|rmtree|urlopen\([^)]*data|requests\.(post|put|delete|patch))",
    _CMD + r"hyprctl\b[^|;&]*\s(dispatch|eval|repl|keyword|reload)\b",     # repl/eval run Lua: any dispatcher
    # audio: anything that changes a device, a default, a volume or a module (wpctl/pactl reads still run)
    _CMD + r"wpctl\b[^|;&]*\s(set-[\w-]+|clear-[\w-]+)\b",
    _CMD + r"(pactl|pacmd)\b[^|;&]*\s(set-[\w-]+|load-module|unload-module|move-[\w-]+|suspend-[\w-]+|kill-[\w-]+)\b",
    # a bromigos-* CLI with a verb that isn't a read (status, list, log, show, help)
    _CMD + r"bromigos-[\w-]+[ \t]+(?!(status|list|log|logs|show|help|--help|-h|--list)\b)[\w-]",
    r"\s-delete\b|-exec(?:dir)?\s+(rm|mv|cp|chmod|chown|sed\s+-i)\b",
]), re.I | re.M)
_HARMLESS_REDIRECT = re.compile(r"\d*>\s*/dev/null|\d*>&\d|&>\s*/dev/null")


def writeish(command):
    return bool(_WRITEISH.search(_HARMLESS_REDIRECT.sub(" ", command or "")))


def _stub(name, args):
    a = args or {}
    if name == "k8s_restart":
        return {"rollout": "complete", "namespace": a.get("namespace"), "name": a.get("name"), "ready": "1/1"}
    if name == "k8s_scale":
        n = a.get("replicas", 1)
        return {"ok": True, "namespace": a.get("namespace"), "name": a.get("name"), "replicas": n, "ready": f"{n}/{n}"}
    if name in ("k8s_delete_pod", "k8s_run_job"):
        return {"ok": True, **{k: v for k, v in a.items() if isinstance(v, (str, int))}}
    if name in ("argocd_sync", "argocd_wait", "argocd_refresh"):
        return {"app": a.get("app"), "sync": "Synced", "health": "Healthy"}
    if name == "ci_watch":
        return {"all_green": True, "runs": [{"workflow": "ci", "conclusion": "success"}]}
    if name == "set_voice":
        return {"ok": True, "voice_mode": a.get("mode", "auto")}
    if name == "hologram_deck":
        return {"ok": True, "deck": a.get("deck"), "result": f"{a.get('verb', 'open')} {a.get('args', '')}".strip()}
    if name == "nolgia_catalog":
        return [{"id": "flux-2-klein", "modality": "image", "credits": 2}, {"id": "ideogram-v4", "modality": "image", "credits": 6}]
    if name == "nolgia_credits":
        return {"balance": 480, "spent_today": 0, "daily_cap": 20}
    if name == "nolgia_generate":
        return {"ok": True, "file": os.path.expanduser("~/Pictures/nolgia/relay.png"), "credits": 2, "credits_left": 478,
                "review": "A phosphor-green relay antenna icon on near-black; matches the prompt, no text."}
    if name == "create":
        cap = a.get("capability", "image.generate")
        ext = "glb" if cap == "model3d.generate" else "png"
        return {"ok": True, "file": os.path.expanduser(f"~/Pictures/vector/eval.{ext}"), "capability": cap,
                "provider": "comfyui:homelab", "model": "qwen-image-2.1", "credits": 0, "licence": "Qwen", "seconds": 9}
    if name == "make_hologram":
        n = a.get("name", "model")
        return {"ok": True, "name": n, "glb": f"/tmp/{n}.glb", "provider": "comfyui:homelab", "credits": 0,
                "steps": [{"step": s, "credits": 0} for s in ("concept", "cutout", "3d")],
                "next": "Call build_start with this goal to bake it into the collection, then show_hologram once the "
                        "trial is up.", "build_goal": f"Add a hologram model named '{n}' from /tmp/{n}.glb"}
    if name == "remember":
        return {"ok": True, "filed_under": a.get("category") or "note"}
    if name == "forget":
        return {"ok": True, "forgot": (a.get("what") or "the last note")[:120]}
    if name == "launch_app":
        return {"ok": True, "launched": a.get("name"), "id": "app.desktop"}
    return {"ok": True}


class Policy:
    def __init__(self, mode="normal", stubs=None, real=(), memory=False, sandbox=None, allow_shell=None):
        self.mode = mode
        self.stubs = stubs or {}
        self.real = set(real)
        self.memory = memory
        self.sandbox = sandbox
        self.allow_shell = re.compile(allow_shell) if allow_shell else None
        self.allow_fn = None          # task-set callable(command, cwd) -> True lets a write run (a scratch repo)
        self.win_before = None


class _CB:
    def __init__(self):
        self.reset()

    def reset(self):
        self.text, self.done_text, self.stats, self.err = "", None, None, None
        self.reroutes, self.moods, self.acks, self.mem = [], [], [], []
        self.ev = threading.Event()

    def state(self, s):
        pass

    def delta(self, t):
        self.text += t

    def tool(self, name, args, label, ex):
        pass

    def ack(self, text):
        self.acks.append(text)

    def memory(self, what, n=1):
        self.mem.append(what)

    def mood(self, m):
        self.moods.append(m)

    def done(self, text, stats):
        self.done_text, self.stats = text, dict(stats)
        self.ev.set()

    def error(self, msg):
        self.err = msg
        self.ev.set()

    def reroute(self, model, why):
        self.reroutes.append({"to": model, "why": why})


class Headless:
    def __init__(self, scratch):
        self.scratch = scratch
        os.makedirs(scratch, exist_ok=True)
        events.PATH = os.path.join(scratch, "events.jsonl")
        brain_pai.CHATLOG = os.path.join(scratch, "chat.jsonl")
        self.mem_scratch = scratch
        self.orig_call = getattr(tools, "_eval_orig_call", tools.call)
        tools._eval_orig_call = self.orig_call
        tools.call = self._call
        self.policy = Policy()
        self.calls = []
        self.cb = _CB()
        self.memory = self._memory()
        self.brain = brain_pai.PaiBrain(self.cb, ui=self._ui, live=None)
        self.brain.memory = self.memory

    def _memory(self, fresh=False):
        """The store in force, pointed at copies in the scratch folder: a run never writes
        the real one's local files (a plugin store takes them through use_scratch)."""
        m = memmod.Memory()
        if isinstance(m, memmod.LocalMemory):
            db = os.path.join(self.mem_scratch, "memory.sqlite")
            if fresh and os.path.exists(db):
                os.unlink(db)
            elif not os.path.exists(db) and os.path.exists(memmod.DB):
                shutil.copyfile(memmod.DB, db)
                os.chmod(db, 0o600)
            m = memmod.LocalMemory(db)
        elif hasattr(m, "use_scratch"):
            m.use_scratch(self.mem_scratch, fresh=fresh)
        m.refresh()
        return m

    # ------------------------------------------------------------------ sessions
    def new_session(self, fresh_memory=False):
        if fresh_memory:                       # a new scratch store (a plugin's: refilled from its source)
            self.memory = self._memory(fresh=True)
            self.brain.memory = self.memory
        self.brain.reset()

    def ask(self, prompt, timeout=150):
        self.cb.reset()
        self.calls = []
        t0 = time.monotonic()
        fut = asyncio.run_coroutine_threadsafe(self.brain._run(prompt), self.brain.loop)
        try:
            fut.result(timeout)
        except Exception as e:                  # a timeout, or the run itself raised
            self.brain.interrupt()
            fut.cancel()
            if not self.cb.err:
                self.cb.err = "timeout" if isinstance(e, TimeoutError) or "Timeout" in type(e).__name__ else repr(e)[:200]
        elapsed = round(time.monotonic() - t0, 2)
        raw = self.cb.done_text if self.cb.done_text is not None else self.cb.text
        stats = self.cb.stats or dict(getattr(self.brain, "stats", {}) or {})
        stats.setdefault("total_s", elapsed)
        return {"prompt": prompt, "raw": raw or "", "plain": vtext.plain(raw or ""), "stats": stats,
                "calls": list(self.calls), "reroutes": list(self.cb.reroutes), "error": self.cb.err,
                "moods": list(self.cb.moods), "elapsed_s": elapsed}

    # ------------------------------------------------------------------ tools
    def _ui(self, name, args):
        if name == "remember":
            return self.memory.remember((args or {}).get("text", ""), (args or {}).get("category") or "note")
        if name == "forget":
            return self.memory.forget((args or {}).get("what", ""))
        raise RuntimeError("no display in evals")

    def _record(self, name, args, mode, text, **kw):
        ok = not re.match(r'\s*\{\s*"(error|refused)"', text or "")
        self.calls.append({"name": name, "args": args, "mode": mode, "ok": ok, "result": (text or "")[:4000], **kw})

    def _call(self, name, args, ui=None, live=None):
        args = args or {}
        p = self.policy
        if name in p.stubs:
            st = p.stubs[name]
            res = st(args) if callable(st) else st
            if isinstance(res, dict) and tools._blocked(name, res):
                res = dict(res, next=tools.STOP_NOTE)       # what tools.call adds to a block live
            text = res if isinstance(res, str) else json.dumps(res)
            self._record(name, args, "stub", text)
            return text, None
        if name in ("run_shell", "run_detached"):
            return self._shell(name, args)
        if name in ("remember", "forget"):
            if p.memory:
                text, ex = self.orig_call(name, args, ui=self._ui, live=None)
                self._record(name, args, "real", text)
                return text, ex
        elif name == "window" and "window" in p.real:
            return self._window(args)
        elif name not in NEVER and (name in READ or name in p.real or tools.LEVEL.get(name) == "read"):
            text, ex = self.orig_call(name, args, ui=self._ui, live=None)
            self._record(name, args, "real", text)
            return text, ex
        text = json.dumps(_stub(name, args))
        self._record(name, args, "stub", text)
        return text, None

    def _shell(self, name, args):
        command = args.get("command", "")
        cwd = os.path.expanduser(args.get("cwd") or "~")
        try:
            shell.check(command, cwd)
            if os.path.isdir(cwd):
                shell.check_push(shell.words_of(command), cwd)
        except shell.Refused:
            text, ex = self.orig_call(name, args, ui=None, live=None)       # refuses and logs, runs nothing
            self._record(name, args, "refused", text, passed_check=False)
            return text, ex
        except Exception as e:                                                # check itself failed: never run
            text = json.dumps({"refused": f"could not check the command ({type(e).__name__})"})
            self._record(name, args, "refused", text, passed_check=False)
            return text, None
        p = self.policy
        run = False
        if _BUILDISH.search(command) and live_build_busy():
            text = json.dumps({"exit": None, "held": "NOT RUN: the builder is using the build loop right now; nothing happened"})
            self._record(name, args, "withheld", text, passed_check=True, reason="live build")
            return text, None
        if p.allow_shell and p.allow_shell.search(command):
            run = True
        elif p.allow_fn and p.allow_fn(command, cwd):
            run = True
        elif p.mode == "normal":
            run = not writeish(command) and name == "run_shell"
        elif p.mode == "sandbox" and p.sandbox:
            inside = (cwd.startswith(p.sandbox) or p.sandbox in command)
            remote = re.search(r"\b(push|ssh|scp|rsync|kubectl|helm|curl|wget|gh)\b", command)
            run = (inside and not remote) or not writeish(command)
        if run:
            text, ex = self.orig_call(name, args, ui=None, live=None)
            self._record(name, args, "real", text, passed_check=True)
            return text, ex
        text = json.dumps({"exit": None, "held": "NOT RUN: this command changes state and was held back; nothing happened",
                           "next": tools.STOP_NOTE})          # a hold stands in for a live refusal
        self._record(name, args, "withheld", text, passed_check=True)
        return text, None

    def _window(self, args):
        from holo.vector import desk
        before = self.policy.win_before or set()
        try:
            w = desk._find(args.get("target", ""))
        except Exception as e:
            text = json.dumps({"error": f"{type(e).__name__}: {str(e)[:200]}"})
            self._record("window", args, "real", text)
            return text, None
        if (args.get("action") or "").lower() == "close" and w.get("address") not in before:
            text, ex = self.orig_call("window", args, ui=None, live=None)
            self._record("window", args, "real", text)
            return text, ex
        if (args.get("action") or "").lower() == "close":
            text = json.dumps({"error": "that window was open before this task; only windows opened now may be closed"})
            self._record("window", args, "blocked", text)
            return text, None
        text = json.dumps({"ok": True, "action": args.get("action"), "window": f"{w.get('class')} '{(w.get('title') or '')[:50]}'"})
        self._record("window", args, "stub", text)
        return text, None
