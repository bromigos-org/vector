"""VECTOR's plugin seam (docs/plugins.md): the user's own tools, memory stores and skills,
as Python modules in ~/.config/bromigos/plugins/vector/ (paths.PLUGIN_DIRS).

A plugin is a module with a register(vector) function:

    def register(vector):
        vector.tool("lab_status", "What the lab is doing ...", {"type": "object", ...},
                    lab_status, level="read", private_args=())
        vector.memory("gnosis", GnosisMemory)       # a factory: () -> a store (memory.py's interface)
        vector.skills("/path/to/skills")            # a folder of skill .md files

and, for a system of the user's own (a trading desk, a garden, a game server):

        vector.feed("garden", read, prefix="gdn.", reading=reading, label="GARDEN")   # hologram readings
        vector.deck("garden", verbs=("open", "close"), about="the garden's beds")     # a live-layer deck
        vector.switchboard("garden", "https://garden.example.org")                   # a launch target
        vector.briefing("garden", summary, about="the garden's night")              # a return brief's fact
        vector.persona("The greenhouse is the garden's ...")                          # lines for his prompt
        vector.words("floor", ["harvest"])                                            # a voice role's words

Limits (enforced here, not by the plugin):
  * level is "read" (looks things up) or "desktop" (changes something on this desktop);
    never the terminal: run_shell and its limits stay in the core alone;
  * a plugin can't replace or shadow a core tool, or another plugin's;
  * names are lower_snake_case, 3-40 characters;
  * every plugin tool runs through tools.call: the same audit log, event feed and
    stop-after-a-block rule as the core's;
  * a plugin that fails to load is skipped and named in ERRORS (my_setup shows it).

The plugin folders are protected (guard.py): VECTOR can't write his own plugins, and a
change to one switches his terminal off like a change to his safety code. So is this file.
"""
import glob
import importlib.util
import os
import re
import sys

from ..paths import PLUGIN_DIRS

LEVELS = ("read", "desktop")
NAME = re.compile(r"[a-z][a-z0-9_]{2,39}$")

TOOLS = {}        # name -> {"description", "parameters", "fn", "level", "plugin", "private_args",
                  #          "exhibit", "mood", "mcp"}
MEMORY = {}       # name -> factory
SKILL_DIRS = []   # extra skill folders
FEEDS = {}        # name -> {"read", "every", "prefix", "reading", "label", "keys", "plugin"}
DECKS = {}        # live-layer deck -> {"verbs", "about", "plugin"}
SWITCHBOARD = {}  # launch target -> URL
BRIEFING = {}     # fact name -> {"fn", "about", "repos", "plugin"}
PERSONA = []      # lines for the system prompt, in load order
WORDS = {}        # voice role -> [words that call for it]
ROLES = ("floor", "scientist")
CORE_FEEDS = ("local", "lab")
LOADED = []       # plugin module names, in load order
ERRORS = []       # (plugin, "Type: message")
_done = False


