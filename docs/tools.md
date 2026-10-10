# What VECTOR can do

VECTOR acts through tools. Each tool has a level, which is read, desktop or terminal
([safety.md](safety.md#tool-levels-and-the-gate)). The core's tools are defined in
`holo/vector/tools.py` (`SPECS`). Plugins can add more ([plugins.md](plugins.md)).

A tool whose backend isn't set up on this machine isn't offered at all. Ask him "how are
you set up?" (`my_setup`) to see what's on and why.

## This machine and you

| Tool | What it does |
|---|---|
| `system_stats` | Reads CPU, memory, GPU, disks, network and fans |
| `time_now`, `calendar_month` | Tells the date and time, shows a month |
| `conversation_history` | Finds past conversations, such as "what did we talk about yesterday?" |
| `docs_search`, `docs_read` | Searches and reads Markdown docs in your repositories and dotfiles |
| `notes_read`, `notes_append` | Reads and adds to FIELD NOTES, your notepad (`~/.local/share/bromigos/notes.md`) |
| `remember`, `forget` | Files or removes a long-term memory note |
| `set_profile` | Changes what he calls you, when you ask |
| `my_setup` | Explains his own setup: models, tools on and off, plugins loaded |

## Programs and windows

| Tool | What it does |
|---|---|
| `app_search` | Finds installed programs by name or purpose |
| `launch_app` | Starts an installed program by fuzzy name |
| `launch` | Opens a known app, a switchboard entry or a URL in your browser |
| `run_detached` | Starts a GUI program or long job that outlives the turn |
| `open_path` | Opens a file, folder or URL with its default app |
| `windows` | Lists open windows |
| `window` | Focuses, moves or closes a window (he says so before closing) |
| `media` | Shows what's playing and controls it |

## The desktop

| Tool | What it does |
|---|---|
| `panel` | Toggles a widget panel |
| `wallpaper` | Switches the wallpaper variant |
| `scan` | Runs the desktop's scanner sweep |
| `show_hologram`, `open_gallery` | Shows a model on his table, or opens the gallery |
| `hologram_deck` | Opens and drives the live layer's hologram decks |
| `set_voice` | Pins one of his voices, or lets him choose |
| `quiet` | Silences alerts and briefs for a while |
| `briefing_now` | Gathers the facts for a brief now (needs a homelab) |

## Den TV channels

The den is the wallpaper's radio room. Its screens play channels you choose.
These tools share bromigOS's channel library with Den TV, `bromigos media` and the CLI.
Each call is audited. Changes preserve comments and dotfiles links and appear without a
session restart.

| Tool | What it does |
|---|---|
| `tv_channels` | Lists channels, contents, order, bumpers, screens and now/next |
| `tv_channel_add_item` | Adds a show, movie, YouTube URL or music pick to a picked channel |
| `tv_channel_remove_item` | Removes a pick. Its media files stay |
| `tv_channel_create` | Creates a picked, library, YouTube or folder channel |
| `tv_tune` | Tunes a named channel on the big monitor, scope or small monitor |
| `tv_channel_delete` | Removes a whole channel only after your latest request names it |

Ask "Add this show to Night Block", "What's on the den TV?", or "Tune the scope to
Night Block". Library channels already contain everything in their library. Music is
played from Jellyfin or Plex; an album can be named as `music:album:Album title`.

The phone gets `tv_channels` and the two individual pick tools. Creating channels,
tuning screens and deleting whole channels stay on the desktop. A whole-channel delete
needs both `confirm=true` and your own latest words asking for deletion and naming it.

## The web

| Tool | What it does |
|---|---|
| `web_search` | Searches the web through the SearXNG instance named in your private overlay |
| `web_fetch` | Fetches a page's readable text (http and https only, no LAN hosts but your lab's domain) |
| `browser_open`, `browser_tabs`, `browser_read`, `browser_find`, `browser_click`, `browser_type`, `browser_scroll`, `browser_back`, `browser_look`, `browser_close` | Drives his own browser ([limits](safety.md#his-browser)) |

## His eyes

| Tool | What it does |
|---|---|
| `look` | Looks at the screen, a window or a region and answers a question |
| `read_screen_text` | Transcribes on-screen text exactly |
| `active_window` | Says what "this" means: the focused window |
| `watch` | Follows along every 20 s until you stop it, for 15 minutes at most |

His eyes look only when you ask ([limits](safety.md#his-eyes)).

## Making things

| Tool | What it does |
|---|---|
| `create` | Makes an image, an edit, a cutout or a 3D model on a free target |
| `make_hologram` | Makes a concept, a cutout and a 3D mesh, ready to bake into your collection |
| `lab_console` | Shows your homelab console's status cards (needs bromigos-homelab) |
| `lab_jobs` | Lists the lab job queue, or shows one job |
| `lab_job_submit` | Queues work on your homelab's GPUs |
| `lab_job_cancel` | Cancels one of his own lab jobs |
| `nolgia_catalog`, `nolgia_credits`, `nolgia_read` | Reads nolgia's models, prices and his spend |
| `nolgia_generate`, `nolgia_review` | Makes something with nolgia, a paid service, only when you name it |

[safety.md](safety.md#making-things-and-spending) explains how he stays free by default.

## His terminal and undo

| Tool | What it does |
|---|---|
| `run_shell` | Runs a bash command in his sandbox ([limits](safety.md#the-terminal)) |
| `shell_off` | Switches him off with the emergency switch, when you ask. He can't switch himself back on |
| `snapshot_create`, `snapshot_list`, `snapshot_undo` | Takes, lists and undoes snapper snapshots |

## Changing the desktop

| Tool | What it does |
|---|---|
| `build_start` | Starts a build of a new or changed widget, layer, deck or hologram |
| `build_status` | Says where the build is |
| `build_keep`, `build_revert` | Keeps or reverts the trial, on your word |
| `build_stop` | Stops the build and rolls back any trial |

[safety.md](safety.md#the-build-loop) describes the build loop.

## Repositories and coding agents

| Tool | What it does |
|---|---|
| `github` | Reads repos, commits, CI runs, pull requests and issues |
| `ci_watch` | Waits for a commit's CI and reports the result |
| `changes_check` | Lists what he left uncommitted or unpushed |
| `github_repo_create` | Creates a new repository, private by default |
| `herdr_status`, `herdr_read`, `herdr_send`, `herdr_start`, `herdr_wait` | Starts and talks to coding agents running in herdr, a terminal multiplexer for them. What Claude Code says comes from its transcript ([Claude Code](#claude-code)), not herdr's screen |
| `work_clone`, `work_status` | Makes or updates his own clones of his code, bromigOS, the homelab repository, the extensions catalog and your dotfiles |
| `work_branch`, `work_commit`, `work_push` | Starts a branch, commits the files he names, and pushes the branch after rebasing it |
| `pr_open`, `pr_watch`, `pr_merge`, `pr_close` | Opens a pull request, waits for its CI, merges his own when CI is green, or closes it |

A coding agent in herdr that becomes blocked or finishes becomes a notice from him.

He works in his clones as his own GitHub account, bromigodev. He never merges a pull
request that touches his limits. [safety.md](safety.md#his-pull-requests) has the details.

## Claude Code

VECTOR reads Claude Code's own records on this machine: its sessions, their transcripts,
the background jobs' own conversations, its memory files and its logs. It's the way he
knows what Claude said or is doing. He never reads it off the screen or herdr's pane.

| Tool | What it does |
|---|---|
| `claude_sessions` | Lists the sessions, newest first: project, title, start and last activity, message count, whether it's live, its herdr pane, what it's doing, whether it's waiting on you, and its background jobs with their status |
| `claude_read` | Reads a conversation as plain text, page by page. By default, the latest messages, and for a live session what it's doing now and its running jobs and tasks. With `include_tools`, also each tool call and the first lines of its result |
| `claude_search` | Finds words or a "phrase" across the transcripts and jobs, with snippets, newest first |
| `claude_memory` | Reads Claude Code's memory: a project's MEMORY.md index, one file, or a search across them |
| `claude_logs` | Reads the prompts you typed (`history`), its debug log (`debug`) and background task output (`tasks`) |
| `claude_say_latest` | Reads Claude's latest reply aloud in his voice, or the one about something you name |
| `claude_follow` | Follow mode: reads each new reply aloud as its turn ends, until you stop it |

What you say, and what he does:

- "What is Claude working on?" He lists the live sessions and what each is doing.
- "Is Claude waiting on me?" He names the sessions that finished their turn and wait for
  you, or wait for a permission.
- "Read me what Claude said." He reads the latest reply in the live session aloud.
- "What did Claude say about the den TV?" He finds that reply and reads it.
- "What did Claude change in the widgets last night?" He searches, then reads that part.
- "Find where we decided X." He searches the transcripts and tells you where and when.
- "Read Claude's replies to me as they come." Follow mode is on: each finished reply is
  read aloud once its turn has ended. Tool chatter and half-written answers are never read.
  "Stop reading Claude" turns it off.

**Which session.** With one live session, he uses it. With several, he takes the one you
name ("the bromigOS one", "the left pane", "the research session"), else the focused herdr
pane, else the clearly most recent. When it's a toss-up he asks: "There are two: bromigOS
widgets, and the research notes. Which one?" Follow mode follows one session, or all of them,
saying which session each reply came from.

**Read aloud.** Replies are rewritten for speech. Headings and bold become plain sentences,
lists are read item by item, a short table is read row by row ("Big monitor: Shows.") and a
long one is summarised. Code becomes "a command to run is shown on screen", and a short
one-liner is spelled out. Links and URLs become their site, file paths "a file path", and
emoji and symbols are dropped. Redaction runs first.

**Follow mode.** `bromigos vector claude follow on|off|toggle|status [session|all]` and
SUPER+ALT+C work too. ◉ READING CLAUDE shows on his console and on the bar while it's on.
Muted, he shows the replies without speaking them. He never talks into an open
microphone, and talking over him stops him. `bromigos vector claude say [n]` reads the
latest reply, or the last n.

**From your phone.** His brain on Hermes gets `claude_sessions`, `claude_search`, and
`claude_read` for the latest messages only. The memory files, the logs, reading aloud and
follow mode stay on the desktop.

Cloud sessions (claude.ai/code) aren't stored on this machine, so he doesn't see them.
[safety.md](safety.md#claude-codes-records) has the limits.

## Your homelab

bromigos-homelab, bromigOS's homelab package, brings read tools once `homelab.toml`
configures their sources. They are `lab_status`, `prometheus_query`, `k8s_get`,
`k8s_logs`, `k8s_events`, `argocd_apps`, `lab_vault_status`, `gnosis_search` and
`knowledge_search`.

The core also has homelab actions. They need an account you set up for him, and his
access is limited by that account's permissions:

- `k8s_restart`, `k8s_scale`, `k8s_delete_pod`, `k8s_run_job`
- `argocd_sync`, `argocd_refresh`, `argocd_wait`
- `kb_write`, a note into your knowledge base
- `vault_list`, `vault_put`, `vault_copy`, which store secrets without ever returning a
  value

`[tools] homelab = "off"` in `vector.toml` hides all of these.

## Your media

With bromigos-homelab and a Seerr, Sonarr, Radarr or Lidarr source in `homelab.toml`, he
asks your lab's media stack for things (bromigOS's `bromigos lab media`; its docs/lab.md,
"Media requests").

| Tool | What it does |
|---|---|
| `media_search` | Finds a film, show, anime or music: year, release dates, the profile it would get, the size, whether it's in Jellyfin or Plex already |
| `media_get` | Plans a request, then makes it on your go-ahead: TV and films through Seerr, music through Lidarr (then Soulseek), a YouTube link through Pinchflat |
| `media_status` | Says what's downloading (progress, time left), what's stuck and why, the audio and subtitle languages, and one show's seasons and monitoring |
| `media_upcoming` | Lists what you track and when it comes out |
| `media_recent` | Lists what arrived in the library lately |
| `media_language` | Shows or sets your audio and subtitle languages |
| `media_delete` | Says exactly what a delete removes, and deletes it only when you tell him to delete that title by name |

What you say, and what he does:

- "Download Cowboy Bebop." He finds one match, so he requests it. He tells you the profile
  ("Remux-1080p - Anime (English audio)"), the audio ("dual audio: English + Japanese"),
  and what's monitored ("Monitoring season 1 and specials, and new episodes").
- "Get The Office." Two shows match (US and UK), so he asks which.
- "Get season 2 of X", "just the latest season", "keep up with new episodes of X", "stop
  getting new episodes of X". These set the seasons and whether new episodes are picked up.
  A show you already have keeps what it has.
- "Track the new Dune film." He requests it now. Radarr waits for the home release, never a
  cam copy. "What's coming up?" reads the dates.
- "I prefer the Japanese audio with subtitles" for anime sets anime's audio to Japanese and
  the subtitles to English.
- When something you asked for is in the library, he says so in a line. The return brief
  lists it too.
- "Delete Paper Moon Cafe." He tells you what goes and how much space it frees, and deletes
  it, because your words named it. He never deletes on his own, to free space, or from your
  phone. `bromigos media` in a terminal is the other way to delete.

[safety.md](safety.md#making-things-and-spending) has the limits.

## Skills

| Tool | What it does |
|---|---|
| `load_skill` | Loads a skill, a page of know-how for a kind of task ([skills](../skills/README.md)) |

## Still in the core

The homelab actions, herdr, GitHub and nolgia tools, a few tools for one user's own
services, and the build loop all live in the core for now. Each is hidden unless its
backend is set up. Moving them into plugins needs a trusted plugin level first, because
plugins may only add read and desktop tools.
