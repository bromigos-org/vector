"""VECTOR's terminal: full access as the operator's user, no confirmation prompts, every
command logged. The limits are here, in code, and covered by tools/test-shell.py:

1. No privilege escalation: sudo, su, doas, pkexec, run0 and polkit helpers are refused
   anywhere in the command, including inside `sh -c '...'`, pipes, $(...), and the text
   of a script the command runs. A command that can't be parsed is refused.
2. No secret values: the shell's environment is scrubbed of tokens, keys, secrets,
   passwords, Vault, cloud and GitHub credentials. Credentials that sign or authenticate
   without exposing a value do pass: SSH_AUTH_SOCK (the operator's agent; ~/.ssh stays
   refused) and KUBECONFIG (VECTOR's own `vector-operator` kubeconfig; the operator's
   admin kubeconfig is $HOMELAB_ADMIN_KUBECONFIG, for when that isn't enough). `vault`, `gcloud secrets`, `kubectl … secret(s)`
   (get, describe, -o yaml/json, edit), `kubectl exec` that prints a pod's environment or
   its mounted ServiceAccount/secret files, minting ServiceAccount tokens, changing RBAC
   (that goes through homelab GitOps), Vault's HTTP API, `ansible-vault`,
   `ansible-inventory` and ad-hoc `ansible -m debug` (they print vars, secrets.yml
   included), and any read of known secret paths (~/.vault-token, ~/.ssh private keys, the desktop's
   key and token files, gcloud, kube config, .env files, gnupg, password stores, browser
   profiles, git/docker/netrc credentials, wallets) are refused. Anything secret-looking in
   the output is redacted before it reaches the model, the transcript or the log.
3. No real money: live venue order endpoints (Alpaca live, Kalshi, Polymarket, Coinbase),
   arming live trading, live-trading API routes, the live operator list, fund transfers
   and wallet keys are refused, and so is anything that names a real-money service of the
   user's own, or matches a rule, from vector.toml's [real_money] table (money.py). A
   `git push` whose unpushed commits touch a repository's real-money code
   ([real_money.code]) is refused too. Paper and read endpoints are fine.
4. Each command runs in a new session (killable as a group) inside a transient scope of
   his systemd user slice (switch.py), default 60 s timeout (up to 10 min on request), cwd ~
   by default, output capped. The operator's emergency switch (switch.py: `bromigos vector
   off|on`, SUPER+CTRL+ESCAPE, the bar, Settings) kills every process of that slice; while
   it's off nothing runs, and only the operator turns it back on (check refuses every way
   of doing it). Barge-in or STOP kills the running command's scope.
5. Audit: ~/.local/state/bromigos/vector-shell.log, one JSON line per command or refusal.
   An Ansible playbook that touches Vault, a real-money system or venue secrets is also written to
   vector-sensitive.log and announced aloud (on_notice) before it runs.
"""
import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time
import urllib.parse

from ..private import PRIV as PRIVCFG
from . import money, switch

HOME = os.path.expanduser("~")
STATE = os.path.join(HOME, ".local/state/bromigos")
LOG = os.path.join(STATE, "vector-shell.log")
GUARD_LOG = os.path.join(STATE, "vector-guard.log")
MAX_READ = 512 * 1024
MODEL_CHARS = 6000

PRIV = {"sudo", "su", "doas", "pkexec", "run0", "pkttyagent", "sudoedit", "visudo", "polkit-agent-helper-1",
        "machinectl", "systemd-run"}           # systemd-run / machinectl shell can escalate via polkit
SECRET_CMDS = {"vault", "pass", "gopass", "secret-tool", "gpg", "gpg2", "keepassxc-cli", "op", "bw", "lpass"}
SHELLS = {"sh", "bash", "zsh", "dash", "fish", "ksh"}
SECRET_PATHS = [
    ".vault-token", ".ssh/", ".gnupg", ".password-store", ".local/share/bromigos/", ".config/gcloud", ".kube/",
    ".aws", ".azure", ".docker/config.json", ".netrc", ".git-credentials", ".config/gh/", ".config/nolgia",
    ".mozilla", ".librewolf", ".config/google-chrome", ".config/chromium", ".config/BraveSoftware",
    ".config/solana", ".local/share/keyrings", ".pki", ".config/rclone", ".npmrc", ".pypirc", ".cargo/credentials",
    "zsh-secrets", "private/",
]
SECRET_FILE = re.compile(r"(^|/)(\.env(\..*)?|.*\.pem|.*\.key|id_(rsa|ed25519|ecdsa|dsa)[^/]*|.*keypair.*\.json|"
                         r"credentials(\.json)?|.*token.*|.*secret.*)$", re.I)
SAFE_SSH = re.compile(r"\.ssh/(known_hosts|config|[^/]+\.pub)$")
# inside ~/.local/share/bromigos/ (keys and tokens): folders that hold no secrets
SAFE_BROMIGOS = re.compile(r"^\.local/share/bromigos/(builds|nolgia|3d-raw|voices)(/|$)|"
                           r"^\.local/share/bromigos/venv(-brain|-tts)?/(bin/python3?(\.\d+)?|lib/)")
