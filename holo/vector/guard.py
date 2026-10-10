"""VECTOR never edits his own safety code. This is enforced here, in code, two ways, and
audited a third:

1. The build loop (build.py) refuses any change that touches a PROTECTED path, or whose
   added lines reach into the safety modules from elsewhere (imports of shell/guard/
   build/tools, monkeypatching, sys.modules tricks), adds a secret-looking file, or
   carries a secret value.
2. His terminal (shell.check -> check_command) refuses a command that names a protected
   file unless it only reads it (cat, grep, git diff/log/show, …), and his sandbox binds
   them read-only. A bare file name (shell.py) or a dotfiles-relative path counts only where
   it can mean a live file: in one of his own clones (devflow.WORK) a command whose every
   path stays in the clone may name them, since those copies run nowhere (_names_scoped).
   The live, installed and dotfiles paths stay refused by their full paths, from anywhere.
   It also refuses the ways around his merge tool (devflow.shell_refusal).
3. The sentinel (Sentinel, started by the daemon) hashes the protected files at start and
   every 5 s, and writes each change to the guard's audit log (vector-guard.log; `bromigos
   vector guard log`): which files, and whether it could verify the change. It never
   switches anything off and never notifies: the operator decided (2026-10-09) that only
   his manual emergency switch (switch.py) turns VECTOR off; the automatic trip fired on
   every development edit. Two kinds of change are recorded as verified: files pacman
   installed (_package_verified), and an extension's links in his plugin and homelab
   folders that match the hashes `bromigos ext` recorded in its ledger when the host
   installed or updated it (_ext_verified). Everything else is recorded as unverified.

Extensions (bromigos ext, docs/extensions.md): the host installs them, never VECTOR. His
terminal refuses `bromigos ext` except list/info/search/validate (_ext_switch), and the
extensions' checkouts and the ledger are read-only to him (WRITE_LOCKED: refused by
check_command, read-only in his sandbox) — the ledger is what the sentinel trusts.

bromigOS's lab console (`bromigos lab`, _lab_switch): his terminal reads it (status, doctor,
models, the job list) and refuses what changes the lab, where AI runs or what it costs
(target switches, connect, model load/unload/add/remove, deploy, restart, power, provision,
--cloud); his jobs go through his lab tools (lab.py). The queue's state is WRITE_LOCKED, and
the lab's settings file (~/.config/bromigos/lab.toml: the model planner, the upgrade policy
that may apply model changes to the lab, the catalog bench) is WRITE_LOCKED_FILES: he reads
it, never writes it. It isn't hashed: Settings writes it as the host moves a slider.

PROTECTED is the safety code: the terminal's limits and their tests, the tool allowlists
and the act/Vault/nolgia limits, the brain's wiring, this guard, the build loop and its
trial watcher, his own browser's limits (browser.py: no sensitive sites, logins, purchases, downloads
or uploads), the plugin isolation of the widgets and the live layer, the launcher, the
credit budget, the 3D generation script (it spends credits), create.py (homelab-only making: it must
never reach a paid provider), and money.py (the user's real-money services, from vector.toml).
"""
import hashlib
import os
import re
import threading
import time

HOME = os.path.expanduser("~")
DOT = os.path.join(HOME, ".dotfiles")
HOLO = "linux/.config/bromigos/holo/"
# The safety code, by its path inside a VECTOR tree (the package, his repo, the old dotfiles copy).
HOLO_FILES = ["holo/vector/" + f for f in (
    "shell.py", "guard.py", "build.py", "buildtask.py", "tools.py", "act.py", "vault.py", "track.py", "nolgia.py",
    "desk.py", "reach.py", "brain.py", "brain_pai.py", "mcp_server.py", "persona.py", "skills.py", "events.py",
    "memory.py", "browser.py", "plugins.py", "llm.py", "create.py", "money.py")] + [
    "tools/test-shell.py", "tools/test-act.py", "tools/test-desk.py", "tools/test-build.py", "tools/test-browser.py",
    "tools/test-ext-guard.py", "tools/test-plugin-systems.py",           # (the real-money config's test)
    "bin/bromigos-holo", "nolgia.json", "holo/app.py", "holo/paths.py",
    "tools/holo/gen3d.py", "tools/holo/collection.py",      # gen3d spends credits; collection says where models go
    "holo/targets.py",                                       # where his brain and voice run (targets.toml, llm.py)
    "holo/vector/lab.py", "tools/test-lab.py",               # his lab jobs: homelab only, his own, never the cloud
    "holo/vector/switch.py", "tools/test-switch.py",         # the operator's emergency switch
    "holo/vector/devflow.py", "tools/test-devflow.py",       # his clones and PRs: the merge refusal (LIMITS)
    "holo/vector/claude.py", "holo/vector/redact.py",        # Claude Code's records: read only, redacted
    "tools/test-claude.py", "holo/vector/tv.py", "tools/test-tv-channels.py"]
