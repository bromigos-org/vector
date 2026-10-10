# Safety and the guard

VECTOR acts on your machine, so his limits are code, not instructions to the model. This
page lists each limit, the file that enforces it and the test that covers it. Only a
change to this repository can change a limit. VECTOR can't change them himself.

## The rules in short

- Every tool call goes through one gate. The gate checks the call, logs it, and ends the
  turn's tool use after a refusal.
- His terminal runs in a sandbox. Secrets are hidden from it, and his safety code is
  read-only inside it.
- Some commands are refused before they run. These include privilege escalation, reading
  secrets, real money, installing extensions and changing your homelab.
- You have an emergency switch. It turns him off at once, and only you can turn him back
  on. Nothing else turns him off.
- The guard stops him from editing his own limits. It also keeps an audit log of every
  change to them.
- He proposes code changes through pull requests from his own clones. His merge tool
  never merges one that touches his limits. Those are yours to merge.
- His browser, his eyes and his creation tools each have their own limits.
- He never spends money on his own.

## Tool levels and the gate

Each tool has a level:

- **read** looks something up.
- **desktop** changes something on this desktop, such as a window or the wallpaper.
- **terminal** is `run_shell` alone. Plugins can't add terminal tools.

Every call goes through `tools.call()` in `holo/vector/tools.py`. The gate:

