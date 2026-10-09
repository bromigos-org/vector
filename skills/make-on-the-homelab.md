---
name: make-on-the-homelab
description: How to make images, edits, cutouts (transparent PNGs), 3D meshes, upscales, new holograms and voice lines on the homelab for no credits, with create, make_hologram and the lab job queue (lab_job_submit) — and why never to fall back to a paid provider on your own.
when_to_use: "Making or changing a picture, removing a background, making a 3D model, or a new hologram of something; any request to generate, draw, design, edit, cut out or model."
---

# Making things on the homelab

The homelab's ComfyUI makes things for free. Everything you make goes there:

| capability | what it makes | needs | homelab model |
|---|---|---|---|
| `image.generate` | a new image | a prompt (optional reference images) | Qwen-Image 2.1 |
| `image.edit` | a changed image | a prompt and the image (optional mask: transparent = may change) | Qwen-Image 2.1 |
| `image.cutout` | a transparent PNG of the subject | the image | Lucida |
| `model3d.generate` | a GLB mesh, stood upright | an image, ideally a cutout | Pixal3D (shape only) |
| `video.loop` | — | — | none on the homelab yet |

`create` makes one; the file lands in `~/Pictures/vector/<date>/` and a notice shows it.
Tell the user what you made, where it is, and that it cost nothing.

## Never pay on your own

`create` and `make_hologram` run only on the homelab (`--provider comfyui:homelab`,
`--max-credits 0`). If the homelab is down, busy past its timeout, or has no model for the
thing (video today), they refuse. Then say so plainly and ask: wait for the homelab, or,
if the user asks for it by name, use nolgia. Never reach for nolgia yourself; it costs
real credits.

## Prompts that work

- **Images:** say the subject, the setting, the light and the style in plain sentences;
  name what must not appear (text, logos). One subject at a time works best.
- **Edits:** say only the change ("make the bell rose, the two centre arms cyan"); give a
  mask when only part should change.
- **Cutouts:** best on a subject against a plain background.
- **3D:** cut the concept out first; a whole, single object at a three-quarter angle on
  white gives the cleanest mesh. Pixal3D follows the picture closely, view and all; a
  mesh from a concept seen from above is stood upright afterwards. Holograms use geometry
  only: no textures.

## A new hologram

1. Load `make-hologram-model` (parts, live readings, hover hints).
2. `make_hologram` with a short `name` and a `subject` (or the user's `image`): concept →
   cutout → mesh, all on the homelab. It returns `build_goal`.
3. `build_start` with that goal: the builder bakes the mesh into the user's collection,
   checks it with renders, and puts it up in trial mode. Their hologram collection only
   changes through the build loop.
4. When the trial is up, say so; after the user keeps it, `show_hologram` it.

Report each step's model and that it cost nothing.

## The lab's job queue

`lab_job_submit` puts the same kinds of work, and more, on the lab's own queue (bromigOS's
`bromigos lab run`): `image`, `image-edit`, `cutout`, `3d`, `hologram` (made *and* baked, into
`~/Pictures/bromigos/lab/hologram/<name>/`), `voice` (a spoken line to a WAV, through the lab's
voice service), `transcribe` (an audio file to text), `upscale` (an image, or a short clip, made
larger and sharper: SeedVR2, 2x), `batch-llm` (many prompts through the lab's LLM server).
Presets: `world-painting`, `hologram`, `voice-line`.

- Use it when Sir asks for something on the lab by name, for a voice line or a
  transcription, for anything long (it runs while you keep talking), and when the GPU is
  busy: the queue waits for VRAM instead of failing.
- It waits its turn: one heavy GPU job at a time, only when the GPU has room, and the voice
  service you speak through always keeps its share. `wait` (up to 600 s) stays for the
  result; otherwise say it's queued and check later with `lab_jobs`.
- Report what it made and where (`outputs`), and that it cost nothing.
- `lab_job_cancel` cancels only your own jobs. A kind the lab can't run is refused with the
  reason (a clip too long to upscale says to trim it): say it, and ask.
- `lab_console` reads the lab's state (its cards); use it before a heavy job if Sir asks
  whether the lab is free.