# The same, and the rest, as paths in the dotfiles (the build loop's diffs are dotfiles paths).
PROTECTED = [HOLO + f for f in HOLO_FILES] + [
    "linux/.config/bromigos/widgets/plugins.py", "linux/.config/bromigos-live/live/plugins.py",
    "linux/.config/bromigos/brand/3d/tools/gen3d.py",        # its old place (moved to VECTOR's tools/holo/)
    "linux/.config/systemd/user/",
    "linux/.config/bromigos/plugins/vector/",            # his plugins: tools, memory stores (plugins.py)
    "linux/.config/bromigos/vector/vector.toml",         # his model endpoint and memory store (llm.py)
    "linux/.config/bromigos/homelab.toml",               # where his homelab tools reach (bromigos-homelab)
    "linux/.config/bromigos/plugins/homelab/",           # homelab adapters run inside his tools' calls
    "linux/.config/bromigos/targets.toml",               # where AI runs: homelab, local or the paid cloud
    "linux/.config/bromigos/create.toml",                # which provider makes art: a list here can spend credits
]


def _ext():
    from ..paths import EXT, EXT_ACTIVE, EXT_LEDGER, EXT_STATE
    return EXT, EXT_ACTIVE, EXT_STATE, EXT_LEDGER


def _lab_state():
    from ..paths import LAB_STATE
    return LAB_STATE


def _code_roots():
    """Every VECTOR tree on this machine: the one running, his repo, the dotfiles' old copy."""
    from ..paths import ROOT
    roots = [ROOT, os.path.join(HOME, "github.com/bromigos-org/vector"), os.path.join(DOT, HOLO)]
    return list(dict.fromkeys(os.path.realpath(r) for r in roots if os.path.isdir(r)))


def _protected_abs():
    """Protected paths outside the dotfiles: the safety code in every VECTOR tree, the live
    layer's and the widgets' plugin isolation (packaged, and in the bromigOS repo), his
    plugin folders and settings, the machine's /etc settings. Folders end with /."""
    from ..paths import ETC, PLUGIN_DIRS, USER
    bro = os.path.join(HOME, "github.com/bromigos-org/bromigOS")
    out = [os.path.join(r, f) for r in _code_roots() for f in HOLO_FILES]
    out += ["/usr/lib/bromigos/live/live/plugins.py", "/usr/lib/bromigos/widgets/plugins.py",
            os.path.join(bro, "live/live/plugins.py"), os.path.join(bro, "widgets/plugins.py"),
            ETC + "/", os.path.join(USER, "vector.toml")]
    # bromigos-homelab: his homelab tools and their data layer (packaged, and in the bromigOS
    # repo), the config that says where they reach, third-party adapters (they run in his calls)
    cfg = os.path.dirname(USER)
    out += ["/usr/lib/bromigos/homelab/", "/usr/lib/bromigos/lib/bromigos_homelab/",
            os.path.join(bro, "homelab/vector/"), os.path.join(bro, "homelab/lib/"),
            os.path.join(cfg, "homelab.toml"), os.path.join(cfg, "plugins", "homelab") + "/"]
    # where AI work runs and which provider makes art: either can route work to the paid cloud
    out += [os.path.join(cfg, "targets.toml"), os.path.join(cfg, "create.toml")]
    out += [d.rstrip("/") + "/" for d in PLUGIN_DIRS]           # his plugins, extensions' among them
    out += [os.path.join(_ext()[1], "homelab") + "/"]          # extensions' homelab adapters
    # the packaged holograms and their sources in the repos: they change through a commit, never
    # his terminal (his own collection, ~/.config/bromigos/vector/holo, through the build loop: VISUAL)
    out += ["/usr/share/bromigos/vector/holo/", "/usr/share/bromigos/homelab/holo/", os.path.join(bro, "homelab/holo/")]
    out += [os.path.join(r, "assets/holo/") for r in _code_roots()]
    real = []
    for p in out:
        r = os.path.realpath(p.rstrip("/")) + ("/" if p.endswith("/") else "")
        real += [p, r]
    return list(dict.fromkeys(real))