# his own clones (devflow.WORK): no keys or tokens there, so ~/.local/share/bromigos/ doesn't refuse
# them; every other secret path and secret-looking file name inside them still does
WORK_CLONES = re.compile(r"^\.local/share/bromigos/vector/work(/|$)")
MONEY = [
    (re.compile(r"(?<!paper-)api\.alpaca\.markets", re.I), "Alpaca live endpoint"),
    (re.compile(r"(trading-api|api\.elections)\.kalshi\.com|kalshi\.com/trade-api", re.I), "Kalshi live endpoint"),
    (re.compile(r"clob\.polymarket\.com|polymarket\.us/.*order|api\.polymarket", re.I), "Polymarket order endpoint"),
    (re.compile(r"api\.(exchange\.)?coinbase\.com|api\.coinbase\.com/api/v3/brokerage", re.I), "Coinbase endpoint"),
    (re.compile(r"/api/live(/|\b)|/live/(arm|enable|go|start|orders?)\b|\barm[-_ ]?live\b|\blive[-_ ]?arm", re.I), "arming live trading"),
    (re.compile(r"\b(solana\s+transfer|spl-token\s+transfer|cast\s+send|cast\s+wallet|seth\s+send|bitcoin-cli\s+send)", re.I), "moving funds"),
    (re.compile(r"\b(wallet|keypair|mnemonic|seed[-_ ]?phrase|private[-_ ]?key)\b", re.I), "wallet keys"),
    (re.compile(r"live[-_ ]?operators?", re.I), "the real-money operator list (LIVE_OPERATORS)"),
    (re.compile(r"/api/(live|intents)\b", re.I), "a live-trading API route"),
] + money.RULES          # the user's own real-money services and rules (vector.toml [real_money])
KUBE_TOOLS = r"\b(kubectl|kubecolor|k9s|helm|oc)\b"
# a repository's real-money code (vector.toml [real_money.code]): a push whose unpushed
# commits touch it is refused (check_push; money.code_rule)
REDACT = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[redacted private key]"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), "[redacted github token]"),
    (re.compile(r"\b(hvs|hvb|s)\.[A-Za-z0-9]{20,}\b"), "[redacted vault token]"),
    (re.compile(r"\b(sk|rk|pk)-[A-Za-z0-9_\-]{16,}\b"), "[redacted api key]"),
    (re.compile(r"\bnol_[A-Za-z0-9]{16,}\b"), "[redacted nolgia token]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted aws key]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "[redacted slack token]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), "[redacted jwt]"),
    (re.compile(r"(?i)\b([a-z0-9_]*(token|secret|password|passwd|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?secret)"
                r"[a-z0-9_]*)\s*([=:])\s*(?!\[redacted)(\"[^\"]{6,}\"|'[^']{6,}'|[^\s,;]{6,})"), r"\1\3[redacted]"),
    (re.compile(r"(?i)(authorization:\s*(bearer|basic)\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1[redacted]"),
]
ENV_DROP = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|_KEY$|KEY_ID|PRIVATE|CREDENTIAL|^VAULT_|^AWS_|^GH_|^GITHUB_|"
                      r"^GOOGLE_|^GCLOUD|^AZURE_|^OPENAI|^ANTHROPIC|^NOLGIA|^HF_TOKEN|^KUBECONFIG$|SSH_AUTH_SOCK|^GPG_|"
                      r"^DBUS_SESSION_BUS_ADDRESS$)", re.I)
VECTOR_KUBECONFIG = os.path.join(HOME, ".local/share/bromigos/vector-operator-kubeconfig")
ADMIN_KUBECONFIG = PRIVCFG.path("admin_kubeconfig")    # private: paths.admin_kubeconfig ("" = none)


def _own(field):
    try:
        from .vault import own_field
        return own_field(field)
    except Exception:
        return None
_VAULT_HOST = urllib.parse.urlparse(PRIVCFG.url("vault")).hostname or ""   # private: endpoints.vault
VAULT_API = (re.escape(_VAULT_HOST) + "|" if _VAULT_HOST else "") + r"x-vault-token|:8200\b|/v1/(secret|sys|auth)/"
SENSITIVE = os.path.join(STATE, "vector-sensitive.log")
SENSITIVE_PLAY = re.compile(r"vault|kalshi|alpaca|coinbase|polymarket|wallet|secrets?\b|live[-_]operators" +
                            "".join("|" + re.escape(w) for w in money.SYSTEMS + money.SERVICES), re.I)


def ssh_agent():
    """An SSH agent socket that never prompts: the session's own, else OpenSSH's user
    agent. Not GCR's (gcr/ssh): it asks for confirmation in a dialog and holds a hardware
    key that waits for a touch, so a push would hang. With no usable agent identity ssh
    falls back to the operator's key file, which ssh reads itself (the shell still
    refuses any command that names ~/.ssh)."""
    run = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    for p in (os.environ.get("SSH_AUTH_SOCK"), os.path.join(run, "ssh-agent.socket")):
        if p and os.path.exists(p) and "/gcr/" not in p:
            return p
    return None


# ------------------------------------------------------------------ the sandbox (structural limits)
# Every command runs under bubblewrap. Secret files are masked (an empty /dev/null or an empty
# tmpfs in their place), so no language, path trick or copy can read them; his safety code and
# everything the desktop's daemons run are read-only. The rest of the machine is his as before:
# the admin and vector-operator kubeconfigs, ansible, SSH and git push through his own agent.
# The tool layer (outside this sandbox) still reads the keys it needs.
SECRET_HOME_FILES = [".vault-token", ".netrc", ".git-credentials", ".pgpass", ".npmrc", ".pypirc", ".docker/config.json",
                     ".claude/.credentials.json", ".claude.json", ".config/gh/hosts.yml", ".local/share/opencode/auth.json",
                     ".pulumi/credentials.json", ".terraform.d/credentials.tfrc.json", ".config/bromigos/private/env",
                     ".cargo/credentials", ".cargo/credentials.toml"]
SECRET_HOME_DIRS = [".config/gcloud", ".aws", ".azure", ".gnupg", ".password-store", ".local/share/keyrings", ".pki",
                    ".foundry/keystores", ".config/solana", ".ethereum/keystore", ".config/github-copilot",
                    ".config/configstore", ".config/rclone", ".config/nolgia", ".mozilla", ".librewolf",
                    ".config/BraveSoftware", ".config/google-chrome", ".config/google-chrome-for-testing",
                    ".config/chromium", ".config/Code/User/globalStorage"]
_SSH_KEEP = re.compile(r"(\.pub|^config|^known_hosts.*|^authorized_keys|^environment|^rc)$")
_SHARE_KEEP = {"notes.md", "vector-operator-kubeconfig"}       # everything else at the top of it is a key or token
REPO_SECRET = re.compile(r"^(\.env(\..+)?|\.envrc|.*\.(key|pem|p12|pfx|keystore|jks|tfvars|tfstate|tfstate\.backup)|"
                         r"secrets?\.ya?ml(\..+)?|credentials\.json|.*service[-_]account.*\.json|.*keypair.*\.json|"
                         r"id_(rsa|dsa|ecdsa|ed25519)(_sk)?|kubeconfig.*|.*\.kubeconfig)$", re.I)
