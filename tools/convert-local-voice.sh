#!/usr/bin/env bash
# The local voice's GGUF files again, from Qwen's official weights, with qwentts.cpp's own tools
# at the revision qwentts-cpp-python 0.5.0 is built from; then a check that they hash to what
# holo/local_models.py pins. Nothing is installed: everything happens in WORKDIR.
#
#   tools/convert-local-voice.sh [WORKDIR]      # default: a new folder under /tmp
#
# Needs git, cmake, a C++ compiler, uv and about 9 GB of disk. Takes a few minutes. This is how
# the pinned hashes were made (2026-11-11): the result matched the qwentts.cpp author's published
# conversion byte for byte, which is why the voice fetches that copy and checks it against these.
set -euo pipefail

QWENTTS_REV=6fae92914045cd83364d2845ceaa0f7969727319        # qwentts-cpp-python 0.5.0's QWENTTS_NATIVE_REVISION
TALKER=Qwen/Qwen3-TTS-12Hz-0.6B-Base
TALKER_REV=5d83992436eae1d760afd27aff78a71d676296fc
TOKENIZER=Qwen/Qwen3-TTS-Tokenizer-12Hz
TOKENIZER_REV=7dd38ad4e9bad454aae9cd937d0cd577604fe229
WANT_TALKER=d54dbaf10591421fa764ed630d764efa717ae40cd959bd48c66d4eb1af226426
WANT_TOKENIZER=1883beeed99348fc35e23dd225e9082f93f6f8c109330a33d935baa8acdbfd94

W="${1:-$(mktemp -d /tmp/local-voice-convert.XXXXXX)}"
mkdir -p "$W" && cd "$W"
echo "working in $W"

[ -d qwentts.cpp ] || git clone --quiet https://github.com/ServeurpersoCom/qwentts.cpp
cd qwentts.cpp
git checkout --quiet "$QWENTTS_REV"
git submodule update --init --recursive --quiet
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release >/dev/null
cmake --build build --config Release -j "$(nproc)" --target quantize >/dev/null

# convert.py needs numpy, safetensors, gguf and torch (the processor build is enough)
uv venv --quiet --python 3.12 ../venv
uv pip install --quiet --python ../venv/bin/python numpy==2.5.4 safetensors==0.8.0 gguf==0.19.0 huggingface_hub
uv pip install --quiet --python ../venv/bin/python torch==2.14.1 --index-url https://download.pytorch.org/whl/cpu

mkdir -p checkpoints
../venv/bin/hf download "$TALKER" --revision "$TALKER_REV" --local-dir checkpoints/Qwen3-TTS-12Hz-0.6B-Base >/dev/null
../venv/bin/hf download "$TOKENIZER" --revision "$TOKENIZER_REV" --local-dir checkpoints/Qwen3-TTS-Tokenizer-12Hz >/dev/null

../venv/bin/python -I convert.py
./build/quantize models/qwen-talker-0.6b-base-F32.gguf models/qwen-talker-0.6b-base-Q8_0.gguf Q8_0
./build/quantize models/qwen-tokenizer-12hz-F32.gguf models/qwen-tokenizer-12hz-Q8_0.gguf Q8_0

ok=0
for pair in "qwen-talker-0.6b-base-Q8_0.gguf $WANT_TALKER" "qwen-tokenizer-12hz-Q8_0.gguf $WANT_TOKENIZER"; do
    set -- $pair
    got=$(sha256sum "models/$1" | cut -d' ' -f1)
    if [ "$got" = "$2" ]; then echo "ok   $1 $got"; else echo "FAIL $1 $got (want $2)"; ok=1; fi
done
exit $ok
