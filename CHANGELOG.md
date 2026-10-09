# Changelog

Every change to VECTOR (bromigos-vector), release by release: the technical log, made from the
commit subjects by git-cliff when bromigOS prepares a release (bromigOS docs/release.md).
VECTOR is released with bromigOS: the same version, tagged together.

This repository's history starts at 0.1.2. The commits before it, which the links in the
0.1.2 section and below name, are in the archive, `bromigos-org/vector-archive`.

<!-- releases: tools/release/release.py adds each release below this line, newest first -->

## [0.1.3] - 2026-10-09
<!-- release: 0.1.3-rc1 -->

Release candidate v0.1.3-rc1.

Since v0.1.2. Released with bromigOS 0.1.3 (its notes: bromigOS's manual, What's new).

No changes.

## [0.1.2] - 2026-10-09
<!-- release: 0.1.2-rc2 -->

Release candidate v0.1.2-rc2.

Since v0.1.1. Released with bromigOS 0.1.2 (its notes: bromigOS's manual, What's new).

### Added

- The holograms follow bromigOS's motion setting (accessibility.toml: full, reduced, still) ([ee0c832](https://github.com/bromigos-org/vector-archive/commit/ee0c8321fc8bab2efc52b95abfc638783b340dbf))
- The lab console for VECTOR: lab_console, lab_jobs, lab_job_submit, lab_job_cancel; his terminal reads bromigos lab and refuses what changes the lab ([c5f67cb](https://github.com/bromigos-org/vector-archive/commit/c5f67cb496585edcc2635558b9a997ad33b667d3))
- VECTOR and the hologram gallery in the app launcher: desktop entries with their icons, and gallery-show (opens, never a toggle) ([d6f66cb](https://github.com/bromigos-org/vector-archive/commit/d6f66cbf7cc7b82e64af5ff74a67dfd3b758123c))
- The worlds' holograms in the gallery, shelf by shelf, each in its world's colours ([39145cc](https://github.com/bromigos-org/vector-archive/commit/39145cc46327fe990faf9c1a829a3937934bd9aa))
- The lab's settings are read-only to him: ~/.config/bromigos/lab.toml (the model planner, the upgrade policy, the catalog bench) ([76a1a50](https://github.com/bromigos-org/vector-archive/commit/76a1a50e1afec7901162f871c2ae8f3b5d5ec27e))
- The operator's emergency switch (bromigos vector off|on, SUPER+CTRL+ESCAPE, the bar, Settings); Changed: the guard's sentinel only audits, it never switches VECTOR off ([988f945](https://github.com/bromigos-org/vector-archive/commit/988f945b58402330addabd247667af69179d8c06))
- VECTOR's own clones and pull requests (work_clone, work_status, work_branch, work_commit, work_push, pr_open, pr_watch, pr_merge, pr_close; bromigos-holo work) as his GitHub account bromigodev; pr_merge never merges a PR that touches the code behind his limits (devflow.LIMITS, CODEOWNERS) ([4f6d14e](https://github.com/bromigos-org/vector-archive/commit/4f6d14e09ad7cd2acfbe967dd844b9047e10a918))

### Changed

- His homelab making follows the lab's new models: cutouts are Lucida, 3D is Pixal3D (shape only, stood upright), and lab_job_submit's upscale takes an image or a short clip (SeedVR2 7B) ([f235825](https://github.com/bromigos-org/vector-archive/commit/f235825cf70c63b3115c16985d0cdc7901da4e12))
- VECTOR names no one's trading system: a user's own system comes in through plugin hooks, and their real money through vector.toml [real_money] ([6f3e202](https://github.com/bromigos-org/vector-archive/commit/6f3e202737c3ea6c2f1926f029fdd52a11599dc0))

### Fixed

- The state file is written before VECTOR's scene exists (voices and colour empty until then), not a traceback at startup ([09218bf](https://github.com/bromigos-org/vector-archive/commit/09218bfd037aa0777bf0bb319e8f659f54423423))
- A voice change publishes VECTOR's state at once, so a keyboard dial steps through every voice (it read a stale mode and only ever reached the first and last) ([ee3fbdf](https://github.com/bromigos-org/vector-archive/commit/ee3fbdf12bc88a805059547773431b321a0f0d8d))
- The privacy guard reads a list's lists in a sops file (- - ENC[…]) as encrypted, not as plaintext values ([cb51f48](https://github.com/bromigos-org/vector-archive/commit/cb51f48b9377513e3d3a56e8843edcefc2bd368f))
- The guard's notice says his terminal stays off until you run bromigos-holo shell on, not until he's restarted (a restart leaves it off) ([897ab9e](https://github.com/bromigos-org/vector-archive/commit/897ab9e74b49bf73bbd4a31032a95324f7a31ec0))
- Bromigos-holo stop now stops at once (the launcher dropped "now"); stop and restart [now] return once the daemon has gone, so restart during a turn starts a new one; a started daemon no longer holds the caller's output open; the usage names the real verbs (history, voice by its key) and evals has no timer ([9c05dd4](https://github.com/bromigos-org/vector-archive/commit/9c05dd42db6dd4716be2b976d4f4e6e4027d288d))
- Pr_merge merges by rebase, so his commits land on main authored by bromigodev's noreply address (a squash merge was authored with the account's own email) ([4375763](https://github.com/bromigos-org/vector-archive/commit/437576378bf2b9365c050f03cc94ff193f310e80))
- The privacy guard's sops test uses made-up node names ([1a26563](https://github.com/bromigos-org/vector-archive/commit/1a2656334df76c65bdbf67cd5558eaabf1f47b91))
- Test-plugin-systems.py's child keeps this user's pip packages (CI installs them with --user) ([8cff3a7](https://github.com/bromigos-org/vector-archive/commit/8cff3a7983246e1392cc2a546081ddfc4eb85e38))

### Docs

- docs: reduced motion in the holograms: what each setting changes, how still frames are drawn, the tests ([49a21e9](https://github.com/bromigos-org/vector-archive/commit/49a21e91846f2e9c7df22ca0c8f7a1806e153f69))
- docs: VECTOR for a newcomer: a task-first README with a console render, and docs/ pages for safety, configuration, tools, holograms, interfaces and development ([9f8b5d7](https://github.com/bromigos-org/vector-archive/commit/9f8b5d77ebf29f3cfcb602ee385194c4d95b528f))
- docs: the emergency switch (where it is, what off does, why it works with VECTOR hung, why only you switch him on, its files) and the guard as an audit log ([f04a3b8](https://github.com/bromigos-org/vector-archive/commit/f04a3b83b55b12f5ec80f57b3e489bd43e1f3277))
- docs: CHANGELOG.md, the technical log release by release (v0.1.0, v0.1.1 from the tags) ([806bab0](https://github.com/bromigos-org/vector-archive/commit/806bab071023af5e21cfb73be5a2c811b3490e78))
- docs: SECURITY.md, issue templates and the brand assets' licence ([db5ce4a](https://github.com/bromigos-org/vector-archive/commit/db5ce4a890180edbb0fd8daa34109747234c76ed))
- docs: his pull requests (safety.md: his clones, his account, what his merge tool never merges and why it's the enforcement; tools.md; development.md: test-devflow.py and the PR checks; README) ([3a51b95](https://github.com/bromigos-org/vector-archive/commit/3a51b9594e8944a595f9df1c408a9b4c193a0b31))
- docs: README: the guard audits his safety code, it doesn't switch him off (#3) ([fadb522](https://github.com/bromigos-org/vector-archive/commit/fadb522a870efa7f11c2ce50d40932ae99c52667))
- docs: propose-a-change: when his terminal refuses a test script for what its text names, he says so and lets CI run it ([67f98a8](https://github.com/bromigos-org/vector-archive/commit/67f98a8a0e3a9716ef8af5d0a7bcc7e502529681))
- docs: the hologram previews rendered again over the empty den with made-up readings (offscreen.py OFF_DEMO=1) ([ef1a550](https://github.com/bromigos-org/vector-archive/commit/ef1a5502091165d36d462f4f9d9259016332e94a))

### Packaging and CI

- packaging: ship every docs page in /usr/share/doc/bromigos-vector, not only plugins.md ([b80b2dd](https://github.com/bromigos-org/vector-archive/commit/b80b2dd438e9795699d5b8680feee2ed856e4cef))
- packaging: a tag's publish copies bromigos-vector from another channel, signed, instead of building it; release tags only from main ([ac0863a](https://github.com/bromigos-org/vector-archive/commit/ac0863ace01293fdb00c87050091b925e2621758))

## [0.1.1] - 2026-10-08
<!-- release: 0.1.1 -->

Since v0.1.0.

### Added

- His state file names his voices and the colour of the one in use ([afc7592](https://github.com/bromigos-org/vector-archive/commit/afc75921634700644bfe838974b6987bf9187456))

### Changed

- VECTOR's guard knows extensions: it accepts only what bromigos ext recorded, and he can't install them ([cfa24e2](https://github.com/bromigos-org/vector-archive/commit/cfa24e2f87388272ead7b08780fc0f5b3be76808))

### Docs

- docs: VECTOR as a product: the README, the guard as it is, the skills index ([8682f23](https://github.com/bromigos-org/vector-archive/commit/8682f23423fd498ef3e8ed0172f8c7d319ddf5b9))
- docs: the guard watches extensions' plugin and homelab links, not skills ([437b771](https://github.com/bromigos-org/vector-archive/commit/437b77160b03ea9152fee43076adbe92455bde61))

### Packaging and CI

- ci: one packages concurrency group per channel, so a tag's stable publish isn't cancelled by pushes to main ([bbf4da4](https://github.com/bromigos-org/vector-archive/commit/bbf4da48e1710b8d30a61b85752a863b6eed694e))

## [0.1.0] - 2026-10-08
<!-- release: 0.1.0 -->

The first release.

### Added

- Bromigos-vector, VECTOR as a bromigOS package ([95dd61b](https://github.com/bromigos-org/vector-archive/commit/95dd61b1c0d980c3f183d34644a9f6d3204cddc6))
- VECTOR's hologram models ship with him; the tools take a collection (--root) ([b4ce347](https://github.com/bromigos-org/vector-archive/commit/b4ce3478555a3cd605cc41d442cab05ed0578f08))
- VECTOR speaks and listens through the lab first (engine lab), his own GPU only as the fallback ([4b7a3ad](https://github.com/bromigos-org/vector-archive/commit/4b7a3adb7ddf23f8f51bca51f138e951cdbbd953))
- VECTOR makes images, cutouts, 3D meshes and holograms on the homelab, never a paid provider on his own ([a1b448e](https://github.com/bromigos-org/vector-archive/commit/a1b448e87fa2a5b4dd0392e0842aff177cedbe8a))
- VECTOR's voice, hearing, brain and creation follow bromigOS's targets.toml (homelab, local, cloud) ([bb26d64](https://github.com/bromigos-org/vector-archive/commit/bb26d644238d5e07ef3580001b1d5b8cafccb34e))
- VECTOR's input box grows with what you type (up to 8 lines, then scrolls) ([1834fde](https://github.com/bromigos-org/vector-archive/commit/1834fde9bbe1b1bc4eb65c5cb851a21b16172cc3))
- VECTOR's mute takes on|off|toggle|status (for bromigos mute, the desktop's one mute) ([c627475](https://github.com/bromigos-org/vector-archive/commit/c6274751be15868529c751022728125493bd9cec))

### Changed

- VECTOR from the operator's dotfiles (linux/.config/bromigos/holo at c136274) ([4ef0b1e](https://github.com/bromigos-org/vector-archive/commit/4ef0b1e365463732e8eba7a485ddca8cff86ce36))
- The operator's voice-cast tool (make-refs.py) from the package; it lives in his dotfiles ([b602414](https://github.com/bromigos-org/vector-archive/commit/b6024149d7053d3db8c204435704b044436b7369))
- The homelab read tools out of VECTOR's core into bromigos-homelab's VECTOR plugin ([c11de54](https://github.com/bromigos-org/vector-archive/commit/c11de548a4f254a67ca527f2077c5e07ea7e9938))
- VECTOR keeps his connections to the lab voice alive (a small pool), so a line doesn't pay a TLS handshake ([959b468](https://github.com/bromigos-org/vector-archive/commit/959b468409f499fd7ee58319dc4dcc2b66dd90e3))
- VECTOR's mute tooltip: the pip's middle-click mutes all sound (theme + VECTOR) ([9b7776f](https://github.com/bromigos-org/vector-archive/commit/9b7776f6ebfeb56fe71c4b3cfe4e4d74532362ff))
- VECTOR's mute tooltip: the bar pip mutes him alone; the keys mute all sound ([87d21d7](https://github.com/bromigos-org/vector-archive/commit/87d21d74e6a5b8c534e9e193e446c13e1da7b722))

### Fixed

- The guard names paths.py and llm.py by path only (common names elsewhere) ([63300f1](https://github.com/bromigos-org/vector-archive/commit/63300f15c2bf87de639a595f2716f525eac9d6a6))
- Bromigos-holo's help lists evals and setup ([40cd472](https://github.com/bromigos-org/vector-archive/commit/40cd4725595045aa91107e9f24184ad5b8f87eba))
- The hyprland-config skill describes bromigos-desktop's layout (defaults in /usr/share, the user's thin hyprland.lua) and the auto-reload rule ([9662820](https://github.com/bromigos-org/vector-archive/commit/966282017f0b49b4b1d719cd07ed69074a199d0a))
- The build loop changes Hyprland's config without tripping its emergency mode ([92342ee](https://github.com/bromigos-org/vector-archive/commit/92342ee321bd321e0e60466c1bd39116a51e6702))
- A reading whose name has a digit (wick.solar2) is known to make-holo and the gallery's source map ([7515bc0](https://github.com/bromigos-org/vector-archive/commit/7515bc0ed71b589accf919c6667baebb4f63b548))
- With the lab voice up, the local cast voice no longer loads onto his GPU ([164e9bf](https://github.com/bromigos-org/vector-archive/commit/164e9bf48a10ae590aa61a153634c0fe31a8cecf))
- The guard's tripwire watches what VECTOR runs, and accepts verified package updates ([521a3b0](https://github.com/bromigos-org/vector-archive/commit/521a3b0e1e0885ff2bda5fe10f8c4159a3b7dfeb))

### VECTOR

- holo: the hologram's output is bromigOS's primary, not a hard-coded DP-1 ([f381d67](https://github.com/bromigos-org/vector-archive/commit/f381d6724641a0513efd2240b944cca0e83c5429))

### Tests and tools

- tools/holo: gen3d.py runs on bromigOS's creation providers (bromigos art make model3d.generate) ([49fb85d](https://github.com/bromigos-org/vector-archive/commit/49fb85d5c1f5ad0cbd81725898398a6d75d9f423))

### Packaging and CI

- packaging: channels: a version tag publishes bromigos-vector to stable (vX.Y.Z) or rc (-rcN); main stays edge ([cf333d4](https://github.com/bromigos-org/vector-archive/commit/cf333d47e6c213d80bebb61c12ff6d9040a6ee64))
