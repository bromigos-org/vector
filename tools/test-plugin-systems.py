#!/usr/bin/env python3
"""A user's own system through VECTOR's plugin seam (holo/vector/plugins.py) and their
real-money config (holo/vector/money.py), with made-up names: a garden plugin and a ledger.

1. The plugin's calls land where they should: its tool (with its hologram, its mood and its
   place on the MCP server), its feed (the gallery's live source, the parts' readings), its
   decks (hologram_deck's verbs and words), its switchboard link, its briefing fact and CI
   repos, its prompt lines and its voice words. Bad calls are refused, and a plugin can't
   take a core feed, prefix or tool name.
2. vector.toml's [real_money] (the user's) and $VECTOR_REAL_MONEY (one more file) add up:
   the terminal refuses the services and rules from both, act refuses the workload, the
   browser the console, the eyes its window, a push its code, and devflow's LIMITS gains
   the code only for a repository that already has a list.
3. Without either, the package names no one's system: no services, no hosts, no rules.

Everything runs in a child process whose HOME and XDG_RUNTIME_DIR are temporary folders:
nothing here reads the user's own plugins, config or overlay, or reaches the running VECTOR.
Run: python3 tools/test-plugin-systems.py (CI runs it).
"""
import os
import shutil
import subprocess
import sys
import tempfile

TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STUB_PRIVATE = '''
def data(): return {}
def present(): return False
def get(key, default=None): return {"endpoints.garden": "https://garden.example.org"}.get(key, default)
def url(name, path="", default=""): return default
def vault(name, default=""): return default
def vault_root(default=""): return default
def lan_domain(default=""): return default
def path(name, default=""): return default
PATH = ""
'''

PLUGIN = '''
def read():
    return {"beds": [{"name": "north", "moisture": 0.1}, {"name": "south", "moisture": 0.5}]}


def reading(key, data):
    if key == "gdn.beds":
        dry = [b["name"] for b in data["beds"] if b["moisture"] < 0.2]
        return ("warn" if dry else "ok"), 0.6, [f"{len(data['beds'])} beds", "dry: " + ", ".join(dry)]
    return None


def mood(d):
    return "concerned" if isinstance(d, dict) and d.get("dry") else None


def register(vector):
    vector.tool("garden", "The garden's beds, read only.", {"type": "object", "properties": {}},
                lambda: {"dry": ["north"]}, exhibit=("greenhouse", ["roof"]), mood=mood, mcp=True)
    vector.feed("garden", read, prefix="gdn.", reading=reading, label="GARDEN", keys=["gdn.beds", "x.y"])
    vector.deck("greenhouse", verbs=("focus",), about="open/close, focus <bed>: the beds, live")
    vector.switchboard("garden", "https://garden.example.org")
    vector.briefing("garden_night", lambda: {"dry": ["north"]}, about="the garden's night",
                    repos=["example-org/garden"])
    vector.persona("The greenhouse is the garden; [[ref]] waters it.")
    vector.words("floor", ["harvest"])
    for bad in (lambda: vector.feed("lab", read, prefix="gx.", reading=reading),
                lambda: vector.feed("garden2", read, prefix="gdn.", reading=reading),
                lambda: vector.feed("garden3", read, prefix="ws.", reading=reading),
                lambda: vector.tool("garden_w", "x", {}, read, level="desktop", mcp=True),
                lambda: vector.tool("garden_x", "x", {}, read, exhibit="greenhouse"),
                lambda: vector.words("robot", ["beep"])):
        try:
            bad()
            REFUSED.append(False)
        except ValueError:
            REFUSED.append(True)


REFUSED = []
'''

CONFIG = '''
[real_money]
services = ["ledger-live"]
hosts = ["@private:endpoints.garden"]
systems = ["ledger"]
titles = ['\\bledger\\b.*\\blive\\b']
about = "the ledger's live routes"

[real_money.code]
"bromigos-org/homelab" = ["helm/ledger/templates/live.yaml"]
"example-org/ledger" = ["engine/live/"]
'''

EXTRA = '''
[real_money]
services = ["vault-mover"]
refuse = [{match = '/api/sweep\\b', what = "the sweep route"}]
'''