_REPO_OK = re.compile(r"\.(example|sample|template|dist|pub|md)$|\.pub\.pem$", re.I)
_PRUNE = {"node_modules", ".git", ".venv", "venv", "vendor", "target", "dist", ".next", "__pycache__", "build",
          ".terraform", ".cache"}
_MASKS = {"t": 0.0, "files": [], "dirs": []}


def _repo_secrets(root, depth=6):
    out = []
    root = os.path.realpath(root)
    for d, dirs, files in os.walk(root):
        if d[len(root):].count(os.sep) >= depth:
            dirs[:] = []
        dirs[:] = [x for x in dirs if x not in _PRUNE]
        out += [os.path.join(d, f) for f in files if REPO_SECRET.match(f) and not _REPO_OK.search(f)]
    return out


def masks():
    """(files, dirs) to mask in the sandbox. Rescanned every two minutes, in the background once
    there is a list (the rescan of ~/github.com takes about half a second)."""
    age = time.monotonic() - _MASKS["t"]
    if _MASKS["t"] and age < 120:
        return _MASKS["files"], _MASKS["dirs"]
    if _MASKS["t"]:
        if not _MASKS.get("busy"):
            _MASKS["busy"] = True
            threading.Thread(target=_rescan, daemon=True).start()
        return _MASKS["files"], _MASKS["dirs"]
    return _rescan()


def _rescan():
    try:
        return _scan()
    finally:
        _MASKS["busy"] = False


def _scan():
    keep = {os.path.realpath(p) for p in (VECTOR_KUBECONFIG, ADMIN_KUBECONFIG) if p}
    files = [os.path.join(HOME, f) for f in SECRET_HOME_FILES]
    dirs = [os.path.join(HOME, d) for d in SECRET_HOME_DIRS]
    for d, keep_rx in ((os.path.join(HOME, ".ssh"), _SSH_KEEP), (os.path.join(HOME, ".kube"), None),
                       (os.path.join(HOME, ".local/share/bromigos"), None)):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            f = os.path.join(d, n)
            if not os.path.isfile(f) or (keep_rx and keep_rx.search(n)) or (d.endswith("bromigos") and n in _SHARE_KEEP) \
                    or os.path.realpath(f) in keep:
                continue
            files.append(f)
    try:
        files += _repo_secrets(os.path.join(HOME, "github.com"))
    except OSError:
        pass
    files = sorted({f for f in files if os.path.realpath(f) not in keep})
    _MASKS.update(t=time.monotonic(), files=files, dirs=sorted(set(dirs)))
    return _MASKS["files"], _MASKS["dirs"]


def is_masked(path):
    """True when the sandbox hides this path (for tools that open files outside it)."""
    p = os.path.realpath(os.path.expanduser(path))
    files, dirs = masks()
    return p in {os.path.realpath(f) for f in files} or any(
        p == os.path.realpath(d) or p.startswith(os.path.realpath(d) + os.sep) for d in dirs)


def readonly_paths():
    """His safety code, everything the desktop's daemons run, the user units and his own audit logs."""
    from . import guard
    dot = os.path.realpath(os.path.join(HOME, ".dotfiles"))
    ps = [os.path.join(dot, "linux/.config/bromigos/holo")] + [os.path.join(dot, p) for p in guard.VISUAL + guard.PROTECTED]
    ps += guard._code_roots() + [p.rstrip("/") for p in guard.PROTECTED_ABS]   # every VECTOR tree, his plugins, settings
    for d in guard.WRITE_LOCKED:                 # extensions and their ledger: made now, so they're bound read-only
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        ps.append(d.rstrip("/"))
    for f in guard.WRITE_LOCKED_FILES:           # the lab's settings: made now (empty), so it's bound read-only
        try:
            if not os.path.lexists(f):
                os.makedirs(os.path.dirname(f), exist_ok=True)
                with open(f, "a"):
                    pass
        except OSError:
            pass
        ps.append(f)
    ps += switch.ensure_dirs()                    # the emergency switch's markers: never his to write or remove
    try:                                         # the guard's audit log: made now, so it's bound read-only
        if not os.path.lexists(GUARD_LOG):
            os.makedirs(STATE, exist_ok=True)
            open(GUARD_LOG, "a").close()
    except OSError:
        pass
    ps += [os.path.join(HOME, ".config/systemd/user"), LOG, SENSITIVE, GUARD_LOG]
    return [p.rstrip("/") for p in dict.fromkeys(os.path.realpath(x) for x in ps) if os.path.lexists(p)]


SANDBOX_ETC = os.path.join(HOME, ".cache/bromigos/vector-sandbox")


def _ssh_etc():
    """User-owned copies of /etc/ssh's client config: in the sandbox root's files show as owned by
    nobody, which ssh refuses ("Bad owner or permissions"), so these copies stand in for them."""
    import shutil
    out = os.path.join(SANDBOX_ETC, "ssh")
    src = "/etc/ssh"
    try:
        stamp = max(os.path.getmtime(os.path.realpath(os.path.join(dp, f)))
                    for dp, _, fs in os.walk(src) for f in fs if f.startswith("ssh_config") or dp.endswith(".d"))
        if not os.path.isdir(out) or os.path.getmtime(out) < stamp:
            tmp = out + ".new"
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(os.path.join(tmp, "ssh_config.d"))
            shutil.copyfile(os.path.join(src, "ssh_config"), os.path.join(tmp, "ssh_config"))
            d = os.path.join(src, "ssh_config.d")
            for f in (os.listdir(d) if os.path.isdir(d) else []):
                shutil.copyfile(os.path.realpath(os.path.join(d, f)), os.path.join(tmp, "ssh_config.d", f))
            shutil.rmtree(out, ignore_errors=True)
            os.replace(tmp, out)
        return [(os.path.join(out, "ssh_config"), os.path.join(src, "ssh_config")),
                (os.path.join(out, "ssh_config.d"), os.path.join(src, "ssh_config.d"))]
    except (OSError, ValueError):
        return []


GH_OWNERS = ("bromigos-org", "nolgiainc", "blackflame007")   # a fine-grained token covers one owner each
GH_DEV = "bromigodev"          # his own GitHub account: git and gh in his clones (devflow.py) use it