class API:
    def __init__(self, plugin):
        self.plugin = plugin

    def tool(self, name, description, parameters, fn, level="read", private_args=(), exhibit=None, mood=None,
             mcp=False):
        if not NAME.match(name or ""):
            raise ValueError(f"tool name {name!r}: lower_snake_case, 3-40 characters")
        if level not in LEVELS:
            raise ValueError(f"tool {name}: level must be one of {LEVELS} (never the terminal)")
        if name.startswith(("run_", "build_", "shell", "vault_")):          # (and a core tool's name: tools.py drops it)
            raise ValueError(f"tool {name}: that name belongs to VECTOR's core")
        if name in TOOLS:
            raise ValueError(f"tool {name}: already registered by {TOOLS[name]['plugin']}")
        if not callable(fn) or not isinstance(parameters, dict) or not str(description or "").strip():
            raise ValueError(f"tool {name}: needs a description, a JSON-schema dict and a function")
        if exhibit is not None and not (isinstance(exhibit, (tuple, list)) and len(exhibit) == 2
                                        and isinstance(exhibit[0], str) and isinstance(exhibit[1], (tuple, list))):
            raise ValueError(f"tool {name}: exhibit is (model, [parts])")
        if mood is not None and not callable(mood):
            raise ValueError(f"tool {name}: mood is a function of the result")
        if mcp and level != "read":
            raise ValueError(f"tool {name}: only a read tool goes on the MCP server")
        TOOLS[name] = {"description": str(description), "parameters": parameters, "fn": fn, "level": level,
                       "plugin": self.plugin, "private_args": tuple(private_args),
                       "exhibit": (exhibit[0], list(exhibit[1])) if exhibit else None, "mood": mood, "mcp": bool(mcp)}

    def memory(self, name, factory):
        if not NAME.match(name or "") or name == "local" or not callable(factory):
            raise ValueError(f"memory store {name!r}: a lower_snake_case name (not 'local') and a factory")
        MEMORY.setdefault(name, factory)

    def skills(self, path):
        path = os.path.realpath(os.path.expanduser(path))
        if os.path.isdir(path) and path not in SKILL_DIRS:
            SKILL_DIRS.append(path)

    def feed(self, name, read, prefix, reading, label=None, every=30.0, keys=()):
        """A live source for the holograms: read() -> data, polled every `every` seconds while
        a model on the table needs it; a part bound to "<prefix>..." shows reading(key, data)
        -> (level, value 0..1, [lines]) or None. label names it under the model."""
        if not NAME.match(name or "") or name in CORE_FEEDS or name in FEEDS:
            raise ValueError(f"feed {name!r}: a new lower_snake_case name")
        if not re.fullmatch(r"[a-z]{2,12}\.", prefix or "") or prefix in ("ws.", "wick.", "rack.", "emb."):
            raise ValueError(f"feed {name}: prefix is a new short word and a dot, such as 'gdn.'")
        if any(f["prefix"] == prefix for f in FEEDS.values()):
            raise ValueError(f"feed {name}: prefix {prefix} is taken")
        if not callable(read) or not callable(reading):
            raise ValueError(f"feed {name}: needs read() and reading(key, data)")
        FEEDS[name] = {"read": read, "every": max(5.0, float(every)), "prefix": prefix, "reading": reading,
                       "label": str(label or name.upper()), "keys": [k for k in keys if str(k).startswith(prefix)],
                       "plugin": self.plugin}

    def deck(self, name, verbs=("open", "close"), about=""):
        """A deck of the live layer's that the user's own plugins bring (bromigOS docs/plugins.md),
        for hologram_deck: its verbs, and what it shows in a phrase."""
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,30}", name or "") or name in DECKS:
            raise ValueError(f"deck {name!r}: a new lower-case name")
        vs = {v for v in verbs if re.fullmatch(r"[a-z]{2,12}", v or "")} | {"open", "close"}
        DECKS[name] = {"verbs": vs, "about": str(about or ""), "plugin": self.plugin}

    def switchboard(self, name, url):
        """A launch target by name ("open the garden"): an http(s) URL."""
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,30}", name or ""):
            raise ValueError(f"switchboard {name!r}: a lower-case name")
        if url and re.fullmatch(r"https?://[^\s\"'<>]{3,500}", url):
            SWITCHBOARD.setdefault(name, url)

    def briefing(self, name, fn, about="", repos=()):
        """A fact for his briefs ("brief me", the return brief): fn() -> a small dict or list.
        about says what it is, for the brief's order; repos adds GitHub repos whose failed CI
        the brief reports."""
        if not NAME.match(name or "") or name in BRIEFING or not callable(fn):
            raise ValueError(f"briefing {name!r}: a new lower_snake_case name and a function")
        BRIEFING[name] = {"fn": fn, "about": str(about or ""), "plugin": self.plugin,
                          "repos": [r for r in repos if re.fullmatch(r"[\w.-]+/[\w.-]+", r or "")]}

    def persona(self, text):
        """Lines for his system prompt, about the user's own systems (no limits: those are code)."""
        text = str(text or "").strip()
        if text and len(text) <= 4000:
            PERSONA.append(text)

    def words(self, role, words):
        """Words that call for a voice role when a reply carries no markers (text.py)."""
        if role not in ROLES:
            raise ValueError(f"words: role is one of {ROLES}")
        WORDS.setdefault(role, []).extend(w.lower() for w in words if re.fullmatch(r"[\w&'-]{2,30}", w or ""))


def files():
    """The plugin modules, in name order (a leading _ or . means off)."""
    out = []
    for d in PLUGIN_DIRS:
        out += [f for f in sorted(glob.glob(os.path.join(d, "*.py")))
                if not os.path.basename(f).startswith(("_", "."))]
    return out


def load():
    """Import every plugin once and call its register(). Safe to call again."""
    global _done
    if _done:
        return
    _done = True
    for path in files():
        stem = os.path.splitext(os.path.basename(path))[0]
        modname = "vector_plugin_" + re.sub(r"\W", "_", stem)
        try:
            spec = importlib.util.spec_from_file_location(modname, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[modname] = mod
            spec.loader.exec_module(mod)
            reg = getattr(mod, "register", None)
            if not callable(reg):
                raise ValueError("no register(vector) function")
            reg(API(stem))
            LOADED.append(stem)
        except Exception as e:
            ERRORS.append((stem, f"{type(e).__name__}: {str(e)[:160]}"))
            print(f"vector plugins: {stem} skipped ({type(e).__name__}: {str(e)[:160]})", flush=True)
