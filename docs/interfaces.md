# Interfaces

This page is for code that talks to VECTOR: bromigOS's bar, live layer and keyboard
module, scripts, and other AI agents. It covers the launcher's commands, the control
socket, the state file, the event feed and the MCP servers.

## The launcher

`bromigos-holo` (`bin/bromigos-holo`) starts the daemon on first use and sends it
commands. `bromigos vector <command>` is the same thing.

| Command | What it does |
|---|---|
| `bromigos-holo vector` | Shows or minimizes VECTOR |
| `bromigos-holo vector-show` | Shows him, never minimizes (the app launcher's entry) |
| `bromigos-holo ask "…"` | Asks him something without typing in his window |
| `bromigos-holo conversation` | Turns conversation mode on or off |
| `bromigos-holo chat` | Pins his chat box shown, or folds it away |
| `bromigos-holo history` | Opens or closes past conversations |
| `bromigos-holo ptt on`, `ptt off` | Push to talk, bound to Super+V press and release |
| `bromigos-holo gallery [model]` | Opens or closes the hologram gallery |
| `bromigos-holo gallery-show [model]` | Opens the gallery, never closes it |
| `bromigos-holo mute [on\|off\|toggle\|status]` | His voice only. On its own, it toggles |
| `bromigos-holo voice [auto\|<voice>\|cycle]` | Pins a voice by its key in `voice.json`'s `voices` (`main`), or lets him choose |
| `bromigos-holo off`, `on` | The emergency switch ([safety.md](safety.md#the-emergency-switch)). Neither starts the daemon |
| `bromigos-holo switch [status\|toggle]` | Says on or off. `toggle` switches him off at once, or asks YES before switching him on (Super+Ctrl+Esc, the bar) |
| `bromigos-holo shell [on\|off\|stop]` | `on` and `off` are the old names of the switch. `stop` stops the running command only |
| `bromigos-holo guard log [N]` | The guard's audit log, the last N changes (30 by default), in local time |
| `bromigos-holo status` | Prints the daemon's state as JSON, or "not running" |
| `bromigos-holo stop [now]` | Stops the daemon after the current turn, waiting 90 s at most. With `now`, stops it at once |
| `bromigos-holo restart [now]` | Stops the daemon the same way, then starts it |
| `bromigos-holo log` | Shows the end of the daemon's log, `~/.local/state/bromigos/holo.log` |
| `bromigos-holo evals [--only CAT] [--task ID] [--list]` | Runs the eval suite ([development.md](development.md#evals)) |
| `bromigos-holo setup [brain\|voice\|voice-cpu]` | Builds a Python environment ([configuration.md](configuration.md#python-environments)) |

The old `pilot` commands still work. VECTOR was called PILOT before.

## The control socket

The daemon listens on `$XDG_RUNTIME_DIR/bromigos-holo.sock`, a Unix socket with mode 600.
A client connects, sends one command as text, such as `status` or `ask what time is it`,
and reads one reply. The daemon answers within 5 seconds. Besides the launcher's
commands, it accepts `release` (hand the keyboard back) and `say …` (speak a short notice;
the build watcher uses it).

The emergency switch sends `switch off <who>` and `switch on <who>` after it has written
or removed its files. The daemon answers these at once, outside its main loop. `switch off`
stops his command, his turn and his speech. `switch on` only refreshes what he shows: it
can't turn him on while a marker is there.

## The state file

`$XDG_RUNTIME_DIR/bromigos-vector.json` says what VECTOR is doing now. The daemon rewrites
it atomically whenever it changes.

| Field | Values |
|---|---|
| `state` | `idle`, `listening`, `thinking`, `speaking` or `error` |
| `mood` | `calm`, `excited`, `concerned` or `alarmed` |
| `voice` | The voice in use |
| `voice_mode` | `auto`, or the pinned voice |
| `voices` | Every voice in `voice.json`'s order, each `{"role", "name", "colour"}` |
| `voice_colour` | The colour of the voice in use |
| `muted`, `conversation`, `shown` | True or false |
| `unread` | Replies you haven't seen |
| `model` | The brain's model |
| `watching` | Watch mode's details while it's on, else null |
| `suppressed` | A notice held back because of what's on screen, else null |
| `switch` | `on`, or `off` while the emergency switch is off |

Colours are the ones his construct shows, in the theme's hues.

Readers in bromigOS:

- the bar's VECTOR icon (`desktop/waybar/scripts/vector.py`)
- the live layer's worlds
- the keyboard module, whose dial steps through `voices` and whose lighting follows
  `voice_colour` and the mood

`tools/test-state.py` tests the voices and colour fields.

## Event feed

VECTOR tells the desktop what he's doing through
`$XDG_RUNTIME_DIR/bromigos-vector-events.jsonl` (`holo/vector/events.py`). Each line is
one JSON object:

```json
{"v": 1, "t": "2026-10-09T14:02:31.120-07:00", "ts": 1791579751.12, "type": "tool.end", "id": "4242-7", "name": "web_search", "ok": true, "ms": 812, "outcome": "5 items"}
```

The file rotates to `.1` at about 5 MB. It never holds memory text, file contents or
secret values. Readers must ignore types and fields they don't know.

| Type | Fields |
|---|---|
| `memory.recall` | `space`, `ids`, `n`, `ms`, `source` |
| `memory.file` | `space`, `category` |
| `memory.forget` | `space`, `n` |
| `tool.start` | `id`, `name`, `args` (a short summary) |
| `tool.end` | `id`, `name`, `ok`, `ms`, `outcome` |
| `task.progress` | `task`, `title`, `step`, `note`, `state`, `pct` |
| `git.push` | `repo`, `branch`, `sha`, `ok` |
| `ci.result` | `repo`, `sha`, `workflow`, `status`, `conclusion`, `url` |
| `argo.sync` | `app`, `action`, `sync`, `health`, `revision` |
| `k8s.action` | `action`, `namespace`, `kind`, `name`, `replicas`, `ok` |
| `vault.op` | `op`, `path`, `key` (never a value) |
| `browser.action` | What his browser did |
| `voice.stt`, `voice.first_audio` | Speech timings |
| `skill.load` | The skill he loaded |

bromigOS's live layer draws the feed in its decks (`live/live/vfeed.py` there).

## MCP servers

`holo/vector/mcp_server.py` publishes his read tools as an MCP server, for other agents on
this machine. Every call still goes through his tool gate, with the same checks and audit
log.

```bash
cd /usr/lib/bromigos/vector
~/.local/share/bromigos/venv-brain/bin/python -m holo.vector.mcp_server             # stdio
~/.local/share/bromigos/venv-brain/bin/python -m holo.vector.mcp_server --http 8770 # HTTP on 127.0.0.1
```

`holo/vector/mcp_lan.py` serves the same tools, plus his desktop actions, over HTTP on
your LAN. It's for a setup where another assistant on your homelab uses this desktop's
tools.

- It needs a bearer token in `~/.local/share/bromigos/vector-mcp-token`, and refuses to
  start without one.
- It never binds to all interfaces or to loopback.
- It accepts only the client networks it's told about.
- It logs every request without the token or the body.

The module's docstring lists its settings. The package doesn't start either server.
