"""Where VECTOR's code, settings and plugins are, wherever he is installed.

ROOT is this tree: /usr/lib/bromigos/vector when packaged (bromigos-vector), or a
checkout (the repo, or the operator's old dotfiles copy). Settings resolve per file, the
first that exists winning:

  ~/.config/bromigos/vector/<name>     the user's own (voice.json, eyes.json, vector.toml)
  ~/.config/bromigos/holo/<name>       the old dotfiles location, read until it moves
  /etc/bromigos/vector/<name>          the machine's (root-owned)
  ROOT/<name>                          the package's defaults

LOCKED files are never read from a folder the user (or VECTOR's terminal) can write:
nolgia.json is his credit budget, so it comes from /etc or the package only.

Plugins (docs/plugins.md) are Python modules in PLUGIN_DIRS: the homelab's, the user's,
then installed extensions' (`bromigos ext`: links in ~/.local/share/bromigos/ext/active/
vector, recorded with their sha256 in the ledger, EXT_LEDGER). This file decides where his
settings and plugins come from, so it is protected safety code (guard.py).
"""
import os
import sys

HOME = os.path.expanduser("~")
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
PACKAGED = ROOT.startswith("/usr/")
CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "bromigos")
USER = os.path.join(CONFIG, "vector")
LEGACY = os.path.join(CONFIG, "holo")
ETC = "/etc/bromigos/vector"
LOCKED = {"nolgia.json"}
# bromigos-homelab's VECTOR plugins (its homelab tools and Gnosis memory: docs/homelab.md),
# packaged and root-owned, then the user's own; $BROMIGOS_HOMELAB points at another
# homelab tree (a test of a bromigOS checkout).
HOMELAB = next((d for d in (os.environ.get("BROMIGOS_HOMELAB"), "/usr/lib/bromigos/homelab") if d and os.path.isdir(d)), None)



def _xdg(var, default):
    v = os.environ.get(var) or ""
    return v if os.path.isabs(v) else os.path.join(HOME, default)


# Extensions (bromigos ext, docs/extensions.md): checkouts in EXT/src, the links the
# loaders read in EXT_ACTIVE/<seam>, and the ledger of what bromigos ext installed (the
# guard accepts a change in his plugin folders only when it matches the ledger).
EXT = os.path.join(_xdg("XDG_DATA_HOME", ".local/share"), "bromigos", "ext")
EXT_ACTIVE = os.path.join(EXT, "active")
EXT_STATE = os.path.join(_xdg("XDG_STATE_HOME", ".local/state"), "bromigos", "ext")
EXT_LEDGER = os.path.join(EXT_STATE, "ledger.json")
# bromigOS's lab job queue (bromigos lab run|jobs, bromigos-lab.service): its jobs and logs.
# His lab tools (lab.py) submit through `bromigos lab`, from the daemon; his terminal reads
# it only (a job file written by hand could ask for the paid cloud).
LAB_STATE = os.path.join(_xdg("XDG_STATE_HOME", ".local/state"), "bromigos", "lab")
PLUGIN_DIRS = ([os.path.join(HOMELAB, "vector")] if HOMELAB else []) + [os.path.join(CONFIG, "plugins", "vector"),
                                                                         os.path.join(EXT_ACTIVE, "vector")]

# Shared libs: bromigos-core's (bromigos_private, bromigos_theme: /usr/lib/bromigos/lib
# next to this tree when packaged, ~/.config/bromigos/lib beside the dotfiles copy), then
# this tree's own (privacy_guard), then the dotfiles' as a last resort.
LIB_DIRS = [d for d in dict.fromkeys([os.path.join(os.path.dirname(ROOT), "lib"), os.path.join(ROOT, "lib"),
                                      os.path.join(CONFIG, "lib"), "/usr/lib/bromigos/lib"] +
                                     ([os.path.join(HOMELAB, "lib")] if HOMELAB else [])) if os.path.isdir(d)]
for _d in reversed(LIB_DIRS):
    if _d not in sys.path:
        sys.path.insert(0, _d)


def conf(name):
    """The settings file `name` in force (see above); ROOT's default when none other exists."""
    dirs = ([] if name in LOCKED else [USER] + ([LEGACY] if os.path.realpath(LEGACY) != ROOT else [])) + [ETC, ROOT]
    for d in dirs:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return os.path.join(ROOT, name)


def command(name, *legacy):
    """A desktop command by name: the package's /usr/bin first, then the old dotfiles paths."""
    for p in [os.path.join("/usr/bin", name)] + [os.path.join(HOME, x) for x in legacy]:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return os.path.join("/usr/bin", name)
