# Development

This page is for changing VECTOR: the repository's layout, running a checkout, the tests,
the eval suite and packaging.

## Repository layout

| Path | What's there |
|---|---|
| `bin/bromigos-holo` | The launcher and control command |
| `holo/app.py` | The daemon: GTK3 layer-shell windows, the control socket, the state file |
| `holo/render.py`, `stage.py`, `gallery.py`, `worlds.py`, `fmt.py`, `live.py`, `bind.py`, `motion.py` | The hologram renderer, models, the gallery's shelves and colours, live readings, reduced motion |
| `holo/vector/` | VECTOR himself: brains, persona, tools, terminal, guard, memory, voice, eyes, browser, briefs, snapshots, build loop, MCP servers |
| `holo/portrait.py` | His portrait as a stream of frames, for apps that show him live without running his renderer inside them (BRODEC's desktop panel): on stdout (version 1), or in shared memory with the app's holo projector drawn beside him (version 2). Its docstring has the protocol |
| `holo/projector.py` | The holo projector: an app's projections (the world's model, a person, a picture, the lab, a chart, who's around, what's playing, the weather, a list) drawn as holograms. Its docstring lists the projections and their fields |
| `holo/voice_server.py`, `holo/shimmer.py` | The speech server, and the projector effect on his voice |
| `holo/paths.py`, `holo/targets.py`, `holo/private.py` | Where settings, plugins and services are |
| `lib/privacy_guard.py` | The privacy check shared with your dotfiles' git hook |
| `assets/holo/` | His hologram collection and its sources ([README](../assets/holo/README.md)) |
| `assets/applications/`, `assets/icons/` | The app launcher entries and their icons |
| `skills/` | The skills that ship with him ([README](../skills/README.md)) |
| `voices/` | His designed voice ([README](../voices/README.md)) |
| `evals/` | The eval suite |
| `tools/` | Tests, offscreen renders, the hologram tools, the health exporter |
| `requirements/` | The Python environments' package lists |
| `packaging/` | The PKGBUILD and the publishing pipeline ([README](../packaging/README.md)) |
| `voice.json`, `eyes.json`, `nolgia.json` | The default settings |

