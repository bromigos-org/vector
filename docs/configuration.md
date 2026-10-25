# Configuration

This page covers VECTOR's settings files. They set where his brain runs, his voice, his
eyes, his memory and which tools are on. Restart him after a change (`bromigos-holo restart`),
except where a section says it takes effect by itself.

## Where settings live

Each settings file is looked up by name. The first one that exists wins
(`holo/paths.py`):

1. `~/.config/bromigos/vector/<name>`, your own
2. `~/.config/bromigos/holo/<name>`, an older location that's still read
3. `/etc/bromigos/vector/<name>`, the machine's
4. `/usr/lib/bromigos/vector/<name>`, the package's defaults

`$XDG_CONFIG_HOME` replaces `~/.config` when it's set. `nolgia.json` is the exception. It
holds his credit budget, so it's read only from `/etc` or the package, never from a folder
he can write.

| File | What it sets |
|---|---|
| `vector.toml` | The brain's endpoint and models, the memory store, which tool sets are on, your real-money systems |
| `voice.json` | The voice engines, the voices, hearing, conversation mode and pacing |
| `eyes.json` | The vision model and extra windows his eyes must never capture |
| `nolgia.json` | The daily nolgia credit budget |
| `~/.config/bromigos/profile.toml` | Who you are and what he calls you (`bromigos profile`) |

A `voice.json` or `eyes.json` of yours replaces the package's file whole. Copy the
package's file and edit the copy:

```bash
mkdir -p ~/.config/bromigos/vector
cp /usr/lib/bromigos/vector/voice.json ~/.config/bromigos/vector/
```

## His brain

One OpenAI-compatible endpoint serves his brain, his eyes, summaries and embeddings
(`holo/vector/llm.py`). He uses the first of these that's set:

1. `base_url` in `vector.toml`'s `[llm]` table.
2. bromigOS's targets for the `llm` service, if you have a `targets.toml`. He takes the
   first target whose server answers. `bromigos target` shows and changes the order, and
   `bromigos target local` moves him to this machine on his next turn.
3. A LiteLLM gateway named in your private overlay. The private overlay is bromigOS's
   encrypted store of per-user values (`bromigos private`).
4. A local Ollama at `http://localhost:11434/v1`.

```toml
# ~/.config/bromigos/vector/vector.toml
[llm]
base_url = "http://localhost:11434/v1"
model = "qwen3:8b"
vision_model = "qwen2.5vl:7b"     # his eyes
embed_model = "nomic-embed-text"  # memory and skill matching
# api_key_file = "~/.local/share/bromigos/litellm-key"

[memory]
backend = "auto"                  # auto, local, or a plugin's store name

[tools]
homelab = "auto"                  # auto, or off to hide the homelab tools
```

The API key comes from `api_key_file`, which defaults to
`~/.local/share/bromigos/litellm-key`. Without that file he uses `$VECTOR_API_KEY`, and a
server without auth gets a placeholder. The key is never logged or shown by a tool.

Without `model` set, he uses `qwen3:8b` and `qwen2.5vl:7b` on Ollama. On a homelab LiteLLM
gateway he uses the gateway's own model names.

### Brains and lanes

`voice.json`'s `"brain"` picks the brain:

- `"pai"`, the default, is built on Pydantic AI (`holo/vector/brain_pai.py`). It needs the
  brain environment (`bromigos-holo setup brain`).
- `"classic"` is a simpler built-in brain (`holo/vector/brain.py`). He falls back to it
  when Pydantic AI isn't installed.

The Pydantic AI brain has two lanes. The voice lane answers chat and single lookups fast.
When a question needs more reasoning, it hands the turn to the deep lane. Set each lane's
models in order in `voice.json`:

```json
"lanes": {"voice": ["fast-model", "backup-model"], "deep": ["big-model", "backup-model"]}
```