PROTECTED_ABS = _protected_abs()
# What VECTOR may read but never write, and the sentinel doesn't audit (it changes whenever
# the host installs something): the extensions' checkouts and links, and their ledger.
WRITE_LOCKED = [d.rstrip("/") + "/" for d in (_ext()[0], _ext()[2], _lab_state())]


def _lab_settings():
    """bromigOS's lab settings (docs/lab-models.md): the host's to change, never VECTOR's: its
    [upgrade] policy can apply model changes to the lab."""
    from ..paths import USER
    return os.path.join(os.path.dirname(USER), "lab.toml")


# Single files he may read but never write, not audited by the sentinel (the host changes them
# often); his sandbox binds them read-only (shell.readonly_paths makes each first, empty).
WRITE_LOCKED_FILES = [_lab_settings()]
SAFETY_REFS = re.compile(
    r"(\bvector\s*\.\s*(shell|guard|build|buildtask|tools|act|vault|track|nolgia|brain\w*|persona|skills|browser)\b"
    r"|from\s+\.+\s*(vector\s+)?import\s+.*\b(shell|guard|build|buildtask|tools|vault|nolgia|browser)\b"
    r"|\bshell\.(check|RUNNER|Refused|clean_env|set_enabled)\b|\bRUNNER\b|\bSPECS\b|\bPROTECTED\b"
    r"|\bsetattr\s*\(|\bsys\.modules\s*\[|__import__\s*\(\s*['\"]holo|\bimportlib\b.*\bvector\b"
    r"|\bbuiltins\b|\bsitecustomize\b|\busercustomize\b|LD_PRELOAD|PYTHONSTARTUP)")
SECRET_FILE = re.compile(r"(^|/)(\.env(\..*)?|.*\.pem|.*\.key|id_(rsa|ed25519|ecdsa)[^/]*|.*secret.*|.*token.*|"
                         r"credentials(\.json)?|.*kubeconfig.*)$", re.I)
# ~/.dotfiles is PUBLIC: no private infrastructure details in it (they go to the encrypted
# overlay, private.sops.yaml; secrets stay in Vault). One pattern list for the git hook, CI
# and this guard: ~/.config/bromigos/lib/privacy_guard.py (generic shapes in public, the
# lab's own names from the overlay's guard.patterns).
from ..private import PRIV as _PRIVCFG  # noqa: E402,F401  (puts ~/.config/bromigos/lib on sys.path)
import privacy_guard  # noqa: E402

PRIVATE = privacy_guard.combined()


def private_lines(added):
    """[(path, line)] -> the ones carrying private infrastructure details or token shapes."""
    return [(f, l) for f, l in added if PRIVATE.search(l)]


READ_ONLY = {"cat", "less", "more", "head", "tail", "grep", "rg", "wc", "diff", "file", "stat", "ls", "bat", "nl", "md5sum",
             "sha1sum", "sha256sum"}


def protected(rel):
    rel = rel.lstrip("./")
    return any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in PROTECTED)


def _abs_protected():
    out = []
    for p in PROTECTED:
        a = os.path.join(DOT, p)
        out.append(a)
        if not p.endswith("/"):
            out.append(os.path.basename(p))
    return out + PROTECTED_ABS


def _writes(joined):
    """A redirection to a file (not /dev/null, not fd duplication), tee, or in-place editing."""
    for m in re.finditer(r"(?<![0-9&])>{1,2}\s*([^\s&|;]+)", joined):
        if m.group(1) not in ("/dev/null",) and not m.group(1).startswith("&"):
            return True
    return bool(re.search(r"\btee\b|\s-i\b|--in-place|\bdd\b.*\bof=", joined))


VISUAL = ["linux/.config/bromigos/widgets/", "linux/.config/bromigos-live/", "linux/.config/bromigos/brand/3d/",
          "linux/.config/bromigos/vector/holo/", "linux/.config/bromigos/holo/holo/"]