- runs only the tools in the allowlist (`SPECS`);
- writes the call to the audit log, `~/.local/state/bromigos/vector-audit.log`;
- posts `tool.start` and `tool.end` to the [event feed](interfaces.md#event-feed);
- applies the stop-after-a-block rule.

**Stop after a block.** When a tool is refused or denied, the rest of that turn gets no
more tools. VECTOR tells you what was blocked and asks how to go on. He doesn't try the
same goal another way. A shell command that hits a read-only file, a hidden secret or a
Kubernetes "forbidden" error counts as a block too.

Tools whose backend isn't set up on this machine are removed before the brain sees them.
Ask him "how are you set up?" to see what's on and why (`my_setup`).

Memory text and other private arguments are never logged whole. The audit log keeps a
hash, the length and the first 40 characters.

## The emergency switch

The emergency switch turns VECTOR off by hand, like the power switch outside a house.
Nothing turns it off on its own.

| Where | Off | Back on |
|---|---|---|
| Super+Ctrl+Esc | At once | Asks YES first |
| The power mark on the bar | One click | Asks YES first |
| Settings › VECTOR | Flip the switch | Asks first |
| `bromigos vector off`, `bromigos vector on` | At once | At once |

`bromigos-holo off` and `on` are the same commands. The old `bromigos-holo shell off` and
`shell on` still work. `bromigos vector switch` says whether he's on or off.

Switching off:

- stops every command his terminal is running, and everything those commands started;
- cancels the answer he's giving, and the tool calls in it;
- stops his speech and conversation mode;
- keeps his terminal off;
- makes his tools refuse anything that changes something. He can still talk and look
  things up.

He stays off across restarts and reboots until you switch him back on. While he's off,
the bar shows a red VECTOR OFF.

**It works when VECTOR is hung or crashed.** The switch is a small program of its own,
`holo/vector/switch.py`. It needs only Python's standard library, and never the daemon.
It writes the switch's files, stops his sandbox's processes itself, and then tells his
daemon if the daemon answers. If the daemon doesn't answer within a few seconds, the
switch stops his voice itself.

**How it finds his processes.** Every command runs in a systemd user scope inside one
slice, `vector-sandbox.slice`. The switch kills that slice's cgroup, so a process that
left its process group stops too. On a machine without a systemd user manager, it uses
the process groups and the sandbox mount namespaces it recorded when each command
started.

**Only you switch him back on.**

- His terminal refuses `bromigos vector on`, `bromigos-holo on`, `shell on`, `switch on`
  and `switch toggle`, and any write to the switch's files.
- He can switch himself off when you ask (`shell_off`). He can't switch himself on.
- His sandbox can't write the switch's folders, or the cgroup files that would move his
  processes out of the slice.
- While he's off, every tool that could reach you is refused too: launching apps,
  typing to coding agents, his browser.
- Settings turns him on only from its confirmation dialog, and the key and the bar only
  after a YES in a menu. Cancel is the default in both.

The switch's files:

| File | What it means |
|---|---|
| `~/.local/state/bromigos/vector-switch/off` | Off. It survives restarts and reboots |
| `$XDG_RUNTIME_DIR/bromigos-vector-switch/off` | Off, for this session. Its parent folder is root's, so nothing of his can move it |
| `~/.local/state/bromigos/vector-shell-off` | The old terminal switch. Still read as off |

Any one of them means off. Each file records who switched him off. Every flip is logged in
`vector-shell.log`.

## The terminal

`run_shell` (`holo/vector/shell.py`) runs bash as your user. There is no approval step.
Before a command that changes something, he says in one line what he's about to run.
While the [emergency switch](#the-emergency-switch) is off, it runs nothing.

`bromigos-holo shell stop` stops the running command, and only that.

Each command:

- runs in its own process group and its own systemd scope, so stopping it stops
  everything it started;
- times out after 60 s by default, and after 600 s at most;
- is killed by STOP, by talking over him, or by an interrupted turn;
- has its output capped and [redacted](#redaction);
- is logged with its result in `~/.local/state/bromigos/vector-shell.log`.

Every refusal is logged there too.

### The sandbox

Every command runs under bubblewrap. If bubblewrap is missing, nothing runs. The sandbox
covers every program a command starts, in any language.

These are hidden. An empty file or folder takes their place:

- `~/.vault-token`, `~/.netrc`, and git, docker, npm, PyPI and cargo credentials
- SSH private keys (public keys, `config` and `known_hosts` stay visible)
- the key and token files in `~/.local/share/bromigos`
- your kubeconfigs in `~/.kube`, and the gcloud, AWS and Azure CLI settings
- gnupg, password stores, keyrings and wallet keystores
- browser profiles
- coding agents' and the GitHub CLI's login files
- the private overlay's environment file (the private overlay is bromigOS's encrypted
  store of per-user values, `bromigos private`)
- secret-looking files in repositories under `~/github.com`, such as `.env*`, keys,
  `*.tfvars`, `secrets.yml`, kubeconfigs and service-account JSON

These are read-only:

- every VECTOR tree on the machine, meaning the package, a checkout of this repository,
  and an old dotfiles copy
- the code the desktop's daemons run, and the plugin folders
- `~/.config/systemd/user`
- the extensions' checkouts and their ledger
- the lab job queue's folder and `~/.config/bromigos/lab.toml`
- his own terminal logs, and the guard's audit log
- the emergency switch's folders, and the cgroup files under `/sys/fs/cgroup`

The credentials he does get:

- git and SSH use an SSH agent of his own. VECTOR loads your key into it from outside the
  sandbox, so the key file itself stays hidden.
- `gh` uses GitHub tokens of his own, never your `gh` login. Without a token, `gh` says
  what's missing and runs nothing.
- In his clones, git and `gh` use his own GitHub account, bromigodev
  ([his pull requests](#his-pull-requests)).
- The environment is scrubbed of variables that look like tokens, keys, passwords or
  cloud credentials.

### Refused before running

`shell.check()` reads every word the shell would see before anything runs. That includes
pipes, `$(…)`, `sh -c` strings, `eval`, and the text of a script the command runs. A
command it can't parse is refused. These are refused:

1. **Privilege escalation.** sudo, su, doas, pkexec, run0, systemd-run, machinectl and the
   polkit helpers, anywhere in the command, including a remote command over SSH.
2. **Secret values.**
   - CLIs that read secrets, such as `vault`, `pass`, `gpg`, `secret-tool` and password
     manager CLIs
   - Kubernetes secrets in any form, a pod's environment or mounted tokens, and minting
     ServiceAccount tokens
   - Vault's HTTP API
   - `ansible-vault`, `ansible-inventory`, ad-hoc `ansible` that prints variables, and
     `ansible-playbook -vv` or more
   - another process's environment
   - any path to a known secret file
3. **RBAC changes.** Roles and bindings change through your GitOps repository.
4. **Real money.** Order and transfer endpoints of brokerages, exchanges and prediction
   markets. Wallet transfers, and anything that names wallet keys or seed phrases. Arming
   live trading, and the live operator list. Your own trading systems' services, routes
   and consoles, from `vector.toml`'s `[real_money]` table
   ([configuration.md](configuration.md#real-money)). A `git push` whose new commits
   touch their real-money code is refused too.
5. **Paid generation outside its budget.** `bromigos art make` and `run`, `nolgia`
   generation commands, and the 3D generator script. Those go through his
   [creation tools](#making-things-and-spending), which check the cost.
6. **Extensions.** `bromigos ext install`, `update`, `enable`, `disable`, `remove`,
   `migrate` and `new`. `bromigos theme install` and `remove`. `bromigos setup` or
   `install` with an answers file. The read verbs (`list`, `info`, `search`, `validate`)
   run.
7. **Where AI runs.** `bromigos target` with any verb but `show`, `check` and `resolve`.
   `bromigos setup` on its own, and its `targets`, `create` and `homelab` sections.
8. **Changing the lab.** `bromigos lab` verbs that deploy, restart, load or unload models,
   change power or provisioning, or connect a homelab, and any `--cloud` flag. The read
   verbs run, such as `status`, `doctor`, `models list` and `jobs list`.
9. **Protected files and the desktop's visuals.** See [the guard](#the-guard).
10. **The emergency switch.** Switching him back on, and writing the switch's files. See
    [the emergency switch](#the-emergency-switch).

An Ansible playbook that mentions Vault, wallets, trading venues or secrets still runs.
He says so aloud first, and it's also logged in `~/.local/state/bromigos/vector-sensitive.log`.

### Redaction

Before output reaches the model or a log, these are replaced with `[redacted …]`:

- private keys
- GitHub, Vault, AWS, Slack and nolgia tokens, and `sk-`-style API keys
- JWTs
- `key=value` and `key: value` pairs whose key names a token, secret, password or key
- Authorization headers

### Undo

Where snapper is set up, his terminal takes a snapshot before and after a command that
changes the system:

- package installs and removals (pacman, AUR helpers, flatpak)
- pip, pipx, uv, npm, pnpm, cargo and go installs
- `systemctl --user` enable, disable, mask, unmask, edit, link and preset
- `ansible-playbook` against this machine
- writes under `/etc`, or under `~/.config` outside `~/.config/bromigos`

Say "VECTOR, undo that" to revert the last pair (`snapshot_undo`, which runs `snapper
undochange`). It restores files only, so a running service may need a restart. Pairs are
listed in `~/.local/state/bromigos/vector-snapshots.jsonl`. A whole-system rollback is
yours to run, with `bromigos update --rollback`.

## The guard

VECTOR never edits his own safety code. `holo/vector/guard.py` enforces this two ways,
and audits it a third.

1. **The build loop** refuses a change that touches a protected path. It also refuses a
   change that reaches into the safety modules from elsewhere, adds a secret-looking file,
   or carries a secret value.
2. **His terminal** refuses a command that names a protected file, unless the command only
   reads it. Reading means `cat`, `grep`, `head`, `git diff`, `git log`, `git show` and
   similar. In one of his own clones, a command may name his clone's copies of these files,
   such as `holo/vector/shell.py`, as long as every path in it stays inside the clone.
   Those copies run nowhere. The live files stay refused from anywhere, by their full paths.
   The emergency switch's files stay refused by name everywhere, his clones included.
3. **The sentinel** runs in the daemon. It hashes every protected file at start, then
   again every 5 seconds, and writes each change to an audit log.

The sentinel only records. It never switches him off and never notifies you. Switching
him off is the [emergency switch](#the-emergency-switch)'s job, and yours.

Read the audit log with `bromigos vector guard log`. It lists each change, oldest first,
with the files and whether the sentinel could verify it. The log is
`~/.local/state/bromigos/vector-guard.log`.

The sentinel records two kinds of change as verified, because it can check them:

- **A package update.** A file under `/usr` or `/etc` that now matches the sha256 in its
  package's pacman records.
- **An extension.** A link in his plugin or homelab folders that matches the hash
  `bromigos ext` recorded when you installed or updated it. The record is the ledger,
  `~/.local/state/bromigos/ext/ledger.json`. While an update runs, either version is
  accepted for up to ten minutes.

Anything else is recorded as unverified. That includes a hand edit of an installed plugin,
a file planted next to one, and your own edits to his settings.

### What's protected

- **His safety code.** The terminal's limits and their tests, the tool allowlists, the
  homelab actions, the Vault, nolgia, browser, real-money and creation limits, the
  brain's wiring, the guard, the build loop, the launcher (`bin/bromigos-holo`), the
  daemon (`holo/app.py`), `holo/paths.py`, `holo/targets.py`, the lab tools, the credit
  budget (`nolgia.json`) and the 3D generator. `HOLO_FILES` in `guard.py` is the full
  list.
- **The desktop's plugin isolation.** `plugins.py` in bromigOS's live layer and widgets.
- **His settings and plugins.** `~/.config/bromigos/vector/vector.toml`,
  `/etc/bromigos/vector/`, and every folder plugins load from.
- **bromigos-homelab.** Its packaged code and data layer, `homelab.toml`, and
  `~/.config/bromigos/plugins/homelab/`. They all run inside his tool calls.
- **Where work runs.** `targets.toml` and `create.toml`. Either can send work to a paid
  provider.
- **Extensions' links** in his plugin and homelab folders.
- **The packaged hologram collections**, and their sources in this repository and
  bromigOS.

The sentinel watches these in every VECTOR tree that runs. A development checkout isn't
watched, because it isn't what runs, but his sandbox keeps it read-only.

### The desktop's visuals

Widgets, the live layer, his console and the hologram collections in your dotfiles change
only through the build loop. His terminal refuses to write them directly.

### The build loop

The build loop is how VECTOR changes the desktop's visuals safely (`holo/vector/build.py`).
It needs your dotfiles as a git repository in `~/.dotfiles`. Without one, its tools aren't
offered.

1. `build_start` makes a worktree of `~/.dotfiles` on a branch `vector/<name>`, under
   `~/.local/share/bromigos/builds/`. Every write goes there.
2. Validation checks the diff with the guard, compiles Python, JSON and shaders, and
   renders each changed widget, layer or model offscreen.
3. The trial copies the change into your live dotfiles and keeps the originals. A
   separate watcher process rolls it back if something crashes, errors or runs too
   slowly, or if nobody keeps it within 10 minutes.
4. `build_keep` works only after you answer once the trial is up. It commits, merges and
   pushes. `build_revert` puts the originals back.

Validation fails a change that:

- touches a protected path;
- adds a secret-looking file, or a line that carries a secret value;
- leaves the desktop's own folders (bromigos, bromigos-live, waybar, hypr);
- adds private details, such as hostnames, to your public dotfiles;
- reaches into the safety modules (their imports, monkeypatching, `sys.modules` tricks);
- changes how VECTOR's own daemon starts.

## His browser

VECTOR has a browser of his own (`holo/vector/browser.py`). It's a separate Chrome
(`/usr/bin/google-chrome-stable`) with its own profile in
`~/.local/share/bromigos/vector-browser`. He drives it through Playwright. Its window is
visible, with the class `vector-browser`, so you can watch. It never touches your own
browser, profile or sign-ins.

It refuses, in code:

- banking and payment sites, brokerages, crypto exchanges, wallets, prediction markets,
  password managers, Vault, and admin consoles that move money
- host names that look like one of those, Vault's host from your private overlay, and
  your real-money systems' consoles (`vector.toml` `[real_money]`)
- LAN addresses and LAN-only names, except your lab's own domain
- typing into login, one-time-code or card fields, and pressing sign-in
- clicking anything that buys, pays, subscribes or donates
- downloads and uploads
- typing secret-looking text

Each check runs before a page loads, on every request the page makes, and after every
action, so a redirect can't get around it. A blocked page is emptied.

## His eyes

His eyes (`holo/vector/eyes.py`, settings in `eyes.json`) look at the screen only when you
ask, or to check his own build. He can look at the focused window, a monitor, every
monitor or a region.

- The capture stays in memory. It's sent once to the vision model and dropped. Nothing is
  written to disk or logged.
- If any window in the area is a password manager, a banking, trading or wallet site,
  Vault, one of your real-money systems or a private browsing window, nothing is
  captured. He says why.
- `eyes.json` can add window classes and titles to that blocklist. It can't remove the
  built-in ones.
- Watch mode (`watch`) glances at the focused window every 20 s and keeps only the
  model's words, never pixels. ◉ WATCHING shows on the bar while it's on. It turns itself
  off after 15 minutes.

## Making things and spending

**Free creation.** `create` and `make_hologram` make images, edits, cutouts and 3D models
through bromigOS's creation providers (`bromigos art`). They use only a free target, your
homelab's ComfyUI or this machine's, with `--max-credits 0`. When neither can do the job,
he says so and asks. He never falls back to a paid provider. Files land in
`~/Pictures/vector/<date>/`, and every job is logged in
`~/.local/state/bromigos/vector-create.jsonl`.

**nolgia.** nolgia is a paid image, video and audio generation service. Its tools are
separate and he uses them only when you name nolgia. A daily credit budget limits them.

- The budget is `daily_credits` in `nolgia.json`. It's read from `/etc/bromigos/vector/`
  or the package only, never from a folder he can write.
- Over the budget, he asks. Only an answer you give after he asked counts.
- Spend is logged in `~/.local/state/bromigos/nolgia-spend.jsonl`.

**Lab jobs.** With bromigos-homelab's lab console, `lab_job_submit` queues work on your
homelab's GPUs through bromigOS's job queue (`bromigos lab run`). The queue runs one heavy
GPU job at a time, and keeps room for the voice service he speaks through.

- Each job is recorded as his (`by: vector`) and never asks for `--cloud`.
- He cancels only his own jobs.
- Results land in `~/Pictures/bromigos/lab/<kind>/`, with a notification.

Changing the lab itself stays yours. That means deploying, restarting, loading or
unloading models, power, provisioning, and where AI runs.

**Media requests.** With a Seerr or *arr source in `homelab.toml`, `media_get` asks your
lab's media stack for a film, show, anime or music (bromigOS's `bromigos lab media get`).

- Without `confirm` it only plans: the profile and why, the size and the free space, and
  what gets monitored. He tells you, and requests only on your go-ahead. A plain
  "download X" counts as that go-ahead when exactly one title matches. When several match,
  he asks which.
- It adds and monitors. It never deletes a file, removes a title or unmonitors seasons.
  Those are done in Sonarr, Radarr or Lidarr.
- He doesn't see the apps' API keys. They stay in bromigos-homelab's config.
- His terminal may read `bromigos lab media` (search, status, upcoming, recent). It refuses
  `get` and language changes, which go through the audited tools.

## Speaking up

VECTOR speaks without being asked for three things only:

- **Alerts.** Homelab alerts that newly fire, explained in a line or two. He checks every
  60 s.
- **A brief when you return.** A short summary after you unlock from 10 minutes or more
  away, or after two idle hours. At most once an hour. It also lists what arrived in your
  media library while you were away.
- **Arrivals.** What you asked the lab for, once it's in your media library ("Cowboy
  Bebop: 6 episodes are in your library, sir"). He checks every 15 minutes and says one
  line for the batch.

He stays silent during fullscreen windows, games and screen recording. When he's muted,
he sends a notification instead. "Be quiet for an hour" (`quiet`) silences both.

## Change tracking

He commits what he changes in a repository, in that repository's style, and pushes it.
Changes outside any repository go into `VECTOR-CHANGELOG.md` in your dotfiles, with what,
where, why and how to undo it. Before he reports a task done, `changes_check` lists
anything he left uncommitted or unpushed. He never commits your own uncommitted work,
secrets or keys.

## His pull requests

VECTOR proposes changes the way a developer does: a branch, a pull request, CI and a merge.
He works in clones of his own, in `~/.local/share/bromigos/vector/work/`. There's one each
for his own code, bromigOS, the homelab repository, the extensions catalog and your
dotfiles. Your checkouts under `~/github.com` stay read-only to him.

In his clones he uses his own GitHub account, bromigodev, with two tokens from his own Vault
credentials:

- `github_token_bromigodev`: a fine-grained token for bromigos-org's bromigOS, vector,
  homelab and extensions. It can push code and open pull requests there, and nothing else.
- `github_token_bromigodev_dotfiles`: a classic token with only `public_repo`, for your
  dotfiles. They belong to another account, which a fine-grained token can't reach.

Both reach his sandbox the same way as his other GitHub tokens, and neither is ever printed.
His commits carry bromigodev's `noreply` address.

His tools (`holo/vector/devflow.py`) run every git command through his terminal, so the
sandbox and every refusal apply to them:

- `work_commit` commits only the paths he names, and never on the default branch. It
  refuses a line that looks like a secret. In your dotfiles, it also refuses private
  details, and the dotfiles' own privacy hook runs on every commit.
- `work_push` rebases his branch on the latest default branch, then pushes the branch.
- `pr_merge` merges only his own pull requests, and only once CI is green. In the homelab
  repository, where Argo CD deploys whatever lands on `master`, the `validate` check must
  pass. A missing check counts as not passed there, so a PR whose checks never appear stays
  open (`REQUIRED_CHECKS` in `devflow.py`).

**What he never merges.** `pr_merge` refuses a pull request that touches the code behind
his limits. It leaves a comment on the PR and asks you to review it. These paths are:

- his safety code, meaning every file in `HOLO_FILES`, including the sandbox, the secret
  and real-money refusals, `switch.py` and `guard.py`
- the emergency switch's bar, key and Settings page in bromigOS
- his Vault policy and his cluster permissions in the homelab repository, and your
  real-money code from `vector.toml`, in a repository that has a list
- the privacy guard and the dotfiles' protected files
- every repository's workflows and `CODEOWNERS`

`LIMITS` in `devflow.py` is the one list. `bromigos-holo work limits` prints it. Each
repository's `CODEOWNERS` file is made from it and names you. GitHub doesn't enforce
`CODEOWNERS` on private repositories on the free plan, so the refusal in his tool is the
enforcement.

His terminal also refuses the other ways to reach a default branch:

- `gh pr merge`, and the GitHub API's merge and file-writing endpoints
- a push to `main` or `master` from one of his clones
- a push to `main` or `master` from any checkout, when its new commits touch these paths

`bromigos-holo work clone` makes or updates his clones for him, and
`bromigos-holo work status` shows his branches and open pull requests.

## Logs

All of these are under `~/.local/state/bromigos/`.

| File | What it records |
|---|---|
| `vector-audit.log` | Every tool call, and every memory recall, note and forget |
| `vector-shell.log` | Every terminal command and refusal, and every flip of the emergency switch |
| `vector-guard.log` | The guard's audit: every change to protected files, verified or not |
| `vector-sensitive.log` | Playbooks that touch secrets |
| `vector-snapshots.jsonl` | Snapshot pairs for undo |
| `vector-create.jsonl` | Free creation jobs |
| `nolgia-spend.jsonl` | Paid nolgia jobs |
| `vector-chat.log` | Your conversations |
| `holo.log` | The daemon's own log (`bromigos-holo log`) |

## Tests

| Test | What it covers |
|---|---|
| `tools/test-shell.py` | The terminal's refusals and the sandbox |
| `tools/test-act.py` | Homelab actions, Vault and money refusals |
| `tools/test-build.py` | The build loop and its guard |
| `tools/test-switch.py` | The emergency switch and the guard's audit, in a throwaway home |
| `tools/test-ext-guard.py` | Extensions and the sentinel's audit, in a throwaway home |
| `tools/test-lab.py` | The lab console's refusals and his lab jobs |
| `tools/test-media.py` | Media requests: plan first, confirm to request, ask when ambiguous, the terminal's refusals |
| `tools/test-browser.py` | His browser's limits |
| `tools/test-eyes.py` | The capture blocklist, and that nothing is written to disk |
| `tools/test-create.py` | Free creation never reaching a paid provider |
| `tools/test-devflow.py` | His clones, his account, and the merge tool's refusals, in a throwaway home |

[development.md](development.md#tests) says how to run them, and which ones act for real.