Each lane is a fallback chain. A model has 9 s to start answering, then 25 s between
streamed events. If it misses either, the same turn moves to the next model and the
console shows an amber "rerouting to …" line. The failed model sits out for 20 s to 2
minutes, depending on how it failed. If every model fails, he says so. An empty `lanes`
uses `model` for both.

## His voice

`voice.json` picks the speech engines. He tries `engine` first, then each engine in
`fallback`, in order:

| Engine | Where it runs |
|---|---|
| `lab` | Your homelab's voice service, so your own GPU stays free |
| `qwen` | Qwen3-TTS on this machine's GPU, in his designed voice |
| `kokoro` | Kokoro on the CPU |

The package's default is `lab`, then `qwen`, then `kokoro`. The `lab` engine finds the
service through bromigos-homelab's `voice` capability, unless `lab.url` names one. If the
lab is down, or slower than `lab.first_audio_timeout_s` to start, the next engine speaks.
`bromigos target` shows and switches where speech (`voice.tts`) and hearing (`voice.stt`)
run.

His designed voice is `voices/vector-ref-A.wav`. It was made from a text description. No
recording of anyone was used ([voices/README.md](../voices/README.md)).

Set up the local engines once:

```bash
bromigos-holo setup voice       # Qwen3-TTS, Whisper and Kokoro, for a GPU
bromigos-holo setup voice-cpu   # Kokoro and a small Whisper, without a GPU
```

### Hearing

Speech-to-text is your homelab's Whisper when `stt.provider` is `"lab"`, else
faster-whisper on this machine (`stt.model`, `stt.device`).

- **Push to talk.** Hold Super+V. Nothing records unless the key is held, and a recording
  stops after `max_record_seconds`.
- **Conversation mode.** Super+Shift+E. He listens, answers when you pause, and listens
  again. Talk over him to interrupt. The `conversation` table sets the pause length
  (`end_of_turn_ms`) and when the mode turns itself off (`auto_off_s`).

He never hears himself. With `echo_cancel` on, playback and the microphone go through a
PipeWire echo-cancel pair that exists only for your session. Your default devices are
untouched. He also drops the start of a recording while his speakers drain
(`drain_ms`), and any transcript that repeats what he just said.

### Voices and moods

The `voices` table lists his voices. Each has a name, a reference recording (`ref`), a
colour and a note on what it's for. The package ships one voice, `main`, the caretaker.

- The brain can mark a sentence for another voice. He switches with a short dial sound,
  and his hologram takes that voice's colour.
- `bromigos-holo voice <voice>` pins a voice by its key in the table, such as `main`.
  `bromigos-holo voice auto` lets him choose again, and `bromigos-holo voice cycle` steps
  to the next one.
- A reply, or what's going on, sets his mood to calm, excited, concerned or alarmed. The
  mood tints his hologram and adds a little texture to his voice (`mood_shimmer`).

### Pacing and muting

A reply plays as one stream. The next sentence renders while the current one plays. Long
pauses are trimmed (`pacing`) and speech is a little faster than normal (`speed`) without
a change in pitch.

| To mute | Do this |
|---|---|
| His voice only | Middle-click his bar icon, or run `bromigos-holo mute` |
| The whole desktop, his voice included | Super+Shift+M, or run `bromigos mute` |

`bromigos-holo mute on`, `off`, `toggle` and `status` work whether or not he's running.

## His eyes

`eyes.json` sets:

- `model`, a multimodal model on his endpoint. Empty means `vision_model` from
  `vector.toml`.
- `max_side` and `text_max_side`, the largest image side sent for a look and for reading
  text. An image you attach is scaled to `max_side` too.
- `block.classes` and `block.titles`, windows to add to the built-in capture blocklist.

