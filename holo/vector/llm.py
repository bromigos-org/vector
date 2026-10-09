"""Where VECTOR's models are: one OpenAI-compatible endpoint for the brain, his eyes,
summaries and embeddings (docs/PLAN.md in bromigOS: local-first, Ollama or any
OpenAI-compatible server).

In force, the first that is set:

  vector.toml [llm]            base_url, api_key_file, embed_model (paths.conf: the user's
                               ~/.config/bromigos/vector/vector.toml, /etc, the package's)
  targets.toml                 the `llm` service's targets in order (homelab, local, cloud;
                               bromigOS docs/targets.md), when the user has a targets.toml:
                               the first whose server answers (`bromigos target local` moves
                               his brain to this machine on his next turn)
  the private overlay          endpoints.litellm (the operator's homelab LiteLLM)
  a local Ollama               http://localhost:11434/v1

The key is read from api_key_file (default ~/.local/share/bromigos/litellm-key), or
$VECTOR_API_KEY; a local server without auth gets a placeholder. The key is never
logged or returned by a tool. The brain's wiring is protected safety code, and so is
this file (guard.py).
"""
import os

from .. import targets
from ..paths import conf
from ..private import PRIV

HOME = os.path.expanduser("~")
OLLAMA = "http://localhost:11434/v1"
DEFAULT_KEY_FILE = os.path.join(HOME, ".local/share/bromigos/litellm-key")

_cfg = None


def settings():
    """vector.toml's [llm] table ({} when there is none)."""
    global _cfg
    if _cfg is None:
        _cfg = {}
        try:
            import tomllib
            with open(conf("vector.toml"), "rb") as f:
                _cfg = tomllib.load(f).get("llm", {}) or {}
        except (OSError, ValueError):
            _cfg = {}
    return _cfg


def chosen():
    """targets.toml's entry for the brain (the first whose server answers, else the first),
    or None when vector.toml names a base_url or there's no targets.toml."""
    if settings().get("base_url"):
        return None
    es = [e for e in (targets.resolve("llm") or []) if e.get("endpoint")]
    if not es:
        return None
    return next((e for e in es if targets.answers(e["endpoint"])), es[0])


def base():
    """The endpoint's /v1 base URL, no trailing slash."""
    t = chosen()
    b = settings().get("base_url") or (t and t["endpoint"]) or PRIV.url("litellm", "/v1") or OLLAMA
    return b.rstrip("/")


def url(path):
    """base() + an API path such as /chat/completions or /embeddings."""
    return base() + "/" + path.lstrip("/")


def key_file():
    t = chosen()
    return os.path.expanduser(settings().get("api_key_file") or (t and t.get("key_file")) or DEFAULT_KEY_FILE)


def key():
    """The API key (never logged); a placeholder when the server needs none."""
    try:
        with open(key_file()) as f:
            k = f.read().strip()
        if k:
            return k
    except OSError:
        pass
    return os.environ.get("VECTOR_API_KEY") or "none"


def embed_model():
    """The embedding model: vector.toml, else the homelab's (when the overlay names its
    LiteLLM), else Ollama's nomic-embed-text."""
    m = settings().get("embed_model")
    if m:
        return m
    return "local-qwen3-embedding-0.6b" if homelab() else "nomic-embed-text"


def homelab():
    """True when his brain is the homelab's LiteLLM: targets.toml's choice, else the
    private overlay naming it (the operator's setup) with no base_url of vector.toml's."""
    t = chosen()
    if t is not None:
        return t.get("target") == "homelab"
    return bool(PRIV.url("litellm")) and not settings().get("base_url")


def model(role="main"):
    """A model name for a role: main (summaries, briefings, the brain's default lane) or
    vision (his eyes, image reads). vector.toml [llm] model / vision_model, else the
    homelab's aliases, else a local Ollama's."""
    key = "vision_model" if role == "vision" else "model"
    m = settings().get(key)
    if m:
        return m
    if homelab():
        return "qwen3.8-flash-next" if role == "vision" else "hive"
    return "qwen2.5vl:7b" if role == "vision" else "qwen3:8b"