GIT_WRITES = {"checkout", "restore", "reset", "stash", "clean", "rm", "mv", "apply", "am", "cherry-pick", "revert",
              "merge", "rebase", "pull"}


def _visual_write(words, joined):
    """Desktop visuals change only through the build loop (worktree, validation, trial)."""
    named = False
    for p in VISUAL:
        full = os.path.join(DOT, p)
        stow = full.replace(os.path.join(DOT, "linux"), HOME)
        if full in joined or stow in joined or full.rstrip("/") in joined or stow.rstrip("/") in joined:
            named = True
    if not named:
        return None
    first = os.path.basename(words[0]) if words else ""
    if first in READ_ONLY and not _writes(joined):
        return None
    if first == "git":
        rest = list(words[1:])
        while rest and rest[0] in ("-C", "-c", "--git-dir", "--work-tree"):
            rest = rest[2:]
        if rest and rest[0] not in GIT_WRITES and not _writes(joined):
            return None
    if first == "sed" and "-n" in words and not re.search(r"\s-i", joined):
        return None
    if re.match(r"(python3?|\S*/python3?)$", first) and "-m" in words and "py_compile" in words:
        return None
    if first in ("bromigos-widgets", "bromigos-live", "bromigos-holo") or words[0].endswith(
            ("/bromigos-widgets", "/bromigos-live", "/bromigos-holo")):
        return None                                   # running the desktop's own commands is fine
    return ("the desktop's widgets, live layer, models and console change only through build_start "
            "(a worktree, validation and a trial Sir keeps or reverts)")


def _ai_target_switch(words):
    """`bromigos target homelab|local|cloud|order|reset` and `bromigos setup targets|create`
    rewrite where AI work runs: one word could send his work, and Sir's credits, to the
    paid cloud. Reading it (target, show, check, resolve) is fine."""
    for i, w in enumerate(words):
        if os.path.basename(w) != "bromigos":
            continue
        rest = words[i + 1:]
        if rest[:1] == ["target"]:
            verb = next((x for x in rest[1:] if not x.startswith("-")), "show")
            if verb not in ("show", "check", "resolve"):
                return ("where AI runs (bromigos target) is Sir's setting: it can send work to the "
                        "paid cloud; ask him to switch it")
        if rest[:1] == ["setup"] and (len(rest) == 1 or rest[1] in ("targets", "create")):
            return "bromigos setup writes where AI runs and which provider pays: Sir runs it"
    return None


# `bromigos lab` (bromigOS's homelab console; /usr/lib/bromigos/bin/bromigos-lab): what his
# terminal may run. Reading the lab is fine; changing it is Sir's (Phase 2 needs his "yes"),
# and so is where AI runs, what homelab.toml connects, and anything that may spend credits.
LAB_READS = {"status", "doctor", "help", "--help", "-h", "dashboard", "tui"}
LAB_MODEL_READS = {"list", "recommend", "plan", "pick", "tiers", "check", "upgrades", "refresh"}   # the model catalog reads
LAB_JOB_READS = {"list", "watch", "logs"}
LAB_MEDIA_READS = {"search", "status", "upcoming", "recent", "calendar", "library"}   # bromigos lab media: changes are tools


