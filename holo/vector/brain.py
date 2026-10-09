"""VECTOR's brain: qwen3.8-flash-next on the homelab LiteLLM, streaming, with tool calls.

The key comes from ~/.local/share/bromigos/litellm-key (mode 600, from Vault
via `bromigos-secrets sync`; the path is in the private overlay); it is never logged. Thinking is turned off
(about 0.5 s to first token instead of about 6 s). The conversation is kept in memory
for the session and appended to ~/.local/state/bromigos/vector-chat.log (local only).

VECTOR never depends on one model. CHAIN is tried in order: `hive` (LiteLLM's default
alias: Qwen3.8-Flash-Next, Nemotron-Lightning behind it), then
Nemotron-Lightning by name, then Qwen by name. LiteLLM fails over by
itself when a backend errors, but a wedged backend hangs instead of erroring, so the
client also gives each model FIRST_BYTE seconds to start answering; on a timeout,
connection error or 5xx/429 the same turn is retried on the next model, and a model
that failed is skipped for COOLDOWN seconds. Once a stream is flowing, only a silent
gap of IDLE_GAP seconds counts as failure; long answers are never cut off.
"""
import http.client
import json
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request

from . import persona, tools
from ..private import PRIV

from . import llm  # noqa: E402
URL = llm.url("/chat/completions")                   # vector.toml, the overlay, or a local Ollama
CHAIN = ["hive", "nemotron-lightning-30b", "qwen3.8-flash-next"]
MODEL = CHAIN[0]
FIRST_BYTE = 9.0
IDLE_GAP = 45.0
COOLDOWN = 120.0
KEY = llm.key_file()
CA = os.path.expanduser("~/.config/homelab/homelab-ca.crt")
CHATLOG = os.path.expanduser("~/.local/state/bromigos/vector-chat.log")
MAX_ROUNDS = 6


class Reroute(Exception):
    """This model did not answer in time or failed; try the next one."""


class AllDown(Exception):
    pass


def _label(name, args, result=None):
    if name == "run_shell":            # the transcript shows the command line and how it ended
        cmd = " ".join(str((args or {}).get("command", "")).split())
        cmd = cmd if len(cmd) <= 70 else cmd[:67] + "…"
        try:
            r = json.loads(result) if isinstance(result, str) else (result or {})
        except ValueError:
            r = {}
        end = ("refused" if r.get("refused") else f"killed ({r['killed']})" if r.get("killed")
               else f"exit {r.get('exit')}" if "exit" in r else "…")
        return f"$ {cmd} · {end}"
    parts = []
    for k, v in (args or {}).items():
        if v in (None, "", [], {}, False):
            continue
        parts.append(k.replace("_", " ") if v is True else f"{v}")
    a = ", ".join(parts)
    return name.replace("_", " ") + (f" · {a}" if a else "")


