# Contributing to VECTOR

This page is for anyone changing VECTOR's code or docs. VECTOR is the desktop's AI
assistant, with a voice, and this repository is his package, `bromigos-vector`. The page
covers how to build and test a change, how to write its commits, and how his safety rules
shape what you can send.

VECTOR is in early access, and this repository is private. If you can read this, you have
access.

## Before you start

- **How the code is laid out** is in [docs/development.md](docs/development.md), with how
  to run a checkout, every test and the eval suite.
- **His limits** are in [docs/safety.md](docs/safety.md). Read it before you change
  anything in `holo/vector/`.
- **Writing docs?** Follow bromigOS's
  [docs/WRITING.md](https://github.com/bromigos-org/bromigOS/blob/main/docs/WRITING.md).
  It applies to every Markdown file here too.
- **Found a way around one of his limits?** That's a security problem. Report it
  privately, as [SECURITY.md](SECURITY.md) describes, not in an issue or a pull request.
- **Everyone here** follows the [code of conduct](CODE_OF_CONDUCT.md).

## What you need

Work on bromigOS, or on Arch Linux with the `bromigos-core` package. Install what the
package depends on, plus the tools to build and test it:

```bash
sudo pacman -S --needed base-devel git python python-gobject gtk3 gtk-layer-shell \
  python-cairo python-opengl python-numpy python-pillow python-psutil python-yaml \
  bubblewrap desktop-file-utils
```

His brain and his tools run in a Python environment of their own. Build it once:

```bash
bromigos-holo setup brain
```

## Build

There's nothing to compile. The launcher runs VECTOR from the tree it lives in. To try
your checkout, stop the running VECTOR and start yours:

```bash
bromigos-holo stop
bin/bromigos-holo vector
```

Run `bromigos-holo restart` to go back to the installed package.
[docs/development.md](docs/development.md#running-a-checkout) explains what a checkout
uses and what it doesn't.

To build the package without installing it:

```bash
cd packaging/bromigos-vector
makepkg --nodeps
```

`makepkg` rewrites the `pkgver=` line, so don't commit that change. Undo it with
`git checkout -- PKGBUILD`.

## Test

The tests are scripts in `tools/`. Each one prints what it checks and exits non-zero on a
failure. CI runs the offline ones on every pull request and every push to `main`
(`.github/workflows/tests.yml`). Run them before you push:

```bash
python3 -m compileall -q holo tools lib evals
python3 tools/test-devflow.py
python3 tools/test-privacy-guard.py
python3 tools/test-plugin-systems.py
python3 tools/test-launcher-verbs.py
python3 tools/test-launcher-entries.py
```

If you change a limit, also run its own test, such as `tools/test-shell.py` for the
terminal. Most run in the brain environment:

```bash
~/.local/share/bromigos/venv-brain/bin/python tools/test-shell.py
```

Read a test's docstring before you run it. Some of them act for real, on your desktop or
your GitHub account. [docs/development.md](docs/development.md#tests) lists every test
and says which ones are safe to run at any time.

## Send a change

- **Keep commits small,** one area at a time. Commit files by path, not with
  `git add -A`.
- **Start the subject with the kind of change,** then say what changed in plain words:
  `Added:`, `Fixed:`, `Changed:`, `docs:` or `packaging:`. For example,
  `Fixed: bromigos-holo stop waits for a running turn to finish`.
- **Docs follow the code.** If you change what something does, change its docs in the
  same commit.
- **Rebase on `main` before you push.** Other changes land there often.
- **Keep CI green.** If a check fails, fix it before anything else.

## How his safety rules affect your change

VECTOR's limits are code, not instructions to a model. A change that loosens one changes
what he can do on every machine that runs him, so those changes go through the operator.

- **Protected code is the operator's to merge.** His safety code (`HOLO_FILES` in
  `holo/vector/guard.py`), the merge tool itself (`holo/vector/devflow.py`), the
  workflows and `CODEOWNERS` are listed in `LIMITS` in `devflow.py`.
  `bromigos-holo work limits` prints the list. A pull request that touches any of them
  waits for the operator's review, and VECTOR's own merge tool never merges one.
- **A limit and its test change together.** If you change a refusal, change the test that
  checks it in the same commit, and update [docs/safety.md](docs/safety.md).
- **Don't work around a limit to make a feature fit.** If a feature needs a limit to move,
  say so in the pull request and explain why. Never weaken a refusal quietly.
- **Nothing private.** No hostnames, LAN addresses, tokens, Vault paths or personal
  names. Code reads private values from the private overlay (`holo/private.py`). Tests
  and examples use made-up names.
- **No paid work by default.** He makes things on free hardware and spends nothing unless
  the user asks. A new tool that can spend money follows the creation and spending rules
  in [docs/safety.md](docs/safety.md#making-things-and-spending).

VECTOR sends pull requests here too, as his GitHub account `bromigodev`. His merge tool
merges his own once CI is green, and leaves any that touch `LIMITS` for the operator.
[docs/safety.md](docs/safety.md#his-pull-requests) explains how.