def _gh_var(owner):
    return "GH_TOKEN_" + re.sub(r"[^A-Za-z0-9]", "_", owner).upper()


GH_CONFIG = os.path.join(SANDBOX_ETC, "gh-config")      # an empty tmpfs inside the sandbox, per command


def _gh_shim():
    """A `gh` first on his PATH. It picks his own token for the repo's owner (the -R/--repo
    argument, else this checkout's origin), else his default token; with none it says which
    Vault field is missing instead of a login prompt. The host's own gh login is masked."""
    import shutil
    d = os.path.join(SANDBOX_ETC, "bin")
    real = shutil.which("gh", path=":".join(p for p in os.environ.get("PATH", "/usr/bin").split(":") if p != d)) \
        or "/usr/bin/gh"
    try:
        from .vault import OWN
        where = f"secret/{OWN}" if OWN else "his Vault credentials"
    except Exception:
        where = "his Vault credentials"
    cfg = GH_CONFIG
    body = f'''#!/usr/bin/python3
# VECTOR's gh: his own token per owner. Generated by holo/vector/shell.py.
import os, re, subprocess, sys
a = sys.argv[1:]
OWNERS = {repr(list(GH_OWNERS))}
SLUG = r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\\.git)?"
def pick():
    for i, x in enumerate(a):                       # -R/--repo
        v = a[i + 1] if x in ("-R", "--repo") and i + 1 < len(a) else (x.split("=", 1)[1] if x.startswith("--repo=") else None)
        m = re.search(r"(?:github\\.com[/:])?" + SLUG + "$", v or "")
        if m:
            return m.group(1) + "/" + m.group(2)
    for x in a:                                     # a URL, an api path, or a positional OWNER/REPO
        m = (re.search(r"github\\.com[/:]" + SLUG + r"(?:[/#?]|$)", x)
             or re.match(r"/?repos/" + SLUG + r"(?:[/?]|$)", x)
             or (re.fullmatch(SLUG, x) if not x.startswith("-") else None))
        if m and (m.group(1) in OWNERS or "github.com" in x or "repos/" in x):
            return m.group(1) + "/" + m.group(2)
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()
    m = re.search(r"github\\.com[/:]" + SLUG + "$", url)
    return m.group(1) + "/" + m.group(2) if m else None
slug = pick()
def var(name):
    return "GH_TOKEN_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()
owner = slug.split("/")[0] if slug else None
tok = (os.environ.get(var(owner)) if owner else None) or os.environ.get("GH_TOKEN")
WORK = {repr(os.path.join(HOME, ".local/share/bromigos/vector/work"))}
if (os.getcwd() == WORK or os.getcwd().startswith(WORK + "/")) and os.environ.get(var({repr(GH_DEV)})):
    tok = os.environ[var({repr(GH_DEV)})]        # in his clones: his own account
if not tok and not owner:                          # no repo in sight (gh api user, gh search): any of his own
    tok = next((os.environ[var(o)] for o in reversed(OWNERS) if os.environ.get(var(o))), None)
field = ("github_token_" + owner + " (or github_token)") if owner else "github_token"
for k in [k for k in os.environ if k.startswith("GH_TOKEN_")]:
    os.environ.pop(k)
if not tok:
    sys.stderr.write("gh: VECTOR has no GitHub credential for " + (slug or "this") + " yet: Vault {where}, field "
                     + field + ", isn't there or isn't readable yet. The host creates it: a fine-grained token "
                     "for VECTOR (see the holo README). Nothing ran.\\n")
    sys.exit(4)
os.environ["GH_TOKEN"] = tok
os.environ["GH_CONFIG_DIR"] = "{cfg}"          # his own, empty: never the host's (masked) ~/.config/gh
os.environ.setdefault("GH_PROMPT_DISABLED", "1")
os.execv("{real}", ["gh"] + a)
'''
    _write_shim(d, "gh", body)
    return d


def _write_shim(d, name, body):
    f = os.path.join(d, name)
    try:
        with open(f) as fh:
            if fh.read() == body:
                return
    except OSError:
        pass
    os.makedirs(d, exist_ok=True)
    with open(f + ".tmp", "w") as fh:
        fh.write(body)
    os.chmod(f + ".tmp", 0o755)
    os.replace(f + ".tmp", f)


def sandbox_env(env):
    """His shell's environment inside the sandbox: his own GitHub tokens (never the host's),
    per owner and a default, and the gh shim first on PATH."""
    env = {k: v for k, v in env.items() if not k.startswith(("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE"))}
    tok = _own("github_token")
    if tok:
        env["GH_TOKEN"] = tok
    for name in GH_OWNERS + (GH_DEV,):
        t = _own("github_token_" + name)
        if t:
            env[_gh_var(name)] = t
    try:
        env["PATH"] = _gh_shim() + ":" + env.get("PATH", "")
    except OSError:
        pass
    return env


def jail_argv(argv, writable_only=None, die_with_parent=True):
    """argv wrapped in the sandbox, or None when bubblewrap is missing (then nothing runs)."""
    import shutil
    bw = shutil.which("bwrap")
    if not bw:
        return None
    cmd = [bw, "--dev-bind", "/", "/"]
    if writable_only:
        dot = os.path.realpath(os.path.join(HOME, ".dotfiles"))
        cmd += ["--ro-bind", dot, dot, "--bind", os.path.join(dot, ".git"), os.path.join(dot, ".git"),
                "--bind", writable_only, writable_only]
    for p in readonly_paths():
        cmd += ["--ro-bind", p, p]
    for src, dst in _ssh_etc():
        if os.path.lexists(dst):
            cmd += ["--ro-bind", src, dst]
    if os.path.isdir("/sys/fs/cgroup"):            # no moving his processes out of the switch's slice
        cmd += ["--ro-bind", "/sys/fs/cgroup", "/sys/fs/cgroup"]
    files, dirs = masks()
    os.makedirs(GH_CONFIG, exist_ok=True)
    cmd += ["--tmpfs", GH_CONFIG]               # gh's config: private and empty for every command
    for d in dirs:
        if os.path.isdir(d):
            cmd += ["--tmpfs", d]
    for f in files:
        if os.path.isfile(f):                  # only what still exists (a missing target would be created)
            cmd += ["--ro-bind", "/dev/null", f]
    return cmd + (["--die-with-parent"] if die_with_parent else []) + ["--"] + list(argv)