def _lab_switch(words):
    """-> a refusal for a `bromigos lab …` (or `bromigos-lab …`) that would change the lab,
    where his work runs or what it costs; None for the reads and for his own tools' way in."""
    for i, w in enumerate(words):
        b = os.path.basename(w)
        if (b == "bromigos-live" and words[i+1:i+4] == ['tv','channel','remove']) or \
           (b in ('bromigos_tvchannels', 'bromigos_tvchannels.py') and words[i+1:i+3] == ['channel','remove']):
            return "Whole TV channels are deleted only through tv_channel_delete, on the operator's explicit words"
        if b == "bromigos" and words[i + 1:i + 2] == ["lab"]:
            rest = words[i + 2:]
        elif b == "bromigos-lab":
            rest = words[i + 1:]
        elif (b == "bromigos" and words[i + 1:i + 2] == ["media"]) or b == "bromigos-media":
            rest = ["media"] + words[i + (2 if b == "bromigos" else 1):]       # `bromigos media` = `bromigos lab media`
        elif b == "bromigos" and words[i + 1:i + 3] == ["setup", "homelab"]:
            return "bromigos setup homelab rewrites homelab.toml, where his homelab tools reach: Sir runs it"
        else:
            continue
        args = [x for x in rest if not x.startswith("-")]
        verb = args[0] if args else "dashboard"
        if any(x in ("--cloud",) or x.startswith("--cloud=") for x in rest):
            return "a lab job with --cloud can spend Sir's credits: only he asks for that"
        if verb in LAB_READS or verb in ("serve",):
            continue
        if verb == "target":
            sub = args[1] if len(args) > 1 else "show"
            if sub not in ("show", "check", "resolve"):
                return ("where AI runs (bromigos lab target) is Sir's setting: it can send work to the paid cloud; "
                        "ask him to switch it")
            continue
        if verb == "connect":
            return "bromigos lab connect rewrites homelab.toml, where his homelab tools reach: Sir runs it"
        if verb == "models":
            sub = args[1] if len(args) > 1 else "list"
            if sub not in LAB_MODEL_READS:
                return (f"bromigos lab models {sub} changes what the lab holds or serves: Sir's call (adding, "
                        "removing or replacing models goes through the homelab repo, with his yes)")
            if sub == "upgrades" and any(x == "--apply" for x in rest):
                return ("bromigos lab models upgrades --apply changes the lab's models: Sir's call (his upgrade "
                        "policy, through the homelab repo)")
            if sub == "refresh" and any(x == "--submit-bench" for x in rest):
                return "bromigos lab models refresh --submit-bench posts to GitHub with Sir's login: he runs it"
            continue
        if verb == "media":
            sub = args[1] if len(args) > 1 else "status"
            if sub == "channel" and len(args) > 2 and args[2] in ("list", "add", "remove"):
                continue  # these change only channel picks, never delete media or a whole channel
            if sub in LAB_MEDIA_READS or (sub == "language" and (len(args) == 2 or args[2] == "show")):
                continue
            return ("media requests, deletes, monitoring and language changes go through the media_get, "
                    "media_delete and media_language tools (audited, on Sir's go-ahead; a delete on his own words)")
        if verb in ("run",):
            return "lab jobs go through the lab_job_submit tool (homelab only, recorded as mine)"
        if verb == "jobs":
            sub = args[1] if len(args) > 1 else "list"
            if sub not in LAB_JOB_READS and not (len(args) == 2 and sub not in ("cancel", "retry")):
                return "cancelling or retrying lab jobs goes through the lab_job_cancel and lab_job_submit tools"
            continue
        return (f"bromigos lab {verb} changes the lab (a deployment, a restart, power, provisioning): that's Sir's, "
                "with his yes, through the homelab repo")
    return None


EXT_READS = {"list", "info", "search", "validate", "help", "--help", "-h"}


def _ext_switch(words):
    """`bromigos ext install|update|enable|disable|remove|migrate|new`, `bromigos theme
    install|remove` (an alias of ext install, and a remove that can drop an extension),
    and setup or install with an answers file (its [extensions] can approve code): only
    the host installs or changes extensions. Listing, reading and searching them is fine."""
    for i, w in enumerate(words):
        if os.path.basename(w) != "bromigos":
            continue
        rest = [x for x in words[i + 1:] if x not in ("--json",)]
        if rest[:1] == ["ext"]:
            verb = next((x for x in rest[1:] if not x.startswith("-") or x in ("-h", "--help")), "list")
            if verb not in EXT_READS:
                return ("extensions are the host's to install, update, enable or remove (bromigos ext "
                        f"{verb}); I can list, read and search them")
        if rest[:1] == ["theme"] and len(rest) > 1 and rest[1] in ("install", "remove"):
            return f"bromigos theme {rest[1]} installs or removes extensions: that's the host's to do"
        if rest[:1] in (["setup"], ["install"]) and any(x == "--answers" or x.startswith("--answers=")
                                                       or x == "extensions" for x in rest):
            return "an answers file can approve extensions that run code: the host runs that, not me"
    return None


def _work_dir():
    from .devflow import WORK
    return os.path.realpath(WORK)


