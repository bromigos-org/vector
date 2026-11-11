"""The local voice's model files: where they come from, pinned by revision and sha256, and the
fetch that puts them in the user's cache (~/.cache/bromigos/local-voice). Nothing here is
committed to the repository or shipped in the package.

  tts  Qwen3-TTS 12 Hz 0.6B Base (Qwen, Apache-2.0) as 8-bit GGUF for qwentts.cpp: the talker
       and the 12 Hz speech tokenizer. We converted these ourselves from Qwen's official
       safetensors (Qwen/Qwen3-TTS-12Hz-0.6B-Base at 5d839924…, Qwen/Qwen3-TTS-Tokenizer-12Hz at
       7dd38ad4…) with qwentts.cpp's own tools at 6fae9291… (convert.py, then quantize Q8_0), the
       revision qwentts-cpp-python 0.5.0 is built from. tools/convert-local-voice.sh does it again
       and checks the hashes. Our files came out byte for byte the same as the qwentts.cpp
       author's published conversion, so they're fetched from there; the hashes below are ours,
       and a file that doesn't match them is deleted, never used.
  stt  NVIDIA Parakeet TDT 0.6B v2 (CC-BY-4.0: the attribution is in docs/configuration.md and
       bromigOS's docs/ASSETS.md), the int8 ONNX export onnx-asr reads.

Standard library only (urllib), so `bromigos-holo setup local-voice` and the voice server
can both run it. Run: python3 -m holo.local_models [fetch|check|where]
"""
import hashlib
import json
import os
import sys
import time
import urllib.request

CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "bromigos", "local-voice")
HF = "https://huggingface.co"

MODELS = {
    "tts": {
        "name": "Qwen3-TTS 12 Hz 0.6B Base, Q8_0 GGUF",
        "licence": "Apache-2.0 (Qwen)",
        "repo": "Serveurperso/Qwen3-TTS-GGUF",
        "revision": "b7ee2e8c7459c3bea99da23e3d178125a7d1713c",
        "files": {
            "qwen-talker-0.6b-base-Q8_0.gguf":
                ("d54dbaf10591421fa764ed630d764efa717ae40cd959bd48c66d4eb1af226426", 992615488),
            "qwen-tokenizer-12hz-Q8_0.gguf":
                ("1883beeed99348fc35e23dd225e9082f93f6f8c109330a33d935baa8acdbfd94", 291150624),
        },
    },
    "stt": {
        "name": "NVIDIA Parakeet TDT 0.6B v2, int8 ONNX",
        "licence": "CC-BY-4.0 (NVIDIA)",
        "repo": "istupakov/parakeet-tdt-0.6b-v2-onnx",
        "revision": "0bbb45a3365852604aef28b538a8f066f4ccaa85",
        "files": {
            "config.json": ("666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466", 97),
            "vocab.txt": ("ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d", 9384),
            "encoder-model.int8.onnx":
                ("3e0581fda6ab843888b51e56d7ee78b6d5bc3237ec113af1f732d1d5286aa155", 652184014),
            "decoder_joint-model.int8.onnx":
                ("a449f49acd68979d418651dd2dcb737cc0f1bf0225e009e29ee326354edbf7d3", 8998286),
        },
    },
}


def where(kind, root=None):
    return os.path.join(root or CACHE, kind)


def path(kind, name, root=None):
    return os.path.join(where(kind, root), name)


def _stamp(kind, root=None):
    return os.path.join(where(kind, root), ".verified.json")


def _sha256(p, chunk=1 << 22):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                return h.hexdigest()
            h.update(b)


def present(kind=None, root=None):
    """Whether every file is there and was verified (its sha256 checked once, then trusted while
    its size and mtime stay the same)."""
    kinds = [kind] if kind else list(MODELS)
    for k in kinds:
        try:
            with open(_stamp(k, root)) as f:
                seen = json.load(f)
        except (OSError, ValueError):
            return False
        for name, (sha, size) in MODELS[k]["files"].items():
            p = path(k, name, root)
            try:
                st = os.stat(p)
            except OSError:
                return False
            if st.st_size != size or seen.get(name) != [sha, st.st_size, st.st_mtime_ns]:
                return False
    return True


def missing_bytes(root=None):
    n = 0
    for k, m in MODELS.items():
        for name, (_sha, size) in m["files"].items():
            p = path(k, name, root)
            if not (os.path.exists(p) and os.path.getsize(p) == size):
                n += size
    return n


def url(kind, name):
    m = MODELS[kind]
    return f"{HF}/{m['repo']}/resolve/{m['revision']}/{name}"


def _download(u, dest, sha, size, log, opener=urllib.request.urlopen):
    tmp = dest + ".part"
    h = hashlib.sha256()
    got = 0
    req = urllib.request.Request(u, headers={"User-Agent": "bromigos-vector local voice"})
    with opener(req, timeout=60) as r, open(tmp, "wb") as f:
        last = time.monotonic()
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            f.write(b)
            h.update(b)
            got += len(b)
            if time.monotonic() - last > 10:
                log(f"  {os.path.basename(dest)}: {got >> 20} of {size >> 20} MiB")
                last = time.monotonic()
    if got != size or h.hexdigest() != sha:
        os.unlink(tmp)
        raise RuntimeError(f"{os.path.basename(dest)}: got {got} bytes, sha256 {h.hexdigest()[:16]}…; "
                           f"want {size} bytes, {sha[:16]}…. Deleted, not used")
    os.replace(tmp, dest)


def fetch(kind=None, root=None, log=print, opener=urllib.request.urlopen):
    """Download what's missing or wrong, check each file's sha256, record it. Raises on a
    mismatch (the bad file is deleted)."""
    for k in ([kind] if kind else list(MODELS)):
        m = MODELS[k]
        d = where(k, root)
        os.makedirs(d, exist_ok=True)
        try:
            with open(_stamp(k, root)) as f:
                seen = json.load(f)
        except (OSError, ValueError):
            seen = {}
        for name, (sha, size) in m["files"].items():
            p = path(k, name, root)
            ok = False
            if os.path.exists(p) and os.path.getsize(p) == size:
                st = os.stat(p)
                ok = seen.get(name) == [sha, st.st_size, st.st_mtime_ns] or _sha256(p) == sha
            if not ok:
                log(f"local voice: fetching {m['name']}: {name} ({size >> 20} MiB)")
                _download(url(k, name), p, sha, size, log, opener)
            st = os.stat(p)
            seen[name] = [sha, st.st_size, st.st_mtime_ns]
        with open(_stamp(k, root) + ".part", "w") as f:
            json.dump(seen, f)
        os.replace(_stamp(k, root) + ".part", _stamp(k, root))
    return True


def main(argv):
    what = (argv[:1] or ["check"])[0]
    if what == "where":
        print(CACHE)
        return 0
    if what == "check":
        ok = present()
        print("local voice models: " + ("present and verified" if ok else
                                        f"missing ({missing_bytes() >> 20} MiB to fetch)") + f" in {CACHE}")
        return 0 if ok else 1
    if what == "fetch":
        fetch()
        print(f"local voice models: present and verified in {CACHE}")
        return 0
    print("python3 -m holo.local_models [check|fetch|where]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
