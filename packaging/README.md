# Packaging

This folder builds one package, `bromigos-vector` (`bromigos-vector/PKGBUILD`). CI signs it
and publishes it to bromigOS's pacman repository, `[bromigos]`
(`.github/workflows/packages.yml`).

## What the package installs

| Path | What it is |
|---|---|
| `/usr/lib/bromigos/vector/` | This repository's code (`holo/`, `lib/`, `tools/`, `evals/`), `skills/`, `voices/`, `requirements/`, and the default `voice.json`, `eyes.json` and `nolgia.json` |
| `/usr/bin/bromigos-holo` | The launcher, a link to `bin/bromigos-holo` |
| `/usr/share/bromigos/vector/holo/` | His hologram collection, from `assets/holo/` |
| `/usr/share/applications/`, `/usr/share/icons/hicolor/` | The VECTOR and Hologram Gallery launcher entries and icons |
| `/usr/share/doc/bromigos-vector/` | The README and the docs |

His Python environments aren't in the package. Each user builds them with
`bromigos-holo setup` ([docs/configuration.md](../docs/configuration.md#python-environments)).

The package's `check()` runs `tools/test-launcher-entries.py` and
`tools/test-privacy-guard.py`. It also checks that every JSON file parses and every model
in the hologram manifest has its baked file.

## Versions

The version is `<VERSION>.r<commits>.g<last commit>`, for example `0.1.2.r59.gc5f67cb`.
`VERSION` holds the first part. Every commit makes the version newer, so every publish
supersedes the last.

The count includes the 58 commits made before this repository's history restarted at
v0.1.2 (they're in `bromigos-org/vector-archive`). The PKGBUILD's `_archived` holds that
number, so every version sorts above the ones published before the restart. Leave it as
it is.

## Publishing

CI publishes on a push to `main` that changes the package's files, on a version tag, or
when run by hand. Pull requests never publish.

| Trigger | Channel |
|---|---|
| A push to `main` | edge |
| A `vX.Y.Z` tag | stable |
| A `vX.Y.Z-rcN` tag | rc |
| A manual run | The channel you pick |

A change to the README or `docs/` alone doesn't publish. The workflow's `paths` list says
which files count.

## Shared with bromigOS

`ci/publish.sh`, `ci/prepare-container.sh` and `keys/bromigos.asc` are copies of the files
in bromigOS's `packaging/`. Both repositories publish to the same bucket with the same
signing key. They share a lease in the bucket, so they never rewrite the repository's
database at the same time.

bromigOS's copies have extra steps that apply only to that repository, such as the
bootstrap script, the lab model catalog and a build cache. When you change shared logic,
such as signing, channels or the lease, change both copies.

bromigOS's `packaging/README.md` covers the repository, its channels, the secrets CI
needs (`PKGS_*`) and the runner variable (`CI_RUNNER_PACKAGES`). Without that variable,
the job runs on GitHub's runner and publishes to the public R2 bucket alone, which the
homelab then copies ("When the public bucket is the primary" there). To try that without
touching the real channels:

```bash
gh workflow run packages -R bromigos-org/vector -f hosted=true -f prefix=ci-test
```

## Build locally

Build the package without installing it:

```bash
cd packaging/bromigos-vector
makepkg --nodeps
```