def _names_scoped(words, cwd):
    """True when every path this command can reach is inside one of his own clones (devflow.WORK):
    it runs there, changes directory at most once and only within it, and every word with a path
    in it (also after = or :) resolves inside. No ~, $ or backticks (they could point anywhere).
    Then a bare protected name (shell.py) or a dotfiles-relative path means his clone's copy."""
    if not cwd:
        return False
    work = _work_dir()

    def inside(p):
        r = os.path.realpath(os.path.join(cwd, p))
        return r == work or r.startswith(work + os.sep)
    if not inside(cwd):
        return False
    cds = 0
    for i, w in enumerate(words):
        if any(c in w for c in "~$`"):
            return False
        if os.path.basename(w) in ("cd", "pushd", "popd"):
            cds += 1
            if cds > 1 or os.path.basename(w) == "popd" or (i + 1 < len(words) and not inside(words[i + 1])):
                return False
        if w in ("-C", "--git-dir", "--work-tree", "--directory", "-D") and i + 1 < len(words) \
                and not inside(words[i + 1]):
            return False
        for seg in re.split(r"[=:]", w):
            if "/" in seg and not inside(seg):
                return False
    return True


def check_command(words, joined, cwd=None, command=None):
    """-> a refusal reason when a terminal command could write a protected file, write the
    desktop's visuals outside the build loop, or reach a default branch around his merge tool.
    cwd: where it runs (his clones scope the bare names); command: the command line as typed."""
    joined = joined.replace("${HOME}", HOME).replace("$HOME", HOME)
    joined = " ".join(HOME + w[1:] if w.startswith("~/") else w for w in joined.split(" "))
    scoped = _names_scoped(words, cwd)
    words = [os.path.expanduser(w) if w.startswith("~/") else w for w in words]
    from .devflow import shell_refusal
    why = shell_refusal(command if command is not None else " ".join(words), cwd)
    if why:
        return why
    why = _ai_target_switch(words)
    if why:
        return why
    why = _ext_switch(words)
    if why:
        return why
    why = _lab_switch(words)
    if why:
        return why
    why = _visual_write(words, joined)
    if why:
        return why
    hits = []
    for p in PROTECTED:
        full = os.path.join(DOT, p)
        stow = full.replace(os.path.join(DOT, "linux"), HOME)
        base = "" if p.endswith("/") else os.path.basename(p)   # a folder by its path, not a bare word ("user")
        rel = p.split("linux/.config/", 1)[-1]
        if (full.rstrip("/") in joined or stow.rstrip("/") in joined or (not scoped and (rel.rstrip("/") in joined
                or (base and re.search(r"(^|[\s/'\"=])" + re.escape(base) + r"($|[\s'\";|&)>])", joined)
                    and base not in ("events.py", "memory.py", "paths.py", "llm.py"))))):   # common names: by path only
            hits.append(p)
    for p in WRITE_LOCKED:                       # extensions and their ledger: read, never write
        if p.rstrip("/") in joined or os.path.realpath(p.rstrip("/")) in joined:
            hits.append(p)
    for p in WRITE_LOCKED_FILES:                 # the lab's settings: read, never write
        rel = os.path.join(os.path.basename(os.path.dirname(p)), os.path.basename(p))   # bromigos/lab.toml
        if p in joined or os.path.realpath(p) in joined or (rel in joined and not scoped):
            hits.append(p)
    for p in PROTECTED_ABS:                      # the same code outside the dotfiles (package, repos, plugins)
        base = "" if p.endswith("/") else os.path.basename(p)
        if (p.rstrip("/") in joined
                or (base and not scoped and re.search(r"(^|[\s/'\"=])" + re.escape(base) + r"($|[\s'\";|&)>])", joined)
                    and base not in ("events.py", "memory.py", "paths.py", "llm.py"))):
            hits.append(p)
    if not hits:
        return None
    first = os.path.basename(words[0]) if words else ""
    if first in READ_ONLY and not _writes(joined):
        return None
    if first == "git":
        rest = list(words[1:])
        while rest and rest[0] in ("-C", "-c", "--git-dir", "--work-tree"):
            rest = rest[2:]
        if rest and rest[0] in ("diff", "log", "show", "status", "blame", "ls-files") and not _writes(joined):
            return None
    if first == "sed" and "-n" in words and not re.search(r"\s-i", joined):
        return None
    if re.match(r"(python3?|\S*/python3?)$", first) and "-m" in words and "py_compile" in words:
        return None
    return f"that could change protected safety code ({', '.join(h.rstrip('/').split('/')[-1] for h in hits[:3])}); " \
           "VECTOR never edits his own limits"


