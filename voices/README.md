# VECTOR's designed voices

These are original synthetic voices. Each was designed from a text description with
Qwen3-TTS-12Hz-1.7B-VoiceDesign (Apache-2.0). No one's recording was used.

Each `vector-ref-*.wav` is a single reference recording. The Qwen3-TTS Base model speaks
every line from it, so the voice stays the same from sentence to sentence.

## The voices

| File | Seed | Median pitch | Notes |
|---|---|---|---|
| `vector-ref-A.wav` | 27 | about 126 Hz | The default |
| `vector-ref-B.wav` | 27 | about 125 Hz | The most lilting |
| `vector-ref-C.wav` | 3 | about 168 Hz | Brighter; same description as B |

The descriptions given to the designer:

- **A:** "An older English gentleman in his sixties with a bright, prim and precise voice,
  crisp diction and quiet authority; cheerful, sing-song and delighted, like a very
  polite, very old caretaker who loves his job. Clear, warm and a little formal."
- **B and C:** "A mature male voice in refined Received Pronunciation, gently high and
  bright, chipper and sing-song, crisp clipped diction, an air of ancient authority and
  quiet amusement."

Every reference speaks the same line: "Hello, and welcome. I am the caretaker, and
everything here is running exactly as it should. Protocol is wise, and I am simply
delighted to see you."

A third description, "a precise elderly British voice, light and tenor …", came out at
about 250 Hz. That isn't an older man, so it was dropped.

## Choosing a voice

`voice.json`'s `designed_fallback` names the reference and its transcript. A voice
without a reference of its own speaks with this one. To use B, set:

```json
"designed_fallback": {
  "ref": "voices/vector-ref-B.wav",
  "text": "Hello, and welcome. I am the caretaker, and everything here is running exactly as it should. Protocol is wise, and I am simply delighted to see you."
}
```

The path is relative to the package, `/usr/lib/bromigos/vector/`. A voice in the `voices`
table can have its own `ref` instead. That needs a WAV file with a `.txt` transcript of the
same name beside it. [docs/configuration.md](../docs/configuration.md#voices-and-moods)
covers the voices table.

## Designing another voice

1. Run the VoiceDesign model with a new description and a fixed seed.
2. Check the pitch fits an older man.
3. Save the WAV here, or anywhere for your own use, with its transcript.
4. Point `designed_fallback.ref`, or a voice's `ref`, at it.

Never use a recording of a real person, or of a game or film character, as a reference.