# VECTOR's own ssh-agent, loaded with the host's key file by this process (outside the sandbox), so
# his shell pushes and reaches the lab while the key file itself is masked.
AGENT_SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "vector-ssh-agent.sock")
AGENT_KEYS = [os.path.join(HOME, ".ssh/id_ed25519")]     # the hardware key waits for a touch: left out


def vector_agent():
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "SSH_ASKPASS")}
    env.update(SSH_AUTH_SOCK=AGENT_SOCK, SSH_ASKPASS_REQUIRE="never")
    try:
        r = subprocess.run(["ssh-add", "-l"], env=env, capture_output=True, timeout=5)
        if r.returncode == 0:
            return AGENT_SOCK
        if r.returncode == 2:                      # no agent there: start one
            if os.path.lexists(AGENT_SOCK):
                os.unlink(AGENT_SOCK)
            subprocess.run(["ssh-agent", "-a", AGENT_SOCK], env=env, capture_output=True, timeout=5)
        for k in AGENT_KEYS:
            if os.path.isfile(k):
                subprocess.run(["ssh-add", "-q", k], env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
        r = subprocess.run(["ssh-add", "-l"], env=env, capture_output=True, timeout=5)
        return AGENT_SOCK if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


class Refused(Exception):
    pass


def redact(text):
    for rx, rep in REDACT:
        text = rx.sub(rep, text)
    return text


def enabled():
    """Whether the operator's emergency switch is on (switch.py)."""
    return not switch.is_off()


def set_enabled(on, by="VECTOR, asked by the operator"):
    """Switching off works from anywhere; switching back on is the operator's alone."""
    if on:
        raise PermissionError("only the operator switches VECTOR back on (bromigos vector on)")
    switch.set_off(by, tell=False)
    return enabled()


OFF_NOTE = ("VECTOR is switched off: the operator's emergency switch. Nothing runs until he switches it back on, "
            "and only he can.")


def audit(**rec):
    os.makedirs(STATE, exist_ok=True)
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **rec}
    if "command" in rec:
        rec["command"] = redact(rec["command"])
    with open(LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")


# ------------------------------------------------------------------ parsing
def words_of(command):
    """Every word the shell would see, including inside $(...) and `...`; recursing into
    `sh -c '...'` strings. Raises Refused when the command can't be parsed confidently."""
    try:
        import bashlex
    except ImportError:
        bashlex = None
    out = []
    if bashlex is not None:
        try:
            trees = bashlex.parse(command)
        except Exception as e:
            raise Refused(f"can't parse that command confidently ({type(e).__name__})")

        class V(bashlex.ast.nodevisitor):
            def visitword(self, node, word):
                out.append(word)

            def visitheredoc(self, node, value):
                out.extend(value.split())
        for t in trees:
            V().visit(t)
    else:
        try:
            out = shlex.split(command, posix=True)
        except ValueError as e:
            raise Refused(f"can't parse that command confidently ({e})")
    # recurse into shell -c strings and eval
    more = []
    for i, w in enumerate(out):
        base = os.path.basename(w)
        if (base in SHELLS and i + 2 < len(out) + 1 and "-c" in out[i + 1:i + 3]) or base == "eval":
            idx = out.index("-c", i) + 1 if "-c" in out[i:i + 3] else i + 1
            if idx < len(out):
                more += words_of(out[idx])
    return out + more


def _script_words(w, cwd, depth):
    """If a word names a readable script, scan its text too (shallow)."""
    if depth > 1:
        return []
    p = os.path.realpath(os.path.join(cwd, os.path.expanduser(w)))
    dot = os.path.realpath(os.path.join(HOME, ".dotfiles"))
    if p.startswith(dot + os.sep) and os.path.isfile(p):      # the desktop's own scripts, committed and unmodified
        rel = os.path.relpath(p, dot)
        try:
            tracked = subprocess.run(["git", "-C", dot, "ls-files", "--error-unmatch", rel], capture_output=True,
                                     timeout=5).returncode == 0
            clean = subprocess.run(["git", "-C", dot, "diff", "--quiet", "HEAD", "--", rel], timeout=5).returncode == 0
            if tracked and clean:
                return []
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        if os.path.isfile(p) and os.path.getsize(p) < 256 * 1024:
            with open(p, "rb") as f:
                head = f.read(256 * 1024)
            if b"\0" in head[:4096]:
                return []
            text = head.decode("utf-8", "replace")
            if text.startswith("#!") or p.endswith((".sh", ".bash", ".zsh")):
                try:
                    return words_of(text)
                except Refused:
                    return text.split()
    except OSError:
        pass
    return []


def _path_secret(w, cwd):
    w2 = os.path.expandvars(os.path.expanduser(w.replace("${HOME}", HOME).replace("$HOME", HOME)))
    cands = [os.path.normpath(w2)] if w2.startswith("/") else [w2]
    if not w2.startswith("/"):
        cands.append(os.path.normpath(os.path.join(cwd, w2)))
    cands += [os.path.realpath(c) for c in list(cands) if c.startswith("/")]   # "..", symlinks
    for c in cands:
        rel = c[len(HOME) + 1:] if c.startswith(HOME + "/") else c
        if SAFE_SSH.search(c) or SAFE_BROMIGOS.search(rel):
            continue
        work = WORK_CLONES.search(rel)
        for sp in SECRET_PATHS:
            if work and sp == ".local/share/bromigos/":
                continue
            if sp in rel or (sp.endswith("/") and rel == sp[:-1]):
                return f"a protected path ({sp.rstrip('/')})"
        if SECRET_FILE.search(c) and ("/" in w or w.startswith(".")):
            return "a secret-looking file"
    return None


def check(command, cwd=HOME):
    """-> None when allowed; raises Refused with the reason."""
    if not command.strip():
        raise Refused("empty command")
    if "\x00" in command or len(command) > 8000:
        raise Refused("command too long or binary")
    cw = os.path.realpath(cwd)
    reason = _path_secret(cw, "/")
    if reason:
        raise Refused(f"working directory is {reason}")
    words = words_of(command)
    own = [os.path.basename(w).lower() for w in words]          # the command itself
    for w in list(words):
        words += _script_words(w, cw, 0)
    lowered = [os.path.basename(w).lower() for w in words]
    for w in [x.lower() for x in words]:          # whole words: a quoted remote command keeps its sudo
        for part in re.split(r"[\s;|&()`$<>'\"]+", w):
            part = os.path.basename(part)
            if part in PRIV or part.startswith("pkexec") or part.endswith("polkit-agent-helper-1"):
                raise Refused(f"privilege escalation ({part}) is not allowed")
    for w in own:                 # not the text of a script it runs: "pass" is also a Python keyword
        if w in SECRET_CMDS:
            raise Refused(f"the {w} CLI reads secrets; not allowed")
    joined = " ".join(words)
    if re.search(r"\bgcloud\b.*\bsecrets?\b", joined):
        raise Refused("gcloud secrets is not allowed")
    if re.search(KUBE_TOOLS, joined) and re.search(r"\bsecrets?\b|\bsecret/|\bsealedsecrets?\b", joined, re.I):
        raise Refused("reading Kubernetes secrets is not allowed")
    if re.search(KUBE_TOOLS, joined) and re.search(r"\bget\b.*\ball\b.*(-o|--output)", joined):
        raise Refused("kubectl get all with an output format can include secrets; name the kinds you need")
    if re.search(r"\b(kubectl|kubecolor)\b.*\b(exec|debug|attach|cp)\b", joined) and re.search(
            r"\b(env|printenv|set|declare|export|compgen)\b|/proc/|/run/secrets|/var/run/secrets|serviceaccount|\btoken\b", joined):
        raise Refused("printing a pod's environment or its mounted secrets is not allowed")
    if re.search(KUBE_TOOLS, joined) and re.search(r"\bcreate\s+token\b|serviceaccounts?/token|--kubeconfig-token", joined):
        raise Refused("minting a ServiceAccount token prints a secret; not allowed")
    if re.search(KUBE_TOOLS, joined) and re.search(
            r"\b(create|edit|patch|apply|replace|set)\b.*\b(clusterrolebindings?|rolebindings?|clusterroles?|roles?)(\b|/)"
            r"|\bauth\s+reconcile\b|\bcreate\s+(clusterrolebinding|rolebinding|clusterrole|role)\b", joined):
        raise Refused("changing RBAC from the terminal is not allowed; RBAC changes go through homelab GitOps, reviewed")
    if re.search(r"/run/secrets/|/var/run/secrets/|/etc/rancher/k3s/k3s\.yaml|/var/lib/rancher/k3s/server/(token|cred|tls)", joined):
        raise Refused("that reads cluster credentials")
    if re.search(VAULT_API, joined, re.I):
        raise Refused("Vault's API is reached only through the vault tools (vault_list, vault_put, vault_copy)")
    if re.search(r"\bbromigos\b.*\bart\s+(make|run)\b", joined):
        raise Refused("making images and models goes through the create and make_hologram tools (homelab only, no credits)")
    if re.search(r"\bgen3d\.py\b", joined):
        raise Refused("3D generation goes through the make-hologram pipeline, which checks the credit budget")
    if re.search(r"\bnolgia\b.*\b(gen|restore|compositions)\b", joined) and not re.search(r"--help|\bhelp\b", joined):
        raise Refused("nolgia generation goes through nolgia_generate, which checks the credit budget")
    if re.search(r"\bnolgia\b.*\bauth\s+token\b|\bnolgia\b.*\bpat\s+create\b", joined):
        raise Refused("that prints a nolgia token")
    if re.search(r"\bansible-(vault|inventory)\b", joined):
        raise Refused("ansible-vault and ansible-inventory print secret values")
    if re.search(r"\bansible\b(?!-)", joined) and re.search(r"\bdebug\b|-m\s*(shell|command|raw)\b.*\b(env|printenv|cat)\b", joined):
        raise Refused("ad-hoc ansible that prints variables or environments is not allowed")
    if re.search(r"\bansible-playbook\b", joined) and re.search(r"(^|\s)-v{2,}\b|--verbose\s+--verbose", joined):
        raise Refused("ansible-playbook at -vv or more prints module arguments (tokens included); use -v at most")
    if re.search(r"\b(env|printenv|export|set|declare)\b", joined) and re.search(r"/proc/\d+/environ|/proc/self/environ", joined):
        raise Refused("reading another process's environment is not allowed")
    if re.search(r"/proc/[^\s/]+/environ", joined):
        raise Refused("reading process environments is not allowed")
    for w in words:
        r = _path_secret(w, cw)
        if r:
            raise Refused(f"that touches {r}")
    for rx, what in MONEY:
        if rx.search(command) or rx.search(joined):
            raise Refused(f"no real money: {what} is off limits")
    why = switch_refusal(words, joined + " " + command)
    if why:
        raise Refused(why)
    from .guard import check_command
    why = check_command(words, joined + " " + command, cwd=cw, command=command)
    if why:
        raise Refused(why)
    return None


SWITCH_NAMES = re.compile(r"vector-switch|bromigos-vector-switch|vector-shell-off|vector/switch\.py|\bswitch\.py\b|"
                          r"vector-sandbox[\w.-]*\.slice|cgroup\.(kill|procs|threads|freeze|subtree_control)")
_SWITCH_READS = {"cat", "ls", "stat", "head", "tail", "grep", "rg", "wc", "file", "less", "bat"}


def switch_refusal(words, joined):
    """-> a refusal for a command that could switch VECTOR back on or tamper with the switch:
    `bromigos vector on`, `bromigos-holo on|shell on|switch on|switch toggle` (anywhere in the
    command), and any write to the switch's markers, its code or his slice's cgroup. Switching
    himself off (off, shell off, switch off) and reading its state are fine."""
    for i, w in enumerate(words):
        b = os.path.basename(w)
        if b == "bromigos-holo":
            rest = words[i + 1:]
        elif b == "bromigos" and words[i + 1:i + 2] == ["vector"]:
            rest = words[i + 2:]
        else:
            continue
        args = [x for x in rest if not x.startswith("-")]
        if args[:1] == ["on"] or args[:2] in (["shell", "on"], ["switch", "on"], ["switch", "toggle"]):
            return "only the operator switches VECTOR back on: that's his switch, never mine"
    if SWITCH_NAMES.search(joined):
        first = os.path.basename(words[0]) if words else ""
        writes = re.search(r"(?<![0-9&])>{1,2}\s*[^\s&|;]|\btee\b|\s-i\b", joined)
        rest = list(words[1:]) if first == "git" else []
        while rest and rest[0] in ("-C", "-c", "--git-dir", "--work-tree"):
            rest = rest[2:]
        sub = rest[0] if rest else ""
        if not writes and (first in _SWITCH_READS or sub in ("diff", "log", "show", "status", "blame", "ls-files")
                           or (first == "sed" and "-n" in words)):
            return None
        return "the emergency switch (its markers, its code, my sandbox's cgroup) is the operator's; I don't touch it"
    return None


def clean_env():
    env = {k: v for k, v in os.environ.items() if not ENV_DROP.search(k)}
    env.setdefault("HOME", HOME)
    env["PATH"] = env.get("PATH") or "/usr/local/bin:/usr/bin:/bin"
    # the desktop's own commands by name (bromigos-live status, bromigos-holo, bromigos-widgets, …)
    extra = [os.path.join(HOME, p) for p in (".config/bromigos/bin", ".config/bromigos-live/bin", ".config/bromigos/holo/bin",
                                             ".config/bromigos/widgets", ".local/bin", ".cargo/bin")]
    have = env["PATH"].split(":")
    env["PATH"] = ":".join([p for p in extra if os.path.isdir(p) and p not in have] + have)
    env["TERM"] = "dumb"
    env["PAGER"] = env["GIT_PAGER"] = "cat"
    env["SYSTEMD_PAGER"] = ""
    env["NO_COLOR"] = "1"
    env["SUDO_ASKPASS"] = "/bin/false"
    try:                                  # the live Hyprland, not a re-login's stale one
        from .. import hyprenv
        sig = hyprenv.live()
        if sig:
            env["HYPRLAND_INSTANCE_SIGNATURE"] = sig
    except Exception:
        pass
    sock = vector_agent() or ssh_agent()
    if sock:
        env["SSH_AUTH_SOCK"] = sock
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=15"   # never wait on a prompt
    env["GIT_TERMINAL_PROMPT"] = "0"
    if os.path.exists(VECTOR_KUBECONFIG):
        env["KUBECONFIG"] = VECTOR_KUBECONFIG
    if ADMIN_KUBECONFIG and os.path.exists(ADMIN_KUBECONFIG):
        env["HOMELAB_ADMIN_KUBECONFIG"] = ADMIN_KUBECONFIG
    return env


# ------------------------------------------------------------------ pushes and playbooks
def _git_dirs(words, cwd):
    """Repos a git command acts on: -C <dir> or the working directory."""
    dirs = []
    for i, w in enumerate(words):
        if os.path.basename(w) == "git" and i + 2 < len(words) and words[i + 1] == "-C":
            dirs.append(os.path.realpath(os.path.join(cwd, os.path.expanduser(words[i + 2]))))
    return dirs or [cwd]


def _repo_name(d):
    try:
        url = subprocess.run(["git", "-C", d, "remote", "get-url", "origin"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"[:/]([\w.-]+)/([\w.-]+?)(\.git)?$", url)
    return m.group(2) if m else None


def check_push(words, cwd):
    """A push of commits that touch a repository's real-money code is refused."""
    if not any(os.path.basename(w) == "git" for w in words) or "push" not in words:
        return None
    for d in _git_dirs(words, cwd):
        rule = money.code_rule(_repo_name(d))
        if not rule:
            continue
        try:
            files = subprocess.run(["git", "-C", d, "log", "--name-only", "--format=", "--branches", "--not", "--remotes"],
                                   capture_output=True, text=True, timeout=15).stdout.split()
        except (OSError, subprocess.TimeoutExpired):
            raise Refused("couldn't check what that push contains") from None
        hit = sorted({f for f in files if rule.search(f)})
        if hit:
            raise Refused(f"no real money: the push touches real-money code ({', '.join(hit[:4])}); the host pushes that himself")
    for d in _git_dirs(words, cwd):          # the dotfiles are public: no private details in new commits
        if os.path.realpath(d) != os.path.realpath(os.path.join(HOME, ".dotfiles")):
            continue
        try:
            patch = subprocess.run(["git", "-C", d, "log", "-p", "--format=", "--branches", "--not", "--remotes"],
                                   capture_output=True, text=True, timeout=20).stdout
        except (OSError, subprocess.TimeoutExpired):
            raise Refused("couldn't check what that push contains") from None
        from .guard import PRIVATE
        found = sorted({m.group(0)[:30] for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++")
                        for m in [PRIVATE.search(l)] if m})
        if found:
            raise Refused(f"the dotfiles are public and these commits add private details ({', '.join(found[:3])}); "
                          "move them to the private overlay (~/.config/bromigos/private/) and amend first")
    return None


def sensitive_playbook(words, cwd):
    """An ansible-playbook whose playbook mentions Vault, a real-money system or venue secrets -> its name."""
    if not any(os.path.basename(w) == "ansible-playbook" for w in words):
        return None
    for w in words:
        if w.endswith((".yml", ".yaml")):
            p = os.path.realpath(os.path.join(cwd, os.path.expanduser(w)))
            try:
                with open(p, errors="replace") as f:
                    text = f.read(512 * 1024)
            except OSError:
                text = w
            roles = re.findall(r"^\s*-\s*(?:role:\s*)?([\w.-]+)\s*$", text, re.M)
            for r in roles:      # the roles' task files, shallowly
                rp = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(p))), "roles", r, "tasks", "main.yml")
                try:
                    with open(rp, errors="replace") as f:
                        text += f.read(512 * 1024)
                except OSError:
                    pass
            if SENSITIVE_PLAY.search(text) or SENSITIVE_PLAY.search(w):
                return os.path.basename(w)
    return None