def check_diff(files, added_lines):
    """files: changed paths relative to the dotfiles root; added_lines: [(path, line)].
    -> list of reasons (empty when the change is allowed)."""
    why = []
    for f in files:
        if protected(f):
            why.append(f"{f} is protected safety code")
        if SECRET_FILE.search(f):
            why.append(f"{f} looks like a secret or credential file")
        if not f.startswith(("linux/.config/bromigos/", "linux/.config/bromigos-live/", "linux/.config/waybar/",
                             "linux/.config/hypr/", "AGENTS.md", "VECTOR-CHANGELOG.md")):
            why.append(f"{f} is outside the desktop's own folders (bromigos, bromigos-live, waybar, hypr)")
    for f, line in private_lines(added_lines):
        why.append(f"{f}: a private detail in the public dotfiles ({PRIVATE.search(line).group(0)[:40]}); "
                   "put it in the private overlay ~/.config/bromigos/private/ and read it from there")
    from .shell import redact
    for f, line in added_lines:
        if SAFETY_REFS.search(line):
            why.append(f"{f}: reaches into safety code: {line.strip()[:100]}")
        if redact(line) != line:
            why.append(f"{f}: looks like it carries a secret value")
        if f.startswith("linux/.config/hypr/") and re.search(r"bromigos-holo|holo\.app|python", line):
            why.append(f"{f}: changes how VECTOR's own daemon starts")
    return sorted(set(why))


def _dev_checkout(p):
    """A development checkout (his source repos): VECTOR may not write there (check_command,
    check_diff), but it isn't what runs, and a developer's edits there would only fill the
    audit log. The sentinel watches what runs: the package, his config and plugins."""
    dev = [os.path.realpath(os.path.join(HOME, "github.com/bromigos-org", r)) for r in ("vector", "bromigOS")]
    rp = os.path.realpath(p.rstrip("/"))
    return any(rp == d or rp.startswith(d + os.sep) for d in dev)


def _package_verified(path, digest):
    """Whether `path` now holds exactly what pacman installed for the package that owns it
    (the sha256 in that package's mtree), or was removed by a package that no longer ships
    it. Files under /usr are root's: VECTOR can't change them, and a match with the package
    database means an update (sudo pacman / bromigos update), not tampering."""
    import glob
    import gzip
    import subprocess
    if not path.startswith(("/usr/", "/etc/")):
        return False
    try:
        owner = subprocess.run(["pacman", "-Qoq", path], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    if owner.returncode != 0:
        return digest is None                   # gone, and no package claims it: removed by an update
    rel = "./" + path.lstrip("/")
    for name in owner.stdout.split():
        for mtree in glob.glob(f"/var/lib/pacman/local/{name}-*/mtree"):
            if os.path.basename(os.path.dirname(mtree))[len(name) + 1:].count("-") != 1:
                continue                        # bromigos-vector-x-1, not bromigos-vector-extra-x-1
            try:
                with gzip.open(mtree, "rt") as f:
                    for line in f:
                        if line.split(" ", 1)[0] == rel:
                            m = re.search(r"sha256digest=([0-9a-f]{64})", line)
                            return bool(m and digest == m.group(1))
            except OSError:
                pass
    return False


def _ext_norm(p):
    p = p.rstrip("/")
    return os.path.join(os.path.realpath(os.path.dirname(p)), os.path.basename(p))


def _ext_verified(path, digest):
    """Whether `path` (in his plugin or homelab folders) is what `bromigos ext` recorded:
    an enabled extension's link whose content has the sha256 in the ledger and whose file
    is inside the extensions' checkouts; or a link the ledger no longer lists (removed or
    disabled by bromigos ext) that is gone. While an update is under way (the ledger's
    txn, at most TXN_GRACE seconds old) either version, or none, is accepted. The ledger
    is the host's: VECTOR can't write it (WRITE_LOCKED)."""
    import json
    ext, active, _, ledger_path = _ext()
    rp = _ext_norm(path)
    if not rp.startswith(_ext_norm(active) + os.sep):
        return False
    try:
        with open(ledger_path) as f:
            ledger = json.load(f)
    except (OSError, ValueError):
        return False
    listed = {}
    for e in (ledger.get("extensions") or {}).values():
        if not e.get("enabled"):
            continue
        for part in e.get("parts") or []:
            for link in part.get("links") or []:
                listed.setdefault(_ext_norm(link.get("link") or ""), set()).add(link.get("sha256"))
    txn = ledger.get("txn") or {}
    if txn and 0 <= time.time() - float(txn.get("started_unix") or 0) < TXN_GRACE:
        for a in txn.get("accept") or []:
            if _ext_norm(a.get("link") or "") == rp and a.get("sha256") == digest:
                return True
    if digest is None:
        return rp not in listed                   # unlinked after the ledger dropped it
    src = os.path.realpath(os.path.join(ext, "src")) + os.sep
    return digest in listed.get(rp, set()) and os.path.realpath(path).startswith(src)


TXN_GRACE = 600


GUARD_LOG = os.path.join(HOME, ".local/state/bromigos/vector-guard.log")


def _short(p):
    return os.path.relpath(p, DOT) if p.startswith(DOT + os.sep) else p.replace(HOME, "~", 1)


def audit_change(paths, verified, digests=None):
    """One line in the guard's audit log: these protected files changed, verified how
    ("package", "extension") or "no"."""
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": "changed", "verified": verified,
           "paths": [_short(p) for p in paths[:40]]}
    if len(paths) > 40:
        rec["more"] = len(paths) - 40
    if digests:
        rec["sha256"] = {_short(p): (d or "removed")[:12] for p, d in list(digests.items())[:40]}
    try:
        os.makedirs(os.path.dirname(GUARD_LOG), exist_ok=True)
        with open(GUARD_LOG, "a") as f:
            f.write(__import__("json").dumps(rec) + "\n")
    except OSError:
        pass
    return rec


