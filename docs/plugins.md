# VECTOR plugins

The `bromigos-vector` package is VECTOR's generic core. Anything that belongs to one
person or one machine is a plugin instead. Examples are tools for your own services, a
memory store on your own server, or your own know-how.

A plugin is a Python module. VECTOR loads plugins once, when he starts
(`holo/vector/plugins.py`). He reads these folders, in order:

1. `/usr/lib/bromigos/homelab/vector/`, bromigos-homelab's plugins
2. `~/.config/bromigos/plugins/vector/`, your own
3. `~/.local/share/bromigos/ext/active/vector/`, installed extensions'

A file whose name starts with `_` or `.` is skipped. bromigOS's live layer and widgets have
the same kind of plugin seam (bromigOS `docs/plugins.md`).

## Write a plugin

Start from a working example:

```bash
bromigos ext new vector-tool garden
```

Or write the module yourself, in `~/.config/bromigos/plugins/vector/garden.py`:

```python
def garden_status(bed=""):
    return {"bed": bed or "all", "moisture": 0.41, "next_watering": "18:00"}


def register(vector):
    vector.tool("garden_status", "Soil moisture and the next watering, from the garden sensor.",
                {"type": "object", "properties": {"bed": {"type": "string"}}},
                garden_status, level="read")
```

Restart VECTOR to load it:

```bash
bromigos-holo restart
```

Then ask him "how are you set up?" (`my_setup`). He lists the plugins that loaded, and
the error for any that didn't. A plugin that fails to load is skipped.

## The `register(vector)` calls

| Call | What it adds |
|---|---|
| `tool(name, description, parameters, fn, level="read", private_args=(), exhibit=None, mood=None, mcp=False)` | A tool the brain can call |
| `memory(name, factory)` | A memory store |
| `skills(path)` | A folder of skills |
| `feed(name, read, prefix, reading, label=None, every=30, keys=())` | Live readings for your own hologram models |
| `deck(name, verbs=("open", "close"), about="")` | One of your live-layer decks, for `hologram_deck` |
| `switchboard(name, url)` | A web page `launch` opens by name |
| `briefing(name, fn, about="", repos=())` | A fact for his briefs |
| `persona(text)` | Lines for his system prompt |
| `words(role, words)` | Words that call for a voice role |