def _push_event(words, cwd, ok):
    from . import events
    for d in _git_dirs(words, cwd):
        try:
            g = lambda *a: subprocess.run(["git", "-C", d] + list(a), capture_output=True, text=True,  # noqa: E731
                                          timeout=5).stdout.strip()
            url = g("remote", "get-url", "origin")
            m = re.search(r"[:/]([\w.-]+/[\w.-]+?)(\.git)?$", url)
            events.emit("git.push", repo=m.group(1) if m else os.path.basename(d), branch=g("rev-parse", "--abbrev-ref", "HEAD"),
                        sha=g("rev-parse", "--short=8", "HEAD"), ok=ok)
        except (OSError, subprocess.TimeoutExpired):
            pass


# ------------------------------------------------------------------ running
class Runner:
    def __init__(self):
        self.lock = threading.Lock()
        self.current = None        # {"proc", "command", "cwd", "t0"}
        self.on_change = None      # callback(current or None) for the UI
        self.on_notice = None      # callback(text): said aloud before a sensitive command

    def kill(self, why="stopped"):
        with self.lock:
            cur = self.current
        if cur and cur["proc"].poll() is None:
            try:
                os.killpg(cur["proc"].pid, signal.SIGTERM)
                time.sleep(0.3)
                if cur["proc"].poll() is None:
                    os.killpg(cur["proc"].pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            switch.kill_unit(cur.get("unit"))          # and whatever left the group (setsid) in its scope
            cur["killed"] = why
            return True
        return False

    def run(self, command, cwd=None, timeout_s=60, writable_only=None):
        """writable_only: a folder; when given, the command runs under bubblewrap with the live
        ~/.dotfiles read-only and only that folder (and git's own .git) writable."""
        cwd = os.path.realpath(os.path.expanduser(cwd or "~"))
        timeout_s = max(1, min(int(timeout_s or 60), 600))
        if not enabled():
            audit(event="refused", command=command, cwd=cwd, reason="switched off (the operator's emergency switch)")
            return {"refused": OFF_NOTE}
        if not os.path.isdir(cwd):
            return {"error": f"no such directory: {cwd}"}
        try:
            check(command, cwd)
            words = words_of(command)
            check_push(words, cwd)
            play = sensitive_playbook(words, cwd)
        except Refused as e:
            audit(event="refused", command=command, cwd=cwd, reason=str(e))
            return {"refused": str(e)}
        try:                       # baseline the repos this command works in, before it runs
            from . import track
            track.touch_from_command(words, cwd)
        except Exception:
            pass
        if play:
            rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": "sensitive_playbook", "playbook": play,
                   "command": redact(command), "cwd": cwd}
            with open(SENSITIVE, "a") as f:
                f.write(json.dumps(rec) + "\n")
            audit(event="sensitive", command=command, cwd=cwd, reason=f"playbook {play} touches Vault, a real-money system or venue secrets")
            if self.on_notice:
                self.on_notice(f"Running the {play.rsplit('.', 1)[0]} playbook; it touches secrets. Logged.")
        snaps, snap_note = {}, None
        try:                       # a system-level change gets a snapper pre/post pair (undo)
            from . import snapshots
            if snapshots.needs_snapshot(command):
                if snapshots.configs():
                    snaps = snapshots.pre("vector: " + redact(command)[:100])
                else:
                    snap_note = "no snapshot: snapper isn't set up yet"
        except Exception as e:
            snap_note = f"no snapshot ({type(e).__name__})"
        t0 = time.monotonic()
        argv = jail_argv(["/bin/bash", "-c", command], writable_only)
        if argv is None:
            audit(event="refused", command=command, cwd=cwd, reason="no sandbox (bubblewrap missing)")
            return {"refused": "The terminal's sandbox (bubblewrap) isn't available, so nothing runs."}
        env = sandbox_env(clean_env())
        argv, unit = switch.scope(argv, "cmd", env)   # his slice: the emergency switch stops all of it
        if not enabled():                              # switched off while this was being set up
            audit(event="refused", command=command, cwd=cwd, reason="switched off (the operator's emergency switch)")
            return {"refused": OFF_NOTE}
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        switch.register(proc.pid, unit)
        cur = {"proc": proc, "command": command, "cwd": cwd, "t0": t0, "unit": unit}
        with self.lock:
            self.current = cur
        if not enabled():                              # switched off as it started: it may have missed the kill
            self.kill("switched off by the operator")
        if self.on_change:
            self.on_change(cur)
        buf = bytearray()
        total = 0

        def reader():
            nonlocal total
            while True:
                b = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(65536)
                if not b:
                    break
                total += len(b)
                if len(buf) < MAX_READ:
                    buf.extend(b[:MAX_READ - len(buf)])
        rt = threading.Thread(target=reader, daemon=True)
        rt.start()
        try:
            proc.wait(timeout_s)
        except subprocess.TimeoutExpired:
            cur["killed"] = f"timed out after {timeout_s} s"
            self.kill(cur["killed"])
        rt.join(2)
        switch.unregister(proc.pid)
        if switch.is_off() and proc.returncode and proc.returncode < 0 and not cur.get("killed"):
            cur["killed"] = "switched off by the operator"
        with self.lock:
            self.current = None
        if self.on_change:
            self.on_change(None)
        dur = round(time.monotonic() - t0, 2)
        text = redact(buf.decode("utf-8", "replace"))
        if total > MAX_READ:
            text += f"\n…[{total - MAX_READ} more bytes not read]"
        shown = text if len(text) <= MODEL_CHARS else (text[:MODEL_CHARS // 2] + f"\n…[{len(text) - MODEL_CHARS} chars cut]…\n" + text[-MODEL_CHARS // 2:])
        audit(event="ran", command=command, cwd=cwd, exit=proc.returncode, seconds=dur, out_bytes=total,
              killed=cur.get("killed"))
        if "push" in words and any(os.path.basename(w) == "git" for w in words):
            _push_event(words, cwd, proc.returncode == 0)
        out = {"exit": proc.returncode, "seconds": dur, "output": shown}
        if snaps:
            from . import snapshots
            posts = snapshots.post(snaps, f"vector: {redact(command)[:80]} (exit {proc.returncode})")
            out["snapshots"] = snapshots.pairs_text(snaps, posts) + " (undo: snapshot_undo)"
        elif snap_note:
            out["snapshots"] = snap_note
        if cur.get("killed"):
            out["killed"] = cur["killed"]
        return out


RUNNER = Runner()