CHILD = r'''
import json, os, re, subprocess, sys, tempfile
sys.path.insert(0, sys.argv[1])
mode = sys.argv[2]
bad = 0


def ok(cond, what):
    global bad
    print(("ok   " if cond else "FAIL ") + what)
    bad += 0 if cond else 1


from holo.vector import money, plugins, shell
if mode == "bare":
    ok(money.SERVICES == [] and money.HOSTS == [] and money.CODE == {} and money.SYSTEMS == [],
       "without a config the package names no one's system")
    ok(money.about() == "", "...and the persona lists none")
    plugins.load()
    ok(plugins.FEEDS == {} and plugins.DECKS == {} and plugins.PERSONA == [], "...and no plugin adds anything")
    for c in ("curl https://api.alpaca.markets/v2/orders", "curl -X POST https://x.example/live/arm",
              "curl -X POST https://x.example/api/intents", "echo LIVE_OPERATORS"):
        try:
            shell.check(c)
            ok(False, "the generic rules stay: " + c)
        except shell.Refused:
            ok(True, "the generic rules stay: " + c)
    sys.exit(1 if bad else 0)

from holo.vector import act, browser, devflow, eyes, mood, persona, text, tools
from holo import bind, live as livemod

# ---------------------------------------------------------------- 1. the plugin
plugins.load()
ok("garden" in plugins.LOADED and not [e for n, e in plugins.ERRORS if n == "garden"], "the garden plugin loads")
mod = sys.modules["vector_plugin_garden"]
ok(mod.REFUSED and all(mod.REFUSED), f"bad calls are refused ({sum(mod.REFUSED)}/{len(mod.REFUSED)})")
ok("garden" in tools.SPECS and tools.LEVEL["garden"] == "read", "its tool joins the table, read only")
ok(tools.EXHIBIT.get("garden") == ("greenhouse", ["roof"]), "its tool shows its hologram")
ok(mood.from_tool("garden", json.dumps({"dry": ["north"]})) == "concerned", "its mood reads the result")
ok(plugins.TOOLS["garden"]["mcp"], "it asks for the MCP server")
ok(tools.DECKS.get("greenhouse") == {"open", "close", "focus"}, "its deck joins hologram_deck, with open and close")
ok("Your own: greenhouse (open/close, focus <bed>: the beds, live)." in tools.SPECS["hologram_deck"][0],
   "hologram_deck says what it shows")
ok("[[DECKS]]" not in tools.SPECS["hologram_deck"][0], "no placeholder left in hologram_deck")
ok(tools._switchboard().get("garden") == "https://garden.example.org", "launch opens its link")
from holo.vector import briefing
ok("garden_night" in briefing._own() and "the garden's night since" in briefing.brief_style(),
   "its fact joins the brief, in order")
ok(livemod.plugin_feeds().get("garden", {}).get("prefix") == "gdn.", "its feed is a live source")
ok(bind.prefixes().get("gdn.") == "garden" and bind.KEYS.get("garden") == ["gdn.beds"], "its prefix and keys bind")
ok(bind.sources("greenhouse", [{"bind": "gdn.beds"}]) == ("garden",), "a model bound to it polls it")
L = livemod.Live()
ok("garden" in L.sources, "the gallery's live readings carry it")
L.sources["garden"].data, L.sources["garden"].at = mod.read(), 1.0
lv, v, lines, _ = bind.reading("gdn.beds", L)
ok(lv == "warn" and lines == ["2 beds", "dry: north"], "a part bound to it shows its reading")
lv, _, lines, _ = bind.reading("gdn.nothing", L)
ok(lv == "off", "an unknown key of it is off")
try:
    from holo import gallery                                   # OpenGL: not on every test machine
    ok(gallery.source_name("garden") == "GARDEN" and gallery.source_name("lab") == "LAB",
       "the gallery names it, and the lab")
except ImportError as e:
    print(f"skip the gallery's names ({e})")
p = persona.build()
ok("The greenhouse is the garden; the user waters it." in p, "its prompt lines join his, filled for the profile")
ok("[[OWN]]" not in p and "[[LAB]]" not in p and "[[MONEY]]" not in p, "no placeholder left in the prompt")
ok(text._own_words()["floor"].search("the harvest is in"), "its words call for the floor voice")

# ---------------------------------------------------------------- 2. real money
ok(set(money.SERVICES) == {"ledger-live", "vault-mover"}, "vector.toml and $VECTOR_REAL_MONEY add up")
ok("ledger-live" in persona.build() and "the ledger's live routes" in persona.build(), "the prompt names them")


def refused(c):
    try:
        shell.check(c)
        return False
    except shell.Refused as e:
        return "real money" in str(e)


for c in ("kubectl -n ledger scale deploy/ledger-live --replicas=0", "helm upgrade x-ledger-live ./c",
          "kubectl logs svc/ledger-live.ledger", "systemctl --user restart vault-mover",
          "curl -X POST https://ledger.example/api/sweep"):
    ok(refused(c), "the terminal refuses: " + c)
for c in ("kubectl -n ledger logs deploy/ledger-paper", "echo ledger-lively"):
    ok(not refused(c), "the terminal allows: " + c)
for n, want in (("ledger-live", True), ("ledger-live-abc", True), ("ledger-paper", False), ("vault-mover", True)):
    try:
        act._name(n)
        ok(not want, f"act {'refuses' if want else 'allows'} {n}")
    except PermissionError:
        ok(want, f"act {'refuses' if want else 'allows'} {n}")
ok(bool(browser.site_blocked("https://garden.example.org/beds")), "the browser refuses the console (@private: host)")
ok(any(re.search(rx, "Ledger — Live desk", re.I) for rx in eyes._conf()["titles"]), "the eyes refuse its window")
ok(bool(shell.SENSITIVE_PLAY.search("- role: ledger")), "a playbook naming it is announced")
ok(devflow.touches_limits("bromigos-org/homelab", ["helm/ledger/templates/live.yaml"]) != [],
   "LIMITS: its code joins a repository that has a list")
ok(devflow.touches_limits("example-org/ledger", ["README.md"]) == ["README.md"],
   "LIMITS: a repository without a list stays all the operator's")
d = tempfile.mkdtemp()
g = lambda *a: subprocess.run(["git", "-C", d] + list(a), capture_output=True, text=True, check=True)
g("init", "-q", "-b", "main")
g("remote", "add", "origin", "git@github.com:example-org/ledger.git")
os.makedirs(os.path.join(d, "engine/live"))
open(os.path.join(d, "engine/live/x.go"), "w").write("x")
g("add", "-A")
g("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "x")
try:
    shell.check_push(shell.words_of("git push origin main"), d)
    ok(False, "a push touching its code is refused")
except shell.Refused:
    ok(True, "a push touching its code is refused")
ok(money.matches("console/app/x/wallet/p.tsx", "console/app/*/wallet/") and
   not money.matches("console/app/x/y/wallet/p.tsx", "console/app/*/wallet/") and
   money.matches("engine/migrations/0042_live.sql", "engine/migrations/*live*"), "code paths: folders and globs")
print("all passed" if not bad else f"{bad} FAILED")
sys.exit(1 if bad else 0)
'''


