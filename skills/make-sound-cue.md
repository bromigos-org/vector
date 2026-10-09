---
name: make-sound-cue
description: How to make or change a sound or voice cue on the desktop — the live layer's cue files and config, VECTOR's dial-scratch cue and voices, making a new cue (nolgia sound effects, or synthesised locally), loudness and length budgets, and the no-cloned-voices rule.
when_to_use: "Making a new notification sound, chirp, alert, UI sound, jingle, or a spoken voice line or cue for VECTOR or the live layer."
---

# Sound and voice cues

## Where cues live

- **Live layer** (`linux/.config/bromigos-live/`): `sounds/*.wav` (blip, chirp, alert,
  hiss, sweep, signoff), chosen by `[sounds]` in `config.toml` (`terminal`, `notify`,
  `critical`, `hiss`, `sweep`, `signoff`, `volume` 0.30, `quiet_apps`). Played by
  `live/sound.py` (pw-play). The config reloads on save; a new cue is a new wav plus a
  config line. Muting: SUPER+SHIFT+M.
- **VECTOR** (`linux/.config/bromigos/holo/`): `voice.json` `cue` (the dial scratch
  between voices, 0.24 s, `holo/shimmer.py` `dial_scratch`), the per-voice `shimmer`,
  and his voices (local Qwen3-TTS from the references in `~/.local/share/bromigos/voices/`;
  see `holo/README.md` "Voice").

## Making a cue

- **Procedural first** (free, exact): synthesise with numpy in a short script (sine
  blips, filtered noise sweeps, chirps), 44.1 or 48 kHz mono 16-bit, written with the
  `wave` module or soundfile. Keep it in the house: short, clean, radio-like, a little
  phosphor hum; nothing comic.
- **nolgia** when it needs to be richer: `nolgia_generate audio "<sound>, short, clean,
  no music" model=elevenlabs-sound-effects-v2` (about 4 credits), then trim and level
  with ffmpeg: `ffmpeg -i in.mp3 -af "silenceremove=1:0:-50dB,loudnorm=I=-24:TP=-3"
  -t 0.6 -ac 1 -ar 48000 out.wav`.
- **Voice lines**: VECTOR's own voices only (his TTS), or a nolgia TTS stock voice. Never
  clone or imitate a real person's, a game's or a film's voice.

## Budgets and rules

- UI cues: under 0.6 s, peak under -3 dBFS, integrated loudness about -24 LUFS (they
  play over music and calls). Alerts may be up to 1.5 s.
- Cues are rare and mean something (an event happened); nothing loops forever.
- Quiet modes are respected (muted, recording, fullscreen).
- Check: `ffprobe` (duration, rate), `ffmpeg -af ebur128` (loudness); say you haven't
  heard it, and play it for the user only if they ask (`pw-play out.wav`).
- Commit the wav and the config change through the build loop (files under
  `linux/.config/bromigos-live/` or `holo/`).