The last six are for a system of your own, such as a trading desk or a game server.
[Your own systems](#your-own-systems) shows them together.

### `tool`

- `parameters` is a JSON schema. `fn(**args)` returns a dict, a list or a string.
- `level` is `"read"` (looks something up) or `"desktop"` (changes something on this
  desktop). A plugin can never add a terminal tool. `run_shell` and its limits belong to
  the core alone.
- `name` is lower_snake_case, 3 to 40 characters. It can't be a core tool's name, or start
  with `run_`, `build_`, `shell` or `vault_`. Two plugins can't register the same name.
- `private_args` names arguments to keep private. The event feed leaves them out, and
  the audit log keeps only a hash, the length and the first 40 characters.
- `exhibit` is `(model, [parts])`, the hologram he shows while the tool answers, with the
  parts worth calling out.
- `mood` is a function of the tool's result that returns `"calm"`, `"concerned"`,
  `"alarmed"` or `None`. A result that shows trouble sets his mood even if his reply
  forgets to say so.
- `mcp=True` also offers a `read` tool on his MCP server, next to the core's read tools.

Every plugin tool runs through the core's tool gate, `tools.call()`. It gets the same
audit log, event feed and stop-after-a-block rule as a core tool
([safety.md](safety.md#tool-levels-and-the-gate)).

### `memory`

`factory` is a function that returns a store. The name can't be `local`, which is the
core's own store. [Memory stores](#memory-stores) describes the interface.

### `skills`

`path` is a folder of skills, each `<name>.md` or `<name>/SKILL.md`, in the format
[skills/README.md](../skills/README.md) describes. When two skills share a name, VECTOR
uses the first he finds, in this order:

1. your own, in `~/.config/bromigos/skills/`
2. installed extensions'
3. plugins'
4. the package's

## Your own systems

A plugin can bring a whole system of yours to VECTOR: its read tools, live readings for a
hologram model of it, its decks on the desktop, its facts in his briefs and a few lines
about it in his prompt. This example is a garden:

```python
import json
import urllib.request

URL = "https://garden.example.org"


def read():
    with urllib.request.urlopen(URL + "/api/beds", timeout=10) as r:
        return json.load(r)


def reading(key, data):
    if key == "gdn.beds":
        dry = [b["name"] for b in data["beds"] if b["moisture"] < 0.2]
        return ("warn" if dry else "ok"), 0.6, [f"{len(data['beds'])} beds", f"dry: {', '.join(dry) or 'none'}"]
    return None


def register(vector):
    vector.tool("garden", "The garden's beds, read only.", {"type": "object", "properties": {}},
                read, exhibit=("greenhouse", ["roof"]), mcp=True)
    vector.feed("garden", read, prefix="gdn.", reading=reading, label="GARDEN", keys=["gdn.beds"])
    vector.deck("garden", about="open/close only: the beds, live")
    vector.switchboard("garden", URL)
    vector.briefing("garden", read, about="the garden's night")
    vector.persona("The greenhouse is the garden. Its facts come from the garden tool.")
    vector.words("floor", ["harvest"])
```

What each call does:

- **`feed`** gives your own hologram model live readings. A part whose `bind` starts with
  `prefix` shows `reading(key, data)`. That's a level (`ok`, `warn`, `crit` or `off`), a
  brightness from 0 to 1 and a few lines. `read()` runs every `every` seconds while the
  model is on the table. `label` names the source under the model.
- **`deck`** lets `hologram_deck` open and drive your deck. The deck itself is a
  live-layer plugin (bromigOS `docs/plugins.md`). `about` says what it shows.
- **`switchboard`** makes "open the garden" open the URL.
- **`briefing`** adds `fn()` to "brief me" and to the return brief. `about` puts it in the
  brief's order. `repos` adds GitHub repositories whose failed CI the brief reports.
- **`persona`** adds the text to his system prompt. It's for facts and flavour. His limits
  are code, never prompt lines.
- **`words`** adds words that move a sentence to a voice role when a reply carries no
  markers. `floor` is for markets, `scientist` for technology.

Your system's real-money services don't go in a plugin. Name them in `vector.toml`'s
`[real_money]` table ([configuration.md](configuration.md#real-money)), where his limits
read them.

## Memory stores

`vector.toml`'s `[memory] backend` picks the store
([configuration.md](configuration.md#memory)). A store has the core's interface, from
`holo/vector/memory.py`:

| Method | Returns |
|---|---|
| `recall(query, k=5, budget=0.2)` | `(lines, stats)` within `budget` seconds. `stats` has `recall_ms`, `recall_source` and `recalled` |
| `remember(text, kind="note")` | `{"ok": True, ...}` at once. Write in the background |
| `forget(what="")` | `{"ok": ..., "forgot" or "detail"}`. `""` or `"that"` means the last note |
| `summarize(turns)` | `True`, `False` or `None`, at the end of a conversation |
| `warm()`, `refresh()` | Warms the hot path, or reloads |
| `last_written` | The ids of the last note |
| `use_scratch(folder, fresh=False)` | Optional. Keeps local files in `folder`, for the evals |

The core's helpers in `holo.vector.memory` are there to use:

- `embed(texts)` returns unit embeddings from the model endpoint.
- `audit(what, **kw)` writes the audit log.
- `digest(text)` turns text into a hash, a length and the first 40 characters, for the
  audit log.

## Share a plugin as an extension

To share a plugin, publish it as a bromigOS extension (bromigOS `docs/extensions.md`).
`bromigos ext install <git url>` links the extension's parts into place:

| Extension part | Lands in |
|---|---|
| `vector-tool` or `vector-memory` | `~/.local/share/bromigos/ext/active/vector/` |
| `vector-skill` | `~/.local/share/bromigos/ext/active/skills/` |

Before it installs, `bromigos ext` shows you the extension's permissions and the VECTOR
level it declares, `read` or `desktop`. `bromigos ext new vector-tool <name>`,
`vector-memory <name>` and `vector-skill <name>` make working starters.

## Plugins and the guard

Plugin folders are protected ([safety.md](safety.md#the-guard)):

- VECTOR's terminal refuses to write to them, and his build loop refuses changes to them.
  He never writes his own plugins.
- If a file in one changes while he runs, the guard's sentinel records it in its audit
  log (`bromigos vector guard log`). It doesn't switch him off.
- Restart VECTOR to load a plugin you changed on purpose.

Extensions are checked against the record `bromigos ext` keeps,
`~/.local/state/bromigos/ext/ledger.json`. It lists each link and its sha256.

- The sentinel records a new, changed or removed file in the extensions' folders as
  verified only when it matches the ledger. During an update, it accepts either version
  for up to ten minutes.
- A hand edit of an installed plugin, or a file planted next to one, is recorded as
  unverified.
- VECTOR's terminal refuses `bromigos ext install`, `update`, `enable`, `disable`,
  `remove`, `migrate` and `new`. It also refuses `bromigos theme install` and `remove`,
  and `bromigos setup` or `install` with an answers file, which can approve code. The
  read verbs (`list`, `info`, `search`, `validate`) run.
- The extensions' folders and the ledger are read-only in his sandbox.

`tools/test-ext-guard.py` tests all of this in a throwaway home.

## Plugins that come with bromigOS

bromigos-homelab ships two VECTOR plugins in `/usr/lib/bromigos/homelab/vector/`:

- `homelab_tools.py`, his homelab read tools. They cover the lab's status, Prometheus,
  Kubernetes, Argo CD, Vault's health, Gnosis and a knowledge base.
- `homelab_gnosis.py`, the `gnosis` memory store.

Each tool is offered only where `homelab.toml` configures its source (bromigOS
`docs/homelab.md`).

Some homelab and personal tools still live in the core. [tools.md](tools.md#still-in-the-core)
lists them.