[safety.md](safety.md#his-eyes) explains what his eyes may and may not see.

## Memory

By default his memory is local. It's a SQLite file,
`~/.local/share/bromigos/vector-memory.sqlite`.

- Before every turn he recalls related notes by embedding, with a full-text fallback. The
  recall is capped at 0.2 s.
- "Remember that …" files a short note (`remember`).
- "Forget that" removes the last note, or a named one (`forget`).
- Quiet conversations are summarized into notes.

A plugin can bring another store. bromigos-homelab, for example, brings Gnosis, a memory
service on your homelab. `[memory] backend` picks the store:

- `auto` takes the first store a plugin registered, else the local one.
- `local` always uses the local store.
- A store's name uses that store.

Every recall, note and forget is logged in `~/.local/state/bromigos/vector-audit.log`,
without the full text.

## Tools

Tools whose backend isn't set up on this machine aren't offered. `[tools] homelab = "off"`
in `vector.toml` hides the homelab tools even when a homelab is configured. Ask him "how
are you set up?" to see what's on and why. [tools.md](tools.md) lists every tool.

## Real money

VECTOR never touches real money. The package refuses live orders and transfers at the
well-known brokerages, exchanges and prediction markets, and anything that names wallet
keys ([safety.md](safety.md#refused-before-running)). If you run a trading system of your
own, name it in `vector.toml`'s `[real_money]` table, and he treats it the same way:

```toml
# ~/.config/bromigos/vector/vector.toml
[real_money]
services = ["ledger-live"]                # workloads and services that move real money
hosts = ["@private:endpoints.ledger"]     # their web consoles
systems = ["ledger"]                      # systems that hold money or venue keys
titles = ['\bledger\b.*\blive\b']         # window titles his eyes never capture
refuse = [{match = '/api/orders\b', what = "the ledger's order route"}]
about = "the ledger's live routes, the ledger-live service"

[real_money.code]
"example-org/ledger" = ["engine/live/", "console/app/*/wallet/"]
```

| Key | What he does with it |
|---|---|
| `services` | His terminal refuses any command that names one. His Kubernetes tools refuse a workload whose name starts with one. |
| `hosts` | His browser never opens them, and his eyes never capture a window that names one. |
| `systems` | An Ansible playbook that names one is announced aloud and logged before it runs. |
| `titles` | Regular expressions. His eyes never capture a window whose title matches, and his browser refuses a matching host. |
| `refuse` | Regular expressions. His terminal refuses a command that matches, and says `what` it is. |
| `about` | How he names these systems when he says what he refuses. |
| `code` | Paths in a repository: a folder ends in `/`, and `*` stays inside one folder. His terminal refuses a push of commits that touch them. His merge tool leaves a pull request that touches them to you. |

A value that starts with `@private:` is read from your private overlay, so a host name
never sits in a public file. Every `vector.toml` that exists adds to these lists: yours,
the machine's in `/etc` and the package's. None can take a rule away. `vector.toml` is
protected: his terminal can't change it ([safety.md](safety.md#whats-protected)).
Restart him after a change.

## Your profile

What he calls you comes from your bromigOS profile, `~/.config/bromigos/profile.toml`.
Edit it with `bromigos profile`. It holds your name, the forms of address you like and
your pronouns. Say "call me …" and he changes it himself.

## Python environments

His Python environments live in your home folder, not in the package. `bromigos setup
voice` builds the voice environment when you choose a voice. Nothing builds the brain
environment for you. Without it he runs the classic brain, and his own browser doesn't
work. Build them with:

| Command | What it installs | Where |
|---|---|---|
| `bromigos-holo setup brain` | Pydantic AI, Playwright and the tools' libraries | `~/.local/share/bromigos/venv-brain` |
| `bromigos-holo setup voice` | Qwen3-TTS, Whisper and Kokoro, for a GPU | `~/.local/share/bromigos/venv-tts` |
| `bromigos-holo setup voice-cpu` | Kokoro and a small Whisper | `~/.local/share/bromigos/venv-tts` |

They use `uv` when it's installed. The voice needs `uv`, because `uv` fetches Python
3.12. The package lists come from `requirements/`.