class Brain:
    """cb: object with state(s), delta(text), tool(name, args, label, exhibit), reroute(model, why),
    done(text, stats), error(msg).
    Callbacks run on the worker thread; the app marshals them to GTK."""

    def __init__(self, cb, ui=None, live=None):
        self.cb = cb
        self.ui = ui
        self.live = live
        self.history = []
        self.lock = threading.Lock()
        self.busy = False
        self.cancel = threading.Event()
        self.ctx = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()
        self.cooled = {}           # model -> monotonic time it may be tried again
        self.model = MODEL         # the model that answered last
        self.memory = None         # holo.vector.memory.Memory, set by the app
        self.recalled = []

    def ask(self, text):
        if self.busy:
            self.cancel.set()
            time.sleep(0.05)
        self.cancel.clear()
        threading.Thread(target=self._run, args=(text,), daemon=True, name="vector-brain").start()

    def _log(self, rec):
        os.makedirs(os.path.dirname(CHATLOG), exist_ok=True)
        with open(CHATLOG, "a") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **rec}) + "\n")

    def _messages(self):
        now = time.strftime("%A %d %B %Y, %H:%M %Z")
        mem = ""
        if getattr(self, "recalled", None):
            mem = ("\nWHAT YOU REMEMBER (your long-term memory, for this question; trust it but don't recite it)\n"
                   + "\n".join(f"- {x[:240]}" for x in self.recalled[:5]) + "\n")
        sys = persona.SYSTEM + persona.voices_block() + mem + f"\nIt is {now}. The workstation is an Arch Linux desktop (Hyprland) the operator sits at."
        return [{"role": "system", "content": sys}] + self.history[-24:]

    def _run(self, text):
        with self.lock:
            self.busy = True
            t0 = time.monotonic()
            stats = {"first_token_s": None, "tools": 0, "rounds": 0}
            try:
                self.cb.state("thinking")
                self.recalled = []
                if self.memory is not None:        # capped at 0.2 s; skipped gracefully
                    self.recalled, rs = self.memory.recall(text)
                    stats.update(rs)
                    if self.recalled and hasattr(self.cb, "memory"):
                        self.cb.memory("recall", len(self.recalled))
                self.history.append({"role": "user", "content": text})
                self._log({"role": "user", "text": text})
                final = ""
                for rnd in range(MAX_ROUNDS):
                    stats["rounds"] = rnd + 1
                    content, calls = self._stream_any(stats, t0)
                    if self.cancel.is_set():
                        # interrupted (push-to-talk barge-in): keep what was said so the history stays coherent
                        self.history.append({"role": "assistant", "content": (content + " …").strip() if content else "…"})
                        self._log({"role": "interrupted", "text": content[:300]})
                        return
                    if calls:
                        self.history.append({"role": "assistant", "content": content or None, "tool_calls": calls})
                        for c in calls:
                            name = c["function"]["name"]
                            try:
                                args = json.loads(c["function"]["arguments"] or "{}")
                            except ValueError:
                                args = {}
                            self.cb.state("thinking")
                            result, ex = tools.call(name, args, ui=self.ui, live=self.live)
                            label = _label(name, args, result)
                            stats["tools"] += 1
                            from .mood import from_tool
                            mood = from_tool(name, result)
                            if mood and hasattr(self.cb, "mood"):
                                self.cb.mood(mood)
                            self.cb.tool(name, args, label, ex)
                            self._log({"role": "tool", "name": name, "args": args, "bytes": len(result)})
                            self.history.append({"role": "tool", "tool_call_id": c["id"], "content": result})
                        continue
                    final = content
                    break
                else:
                    final = "Sorry, sorry, I went round in circles there. Ask me again, maybe a bit narrower?"
                    self.cb.delta(final)
                self.history.append({"role": "assistant", "content": final})
                # older tool results are large; keep only their gist in history
                for m in self.history[:-8]:
                    if m.get("role") == "tool" and len(m.get("content") or "") > 1500:
                        m["content"] = m["content"][:1500] + "…"
                stats["total_s"] = round(time.monotonic() - t0, 2)
                self._log({"role": "vector", "text": final, "stats": stats})
                self.cb.done(final, stats)
            except AllDown as e:
                self._log({"role": "error", "text": str(e)[:300]})
                self.cb.error("every model on the rack went quiet (" + str(e)[:140] + "). I'll keep the light on; try me again in a minute?")
            except Exception as e:  # say what broke, in character, and keep going
                self._log({"role": "error", "text": str(e)[:300]})
                self.cb.error(f"{type(e).__name__}: {str(e)[:160]}")
            finally:
                self.busy = False

    def _stream_any(self, stats, t0):
        """One model round, rerouting down CHAIN until a model answers."""
        now = time.monotonic()
        order = [m for m in CHAIN if self.cooled.get(m, 0) <= now] or list(CHAIN)
        why_all = []
        for k, model in enumerate(order):
            try:
                out = self._stream(model, stats, t0)
                self.cooled.pop(model, None)
                if model != self.model:
                    self._log({"role": "route", "model": model})
                self.model = model
                stats["model"] = model
                return out
            except Reroute as e:
                why = str(e)[:80]
                why_all.append(f"{model}: {why}")
                self.cooled[model] = time.monotonic() + COOLDOWN
                self._log({"role": "reroute", "model": model, "why": why})
                if self.cancel.is_set():
                    raise AllDown("cancelled")
                if k + 1 < len(order):
                    self.cb.reroute(order[k + 1], f"{model} {why}")
                    self.cb.state("thinking")
        raise AllDown("; ".join(why_all))

    def _stream(self, model, stats, t0):
        try:
            return self._stream_once(model, stats, t0)
        except urllib.error.HTTPError as e:
            if e.code >= 500 or e.code in (408, 429):
                raise Reroute(f"HTTP {e.code}")
            raise
        except (TimeoutError, socket.timeout, urllib.error.URLError, ConnectionError, ssl.SSLError, http.client.HTTPException) as e:
            raise Reroute("no answer in time" if "timed out" in str(e) else type(e).__name__)

    def _stream_once(self, model, stats, t0):
        key = llm.key()
        body = {"model": model, "messages": self._messages(), "tools": tools.schemas(), "tool_choice": "auto",
                "stream": True, "temperature": 0.7, "max_tokens": 900,
                "chat_template_kwargs": {"enable_thinking": False}}
        req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        content = ""
        calls = {}
        spoke = False
        with urllib.request.urlopen(req, timeout=FIRST_BYTE, context=self.ctx) as r:
            backend = r.headers.get("X-Litellm-Model-Name") or ""
            if backend:
                stats["backend"] = backend.rsplit("/", 1)[-1]     # which deployment answered (hive has two)
            flowing = False
            for raw in r:
                if not flowing and raw.strip():
                    flowing = True           # the model is answering: from here only a long silence fails
                    try:
                        r.fp.raw._sock.settimeout(IDLE_GAP)
                    except AttributeError:
                        pass
                if self.cancel.is_set():
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if obj.get("error"):            # LiteLLM reports a failed upstream inside the stream
                    raise Reroute(str((obj["error"] or {}).get("message", obj["error"]))[:60] if isinstance(obj["error"], dict) else str(obj["error"])[:60])
                try:
                    ch = obj["choices"][0]
                except (KeyError, IndexError):
                    continue
                d = ch.get("delta") or {}
                if d.get("content"):
                    if stats["first_token_s"] is None:
                        stats["first_token_s"] = round(time.monotonic() - t0, 2)
                    if not spoke:
                        self.cb.state("speaking")
                        spoke = True
                    content += d["content"]
                    self.cb.delta(d["content"])
                for tc in d.get("tool_calls") or []:
                    i = tc.get("index", 0)
                    c = calls.setdefault(i, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        c["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        c["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        c["function"]["arguments"] += fn["arguments"]
        out = [calls[i] for i in sorted(calls)]
        for k, c in enumerate(out):
            c["id"] = c["id"] or f"call_{int(time.time() * 1000)}_{k}"
        return content.strip(), out

    def interrupt(self):
        """Barge-in: stop the turn now (the stream loop checks this between chunks)."""
        if self.busy:
            self.cancel.set()

    def reset(self):
        self.history.clear()
        self._log({"role": "session_start"})

    def load_turns(self, turns):
        self.reset()
        for u, v in turns:
            self.history += [{"role": "user", "content": u}, {"role": "assistant", "content": v}]