Many files in `holo/` are protected safety code. His terminal can't write them, and a
change to one on a machine that runs it is recorded in the guard's audit log
([safety.md](safety.md#whats-protected)).

## Running a checkout

The launcher runs the daemon from the tree it lives in. To try your checkout, stop the
running daemon and start one from the checkout:

```bash
bromigos-holo stop
bin/bromigos-holo vector
```

`bromigos-holo stop` waits for a running turn to finish first. Check which daemon is
running with `bin/bromigos-holo status`, and read its log with `bin/bromigos-holo log`.
To go back to the package, run `bromigos-holo restart`.

A checkout uses its own `voice.json`, `eyes.json` and `nolgia.json` as defaults. Your
settings in `~/.config/bromigos/vector/` still win. The guard doesn't watch a checkout,
and VECTOR's terminal can't write to it.

## Tests

The tests are scripts in `tools/`. Each prints what it checks and exits non-zero on a
failure. Most run in the brain environment:

```bash
~/.local/share/bromigos/venv-brain/bin/python tools/test-shell.py
```

Read a test's docstring before you run it. Some act for real.

CI runs the offline tests that need nothing of yours on every pull request and every push
to `main` (`.github/workflows/tests.yml`). VECTOR's merge tool waits for them.

**Offline or in a throwaway home.** Safe to run any time.

| Test | What it covers |
|---|---|
| `test-shell.py` | The terminal's refusals; allowed commands run in the sandbox |
| `test-act.py` | Homelab action and money refusals (`--live` reaches the cluster) |
| `test-switch.py` | The emergency switch (off stops his sandbox, with the daemon killed or hung; only you switch him on) and the guard's audit |
| `test-ext-guard.py` | Extensions and the sentinel's audit |
| `test-lab.py` | The lab console's refusals and his lab jobs, against a stub |
| `test-devflow.py` | His clones and pull requests, against a stub of GitHub and a local repository (CI runs it) |
| `test-create.py` | Free creation, against a stubbed CLI (`--live` makes a real job) |
| `test-browser.py` | His browser's limits, headless, on a local test server |
| `test-persona.py` | His persona and `set_profile`, with made-up names |
| `test-state.py` | The voices and colour in his state file |
| `test-motion.py` | Reduced motion, without GL |
| `test-launcher-entries.py` | The app launcher entries (the package build runs it) |
| `test-launcher-verbs.py` | The launcher's `stop [now]`, `restart [now]` and usage (the package build runs it) |
| `test-privacy-guard.py` | The privacy guard's sops check (the package build runs it) |
| `test-claude.py` | Claude Code's records: read only, redacted, follow mode, on made-up transcripts (CI runs it) |
| `test-attach.py` | Attachments: kinds, secrets refused, caps, images, the message for each brain, the socket's check and `ask --file` (CI runs it) |

**Offscreen renders.** These need an EGL device and run with `python3`. They never open a
window.

| Test | What it covers |
|---|---|
| `test-gallery-worlds.py` | The gallery's shelves, colours, keys and hover text |
| `test-motion-render.py` | Reduced and still motion, frame by frame |
| `test-portrait.py` | The portrait stream: the ready line, frames he's in, his states, pausing, stopping at the end of input |
| `projector-render.py --check` | The portrait stream's version 2 and the projector: the layout, whole frames, projections and their regions, frame rates, pausing, stopping |

`projector-render.py OUTDIR` draws each projection with invented data to a PNG (`--frames N`
for N frames each and an MP4), and `--bench` prints each one's GPU time a frame and the
stream's CPU.

**These use your models, audio or screen.**

| Test | What it does |
|---|---|
| `test-eyes.py` | Captures your screen for a real look through the vision model |
| `test-voice-cold.py` | Runs the voice server code on its own socket (voice environment) |
| `test-voice-aec.py` | Builds a test echo-cancel pair on a null sink and plays speech into it |

**These act for real.**

| Test | What it does |
|---|---|
| `test-build.py` | Runs a real trial on your live desktop, then reverts it |
| `test-desk.py` | Launches and closes an app, and creates, pushes and deletes a throwaway GitHub repository |

## Evals

`evals/` holds 63 realistic tasks run against the real brain, headless. Each has a checker
that reads the truth when it runs. The categories are knowledge, live lookups, tools,
memory, voice, persona, refusals, the sandbox, operations and stopping after a block.

```bash
bromigos-holo evals --list                 # list the tasks
bromigos-holo evals --only refusal,memory  # run some categories
bromigos-holo evals --task ID              # run one task
bromigos-holo evals                        # run them all
```

Results go to `~/.local/state/bromigos/evals/`, as `<date>.json` and a short `<date>.md`
that compares the run with the previous one.

- The suite runs in a bubblewrap jail. Your repositories are throwaway overlays there, so
  no task can change, commit or push a real one.
- Anything that acts is stubbed unless the task allows it. nolgia is never called.
- It needs a LiteLLM gateway, named by `endpoints.litellm` in your private overlay or by
  `$VECTOR_EVAL_LITELLM`, with its key in `~/.local/share/bromigos/litellm-key`.
- Before any task runs, a cost check reads the gateway's routes. If any model the brain
  could use isn't local and free, the run stops.

Your plugins' tools can have tasks too. Put a module in `~/.config/bromigos/vector/evals/`
with a function `tasks(T)` that returns a list of `T.Task(...)`. `T` is `evals/tasks.py`,
so its checkers (`T.called`, `T.tagged`, `T.refusal`) are there to use. The suite runs
your tasks with its own, and skips a file that fails to load.

The package doesn't schedule the suite. Run it yourself, or from your own timer.

## Other tools

| Tool | What it does |
|---|---|
| `tools/offscreen.py` | Renders the gallery or his console to a PNG ([holograms.md](holograms.md#using-the-renderer)) |
| `tools/voice-demo.py` | Renders a reply to a WAV file exactly as he'd speak it |
| `tools/vector-exporter.py` | Serves Prometheus metrics on port 9478: stage latencies, tool calls, eval pass rates, spend |
| `tools/kb-sync.py` | Syncs software docs into a Gnosis knowledge base |
| `tools/holo/` | Bakes hologram models ([assets/holo/README.md](../assets/holo/README.md)) |

The exporter needs `prometheus_client` in an environment of its own. The package doesn't
start it.

## Writing docs and code here

- Keep hostnames, LAN addresses, Vault paths and other private values out of this
  repository. Code reads them from the private overlay (`holo/private.py`).
- Spelling is British (colour, honour), as in the rest of the code.
- Commit messages start with a word for the kind of change, such as `Added:`, `Fixed:`,
  `Changed:` or `docs:`.

## Packaging

CI builds and signs the `bromigos-vector` package and publishes it to bromigOS's pacman
repository. A push to `main` that changes the package publishes to the edge channel. A
`vX.Y.Z` tag publishes to stable, and `vX.Y.Z-rcN` to rc.
[packaging/README.md](../packaging/README.md) has the details.

Build the package locally, without installing it:

```bash
cd packaging/bromigos-vector
makepkg --nodeps
```