def run(mode):
    tmp = tempfile.mkdtemp(prefix="vector-plugin-test-")
    home, rundir = os.path.join(tmp, "home"), os.path.join(tmp, "run")
    os.makedirs(rundir, mode=0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DISPLAY", "WAYLAND", "HYPRLAND", "GH_", "GITHUB_",
                                                                     "XDG_", "VECTOR_", "BROMIGOS_", "PYTHON"))}
    import site
    env.update(HOME=home, XDG_RUNTIME_DIR=rundir, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1",
               BROMIGOS_VECTOR_NO_BAR="1", BROMIGOS_BIN="",
               PYTHONUSERBASE=site.getuserbase())      # the packages pip installed for this user (CI)
    stub = os.path.join(tmp, "stub")
    os.makedirs(stub)
    with open(os.path.join(stub, "bromigos_private.py"), "w") as f:     # made-up overlay, whatever is installed
        f.write(STUB_PRIVATE)
    env["PYTHONPATH"] = stub
    overlay = os.path.join(tmp, "overlay.json")                             # the same, for an installed one
    with open(overlay, "w") as f:
        f.write('{"endpoints": {"garden": "https://garden.example.org"}}')
    env["BROMIGOS_PRIVATE_CONFIG"] = overlay
    if mode == "own":
        cfg = os.path.join(home, ".config/bromigos")
        os.makedirs(os.path.join(cfg, "plugins/vector"))
        os.makedirs(os.path.join(cfg, "vector"))
        with open(os.path.join(cfg, "plugins/vector/garden.py"), "w") as f:
            f.write(PLUGIN)
        with open(os.path.join(cfg, "vector/vector.toml"), "w") as f:
            f.write(CONFIG)
        extra = os.path.join(tmp, "extra.toml")
        with open(extra, "w") as f:
            f.write(EXTRA)
        env["VECTOR_REAL_MONEY"] = extra
    else:
        os.makedirs(home)
    script = os.path.join(tmp, "child.py")
    with open(script, "w") as f:
        f.write(CHILD)
    try:
        return subprocess.run([sys.executable, script, TREE, mode], env=env, cwd=home, timeout=300).returncode
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(run("bare") or run("own"))