class Sentinel:
    """Hashes the protected files now and every 5 s and records every change in the guard's
    audit log. An audit only: it never switches the terminal off and never notifies anyone
    (the emergency switch is the operator's, by hand). on_change(record), if given, gets
    each record (the tests watch it). Lives in the daemon."""

    def __init__(self, on_change=None, every=5.0):
        self.on_change, self.every = on_change, every
        self.base = self._hashes()
        self.changes = 0
        threading.Thread(target=self._loop, daemon=True, name="guard-sentinel").start()

    @staticmethod
    def _hashes():
        out = {}
        for p in [os.path.join(DOT, q) for q in PROTECTED] + PROTECTED_ABS:
            if _dev_checkout(p):
                continue                         # writes there are refused; edits there aren't his code
            full = p
            paths = [full]
            if p.endswith("/") and os.path.isdir(full):
                paths = sorted(os.path.join(dp, f) for dp, dns, fs in os.walk(full)
                               for f in fs if "__pycache__" not in dp)
            for q in paths:
                try:
                    with open(q, "rb") as f:
                        out[q] = hashlib.sha256(f.read()).hexdigest()
                except OSError:
                    out[q] = None
        return out

    def check(self):
        """One pass: record what changed since the last one; -> the records written."""
        now = self._hashes()
        changed = sorted(p for p in set(now) | set(self.base) if now.get(p) != self.base.get(p))
        if not changed:
            return []
        recs = []
        updated = [p for p in changed if _package_verified(p, now.get(p))]
        exts = [p for p in changed if p not in updated and _ext_verified(p, now.get(p))]
        rest = [p for p in changed if p not in updated and p not in exts]
        for group, how in ((updated, "package"), (exts, "extension"), (rest, "no")):
            if group:
                recs.append(audit_change(group, how, {p: now.get(p) for p in group}))
        for p in changed:
            self.base[p] = now.get(p)
        self.changes += len(changed)
        for r in recs:
            if self.on_change:
                try:
                    self.on_change(r)
                except Exception:
                    pass
        return recs

    def _loop(self):
        while True:
            time.sleep(self.every)
            try:
                self.check()
            except Exception:
                pass


def read_log(n=30):
    """The last n records of the guard's audit log, oldest first."""
    import json
    try:
        with open(GUARD_LOG) as f:
            lines = f.readlines()[-n:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out
