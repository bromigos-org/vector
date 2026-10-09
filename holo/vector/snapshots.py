"""Undo for VECTOR's system-level changes, with snapper (configs `root` and `home`, set up by
~/.config/bromigos/system/setup-snapshots.sh with ALLOW_USERS = the operator, so no sudo).

  * Automatic: his terminal wraps every system-level command (package installs into user
    space, pip/npm/cargo installs, systemctl --user enable/disable/mask, ansible against this
    machine, edits under /etc or ~/.config outside the dotfiles) in a pre/post snapshot pair
    on every configured config, described with the command. The numbers come back with the
    result, and go in his VECTOR-CHANGELOG.md entry.
  * snapshot_create(description) / snapshot_list() / snapshot_undo(): "VECTOR, undo that"
    reverts the last pair with `snapper undochange pre..post` (files only; a running service
    may need a restart). A whole-system rollback is the host's: boot the snapshot from the
    GRUB menu ("Arch Linux snapshots"), then `snapper rollback`.
  * Until snapper is configured, everything here skips gracefully and says so.
Pairs are recorded in ~/.local/state/bromigos/vector-snapshots.jsonl.
"""
import json
import os
import re
import shutil
import subprocess
import time

HOME = os.path.expanduser("~")
LOG = os.path.join(HOME, ".local/state/bromigos/vector-snapshots.jsonl")
SETUP = "~/.config/bromigos/system/setup-snapshots.sh"
CONFIGS = ("root", "home")
SYSTEM_CHANGE = re.compile(
    r"\b(pacman|yay|paru|pikaur)\b.*\s-(S|R|U)\w*|\bflatpak\s+(install|uninstall|update)\b|"
    r"\b(pip3?|pipx|uv\s+tool|npm|pnpm|cargo|go)\b[^|;]*\b(install|uninstall|add|remove)\b|"
    r"\bsystemctl\s+--user\s+(enable|disable|mask|unmask|edit|link|preset)\b|"
    r"\bansible-playbook\b[^|;]*(localhost|127\.0\.0\.1|workstation|--connection[= ]local|-c\s+local)|"
    r"(>{1,2}|\btee\b|\bsed\s+-i\b|\bcp\b|\bmv\b|\brm\b|\bln\b|\binstall\b)[^|;]*\s(/etc/|~/\.config/|\$HOME/\.config/|"
    + re.escape(HOME) + r"/\.config/)(?!bromigos)", re.I)


def _snapper(*args, timeout=60):
    return subprocess.run(["snapper", *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def configs():
    """Configured snapper configs this user may use (empty until setup-snapshots.sh ran)."""
    if not shutil.which("snapper"):
        return []
    r = _snapper("--jsonout", "list-configs", timeout=15)
    try:
        names = [c["config"] for c in json.loads(r.stdout or "{}").get("configs", [])]
    except ValueError:
        names = []
    return [c for c in CONFIGS if c in names]


def _skip():
    return {"skipped": f"snapper isn't set up yet; Sir runs {SETUP} once (it needs sudo)"}


def needs_snapshot(command):
    return bool(SYSTEM_CHANGE.search(command or ""))


def pre(description):
    """-> {config: number} for a pre snapshot on every configured config ({} when none)."""
    out = {}
    for c in configs():
        r = _snapper("-c", c, "create", "-t", "pre", "-p", "-c", "number", "-d", description[:120],
                     "--userdata", "important=yes,by=vector")
        if r.returncode == 0 and r.stdout.strip().isdigit():
            out[c] = int(r.stdout.strip())
    return out


def post(pres, description):
    out = {}
    for c, n in (pres or {}).items():
        r = _snapper("-c", c, "create", "-t", "post", "--pre-number", str(n), "-p", "-c", "number",
                     "-d", description[:120], "--userdata", "by=vector")
        if r.returncode == 0 and r.stdout.strip().isdigit():
            out[c] = int(r.stdout.strip())
    if out:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "what": description[:200],
                                "pairs": {c: [pres[c], out[c]] for c in out}}) + "\n")
    return out


def pairs_text(pres, posts):
    return ", ".join(f"{c} {pres[c]}→{posts.get(c, '?')}" for c in pres)


# ------------------------------------------------------------------ tools
def snapshot_create(description):
    """A single snapshot of every configured config (before something the host wants to be able to undo)."""
    cs = configs()
    if not cs:
        return _skip()
    out = {}
    for c in cs:
        r = _snapper("-c", c, "create", "-p", "-c", "number", "-d", (description or "vector")[:120],
                     "--userdata", "important=yes,by=vector")
        if r.returncode == 0:
            out[c] = int(r.stdout.strip())
        else:
            out[c] = f"failed: {r.stderr.strip()[:120]}"
    return {"ok": True, "snapshots": out}


def snapshot_list(limit=10):
    cs = configs()
    if not cs:
        return _skip()
    out = {}
    for c in cs:
        r = _snapper("--jsonout", "-c", c, "list")
        try:
            items = json.loads(r.stdout or "{}").get(c, [])
        except ValueError:
            items = []
        out[c] = [{"number": i.get("number"), "type": i.get("type"), "date": i.get("date"),
                   "description": i.get("description"), "pre": i.get("pre-number")} for i in items[-int(limit):]]
    pairs = []
    try:
        with open(LOG) as f:
            pairs = [json.loads(l) for l in f][-5:]
    except (OSError, ValueError):
        pass
    return {"snapshots": out, "vector_pairs": pairs}


def snapshot_undo(pair=None):
    """Undo VECTOR's last system-level change (or the pair given as 'config:pre..post')."""
    cs = configs()
    if not cs:
        return _skip()
    todo = {}
    if pair:
        m = re.fullmatch(r"(root|home):(\d+)\.\.(\d+)", pair.strip())
        if not m:
            raise ValueError("pair looks like home:12..13")
        todo[m.group(1)] = (int(m.group(2)), int(m.group(3)))
        what = pair
    else:
        try:
            with open(LOG) as f:
                last = [json.loads(l) for l in f][-1]
        except (OSError, ValueError, IndexError):
            return {"ok": False, "detail": "no change of mine to undo (no snapshot pair recorded)"}
        todo = {c: tuple(v) for c, v in last["pairs"].items()}
        what = last["what"]
    out = {}
    for c, (a, b) in todo.items():
        r = _snapper("-c", c, "undochange", f"{a}..{b}", timeout=300)
        out[c] = (r.stdout.strip().splitlines()[-1:] or ["done"])[0] if r.returncode == 0 else f"failed: {r.stderr.strip()[:160]}"
    return {"ok": all(not v.startswith("failed") for v in out.values()), "undid": what, "result": out,
            "note": "files are back as they were before; a service that read them may need a restart. A whole-system "
                    "rollback is Sir's: boot the snapshot from the GRUB menu, then snapper rollback."}
