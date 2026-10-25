"""VECTOR's brain on Pydantic AI (pydantic-ai-slim 2.54.0, pinned; venv-brain).

The same interface and callbacks as brain.py (state, delta, tool, reroute, done, error,
plus mood and memory), so the desktop doesn't care which one runs; voice.json "brain"
picks: "pai" (this) or "classic" (brain.py).

Lanes
  voice  nemotron-lightning-30b first: fast first token for chat and single lookups.
  deep   hive (Qwen3.8-Flash-Next, Nemotron behind it) for multi-tool reasoning.
  The voice lane escalates by itself: it has one extra tool, `think_harder`, and calling
  it hands the turn to the deep lane (no classifier on the hot path). While the deep
  lane starts, an in-character acknowledgement, already rendered in the TTS cache,
  plays in the current voice. voice.json "lanes" sets the models; if the accuracy gate
  (tools/brain-gate.py) fails, the voice lane is simply hive too.

Fallback
  Each lane is a FallbackModel over guarded models. A guard gives its model FIRST_BYTE
  seconds to produce the first stream event, then IDLE_GAP seconds between events;
  a model that misses the first-byte rule raises ModelAPIError while the stream is still
  opening, so FallbackModel moves on, the transcript shows "rerouting to <next>", and the
  failed model is skipped for COOLDOWN seconds.

Tools
  Exactly tools.SPECS, as in-process functions behind that allowlist (no framework
  built-ins). Every call goes through tools.call(), the one place read-only access is
  enforced and audited.
"""
import asyncio
import contextlib
import json
import os
import threading
import sys
import time

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import httpx2 as httpx  # noqa: E402  (pydantic-ai wants httpx2 for OpenAI-compatible providers)
from pydantic_ai import Agent, Tool  # noqa: E402
from pydantic_ai.exceptions import ModelAPIError  # noqa: E402
from pydantic_ai.messages import (FunctionToolCallEvent, ModelRequest, ModelResponse, PartDeltaEvent,  # noqa: E402
                                  PartStartEvent, TextPart, TextPartDelta, UserPromptPart)
from pydantic_ai.models.fallback import FallbackModel  # noqa: E402
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings  # noqa: E402
from pydantic_ai.models.wrapper import WrapperModel  # noqa: E402
from pydantic_ai.providers.openai import OpenAIProvider  # noqa: E402
from pydantic_ai.run import AgentRunResultEvent  # noqa: E402

from . import persona, tools  # noqa: E402
from .brain import CA, CHATLOG, KEY, _label  # noqa: E402
from ..private import PRIV  # noqa: E402

from . import llm  # noqa: E402
BASE = llm.base()                                    # vector.toml, the overlay, or a local Ollama
FIRST_BYTE = 9.0
IDLE_GAP = 25.0           # a model streaming tokens never pauses this long; tools run between requests
COOLDOWN = 120.0          # a backend that doesn't answer at all
STALL_COOLDOWN = 20.0     # one stream that went silent: likely that request, not the backend
from ..paths import conf  # noqa: E402
CONF = conf("voice.json")
ACKS = ["One moment, sir; consulting the deeper records.", "Hm-hm. Let me look properly.",
        "A good question. Allow me a moment."]


def lanes():
    try:
        with open(CONF) as f:
            c = json.load(f).get("lanes", {})
    except (OSError, ValueError):
        c = {}
    if not llm.homelab():                                # a local or other endpoint: its own model
        return c.get("voice") or [llm.model()], c.get("deep") or [llm.model()]
    return (c.get("voice") or ["nemotron-lightning-30b", "hive", "qwen3.8-flash-next"],
            c.get("deep") or ["hive", "nemotron-lightning-30b", "qwen3.8-flash-next"])


class Guard(WrapperModel):
    """First-byte and idle-gap rules for one model; tells the brain when it gives up."""

    def __init__(self, wrapped, brain):
        super().__init__(wrapped)
        self.brain = brain

    @contextlib.asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
        name = self.wrapped.model_name
        if self.brain.cooled.get(name, 0) > time.monotonic():
            raise ModelAPIError(name, "cooling down after a recent failure")
        t0 = time.monotonic()
        async with contextlib.AsyncExitStack() as stack:
            try:
                rs = await asyncio.wait_for(stack.enter_async_context(
                    self.wrapped.request_stream(messages, model_settings, model_request_parameters, run_context)),
                    FIRST_BYTE)
                it = rs._get_event_iterator().__aiter__()
                first = await asyncio.wait_for(it.__anext__(), max(0.5, FIRST_BYTE - (time.monotonic() - t0)))
            except (asyncio.TimeoutError, httpx.HTTPError, ModelAPIError, StopAsyncIteration) as e:
                why = "no answer in time" if isinstance(e, asyncio.TimeoutError) else type(e).__name__
                self.brain._failed(name, why)
                raise ModelAPIError(name, why) from e

            async def guarded():
                yield first
                while True:
                    try:
                        ev = await asyncio.wait_for(it.__anext__(), IDLE_GAP)
                    except StopAsyncIteration:
                        return
                    except asyncio.TimeoutError:
                        self.brain._failed(name, f"silent for {IDLE_GAP:.0f} s")
                        raise ModelAPIError(name, "stream went silent")
                    yield ev
            rs._get_event_iterator = guarded
            self.brain.model = name
            yield rs


class PaiBrain:
    def __init__(self, cb, ui=None, live=None):
        self.cb, self.ui, self.live = cb, ui, live
        self.history = []          # plain {role, content} turns (memory summaries, the session log)
        self.messages = []         # Pydantic AI message history
        self.busy = False
        self.model = None
        self.memory = None
        self.recalled = []
        self.attached = {}         # a prompt with attachments -> its reference, for when it ages out
        self.cooled = {}
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="brain-loop").start()
        self.task = None
        key = llm.key()
        import ssl
        ctx = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()
        self.http = httpx.AsyncClient(verify=ctx, timeout=httpx.Timeout(connect=5.0, read=IDLE_GAP, write=10.0, pool=5.0))
        provider = OpenAIProvider(base_url=BASE, api_key=key, http_client=self.http)
        self.settings = OpenAIChatModelSettings(temperature=0.5, max_tokens=900,   # 0.5: steadier tool choice (evals)
                                                extra_body={"chat_template_kwargs": {"enable_thinking": False}})
        voice, deep = lanes()
        mk = lambda names: FallbackModel(*[Guard(OpenAIChatModel(n, provider=provider), self) for n in names],  # noqa: E731
                                         fallback_on=(ModelAPIError,))
        self.lane_names = {"voice": voice, "deep": deep}
        self.voice_model, self.deep_model = mk(voice), mk(deep)
        self.escalate = None
        self.tools = [self._tool(n) for n in tools.SPECS]
        handoff = [] if voice == deep else [Tool.from_schema(
                                     self._think_harder, "think_harder",
                                     "Hand this question to your deeper self when it needs several lookups, careful "
                                     "reasoning or research. Use it instead of guessing; say nothing else first.",
                                     {"type": "object", "properties": {"why": {"type": "string"}}})]
        self.handoff = handoff
        self.skills_sig = None
        from . import skills
        self.router = skills.Router(skills.embed)
        self.router.warm()
        self.matched = []
        self._build_agents()

    def _build_agents(self):
        """(Re)build both lanes' agents with the current skills catalog (holo/vector/skills.py)."""
        from . import skills
        sig = skills.signature()
        if sig == self.skills_sig:
            return
        # No Pydantic AI capabilities: their deferred catalog goes out as a second system
        # message, and with two system messages hive answered from memory instead of calling
        # tools (2/5 vs 5/5 on the same prompt). The catalog is in the one system prompt
        # (_instructions) and load_skill is an ordinary tool.
        self.skills_sig = sig
        self.voice_agent = Agent(self.voice_model, model_settings=self.settings, tools=self.tools + self.handoff)
        self.deep_agent = Agent(self.deep_model, model_settings=self.settings, tools=self.tools)

    # ------------------------------------------------------------------ tools
    def _tool(self, name):
        desc, schema = tools.SPECS[name]

        def run(**args):
            if self.cancel.is_set():
                return json.dumps({"error": "interrupted"})
            self.cb.state("thinking")
            result, ex = tools.call(name, args, ui=self.ui, live=self.live)
            from .mood import from_tool
            mood = from_tool(name, result)
            rank = {"calm": 0, "excited": 1, "concerned": 2, "alarmed": 3}
            if mood and rank.get(mood, 0) > rank.get(getattr(self, "turn_mood", None) or "calm", 0):
                self.turn_mood = mood                # the strongest the facts warranted this turn
            if mood and hasattr(self.cb, "mood"):
                self.cb.mood(mood)
            self.stats["tools"] += 1
            self.cb.tool(name, args, _label(name, args, result), ex)
            self._log({"role": "tool", "name": name, "args": args if name not in getattr(tools, "PRIVATE_ARGS", {}) else "(private)",
                       "bytes": len(result)})
            return result
        return Tool.from_schema(run, name, desc, schema)

    def _think_harder(self, why=""):
        self.escalate = why or "deeper"
        return "Handed to the deep lane. Say nothing more."

    def _failed(self, name, why):
        # a stalled stream or a slow first token is likely load on that backend, not an outage: a short bench
        self.cooled[name] = time.monotonic() + (STALL_COOLDOWN if why.startswith("silent") else
                                                45.0 if why == "no answer in time" else COOLDOWN)
        self._log({"role": "reroute", "model": name, "why": why})
        chain = self.lane_names["deep" if self.lane == "deep" else "voice"]
        nxt = next((m for m in chain[chain.index(name) + 1:] if self.cooled.get(m, 0) <= time.monotonic()), None) \
            if name in chain else None
        if nxt:
            self.cb.reroute(nxt, f"{name} {why}")

    # ------------------------------------------------------------------ public
    def ask(self, text, attachments=None):
        """attachments: attach.Attachment list (files and images Sir handed him with it)."""
        if self.busy:
            self.interrupt()
            time.sleep(0.05)
        self.cancel.clear()
        self.task = asyncio.run_coroutine_threadsafe(self._run(text, attachments or []), self.loop)

    def interrupt(self):
        """Barge-in: cancel the run (the stream, and any tool not yet started), and stop a
        running terminal command (its whole process group)."""
        from .shell import RUNNER
        RUNNER.kill("interrupted")
        if self.busy:
            self.cancel.set()
            if self.task:
                self.task.cancel()

    def reset(self):
        self.history.clear()
        self.messages.clear()
        self.attached.clear()
        self._log({"role": "session_start"})

    def load_turns(self, turns):
        """Continue an earlier session: its recent turns become the context again."""
        self.reset()
        for u, v in turns:
            self.history += [{"role": "user", "content": u}, {"role": "assistant", "content": v}]
            self.messages += [ModelRequest(parts=[UserPromptPart(u)]), ModelResponse(parts=[TextPart(v or "…")])]

    def _age_attachments(self):
        """An attachment's contents stay in view for a few turns, then only its reference."""
        for m in self.messages[:-8]:
            for p in getattr(m, "parts", ()):
                if isinstance(p, UserPromptPart) and isinstance(p.content, str) and p.content in self.attached:
                    p.content = self.attached.pop(p.content) + "\n(read earlier; its contents are no longer in view)"
        shown = {p.content for m in self.messages for p in getattr(m, "parts", ()) if isinstance(p, UserPromptPart)
                 and isinstance(p.content, str)}
        self.attached = {k: v for k, v in self.attached.items() if k in shown}

    def _log(self, rec):
        os.makedirs(os.path.dirname(CHATLOG), exist_ok=True)
        with open(CHATLOG, "a") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **rec}) + "\n")

    def _instructions(self):
        now = time.strftime("%A %d %B %Y, %H:%M %Z")
        mem = ""
        if self.recalled:
            mem = ("\nWHAT YOU REMEMBER (your long-term memory, for this question; trust it but don't recite it)\n"
                   + "\n".join(f"- {x[:240]}" for x in self.recalled[:5]) + "\n")
        from . import skills
        from .eyes import WATCH
        return (persona.SYSTEM + persona.voices_block() + mem + WATCH.context() + skills.catalog_text() + skills.instructions_for(self.matched, question=getattr(self, "question", "")) +
                f"\nIt is {now}. The workstation is an Arch Linux desktop (Hyprland) the operator sits at.")

    async def _stream(self, agent, prompt, t0, history):
        text, result = "", None
        async with agent.run_stream_events(prompt, message_history=history, instructions=self._instructions()) as run:
            async for ev in run:
                if self.cancel.is_set():
                    break
                if isinstance(ev, AgentRunResultEvent):
                    result = ev.result
                    continue
                delta = None
                if isinstance(ev, PartStartEvent) and isinstance(ev.part, TextPart):
                    delta = ev.part.content
                elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                    delta = ev.delta.content_delta
                if delta:
                    delta = delta.replace("*", "")     # (the callsign is scrubbed where text is shown and spoken)     # spoken and shown as plain text: no markdown emphasis
                elif isinstance(ev, FunctionToolCallEvent) and ev.part.tool_name == "think_harder":
                    return text, None, True
                elif isinstance(ev, FunctionToolCallEvent) and ev.part.tool_name == "load_capability":
                    try:
                        sid = (ev.part.args_as_dict() or {}).get("id")
                    except Exception:
                        sid = None
                    self._log({"role": "skill", "name": sid})
                    from . import events
                    events.emit("skill.load", name=sid)
                if delta:
                    if self.stats["first_token_s"] is None:
                        self.stats["first_token_s"] = round(time.monotonic() - t0, 2)
                        self.cb.state("speaking")
                    text += delta
                    self.cb.delta(delta)
        msgs = result.all_messages() if result is not None else None
        return text, msgs, False

    async def _run(self, text, attachments=()):
        self._build_agents()
        self.busy = True
        t0 = time.monotonic()
        self.stats = {"first_token_s": None, "tools": 0, "lane": "voice", "brain": "pai"}
        self.turn_mood = None
        self.lane = "voice"
        self.escalate = None
        try:
            self.cb.state("thinking")
            self.recalled = []
            if self.memory is not None:            # capped at 0.2 s; skipped gracefully
                self.recalled, rs = await asyncio.to_thread(self.memory.recall,
                                                            text or " ".join(a.name for a in attachments))
                self.stats.update(rs)
                if self.recalled and hasattr(self.cb, "memory"):
                    self.cb.memory("recall", len(self.recalled))
            self.question = text
            self.matched = await asyncio.to_thread(self.router.match, text) if text.strip() else []
            for score, sk in self.matched:
                self._log({"role": "skill", "name": sk["name"], "auto": round(score, 2)})
                from . import events
                events.emit("skill.load", name=sk["name"], auto=True)
            from . import nolgia
            nolgia.LAST_USER["t"] = time.time()      # an over-cap spend needs a host turn after VECTOR asked
            from . import build
            build.LAST_USER["t"] = time.time()       # so does keeping a trial
            tools.LAST_USER_TEXT["text"] = text      # shell_off checks the host asked for it (his words, never an attachment's)
            tools.new_turn()                         # a block earlier stops tools only within its own turn
            prompt = text
            if attachments:      # the model reads them (attach.py); the log, history and memory keep a reference
                from . import attach
                self._log({"role": "user", "text": text, "attachments": attach.refs(attachments)})
                self.history.append({"role": "user", "content": attach.with_refs(text, attachments)})
                prompt = await asyncio.to_thread(attach.compose, text, attachments)
                self.attached[prompt] = attach.with_refs(text, attachments)
            else:
                self._log({"role": "user", "text": text})
                self.history.append({"role": "user", "content": text})
            out, msgs, escalated = await self._stream(self.voice_agent, prompt, t0, list(self.messages))
            if escalated and not self.cancel.is_set():
                self.lane = "deep"
                self.stats["lane"] = "deep"
                if hasattr(self.cb, "ack"):
                    self.cb.ack(ACKS[int(time.time()) % len(ACKS)])
                out2, msgs, _ = await self._stream(self.deep_agent, prompt, t0, list(self.messages))
                out = (out + " " + out2).strip()
            if self.cancel.is_set():
                self._log({"role": "interrupted", "text": out[:300]})
                self.history.append({"role": "assistant", "content": (out + " …").strip()})
                self.messages += [ModelRequest(parts=[UserPromptPart(prompt)]),
                                  ModelResponse(parts=[TextPart((out + " …").strip() or "…")])]
                return
            if msgs:
                self.messages = msgs[-40:]
                self._age_attachments()
            from .text import MOOD, scrub, tag_untagged
            out = scrub(tag_untagged(out))           # never the host's callsign, whatever the model wrote                  # the transcript and log say what the voice did
            tm = getattr(self, "turn_mood", None)
            rank = {"calm": 0, "excited": 1, "concerned": 2, "alarmed": 3}
            said = [m.group(1).lower() for m in MOOD.finditer(out)]
            if tm in ("concerned", "alarmed") and all(rank.get(x, 0) < rank[tm] for x in said):
                out = f"‹mood:{tm}›" + MOOD.sub("", out)  # the facts' mood, when he set none or a milder one
            self.history.append({"role": "assistant", "content": out})
            self.stats["model"] = self.model
            self.stats["total_s"] = round(time.monotonic() - t0, 2)
            self._log({"role": "vector", "text": out, "stats": self.stats})
            self.cb.done(out, self.stats)
        except asyncio.CancelledError:
            self._log({"role": "interrupted"})
        except Exception as e:  # every model failed, or something broke: say so, in character
            msg = str(e)[:160]
            import traceback
            tb = traceback.format_exc()
            print("brain: turn failed:\n" + tb, file=sys.stderr, flush=True)
            self._log({"role": "error", "text": msg, "where": tb.strip().splitlines()[-3:]})
            if "ModelAPIError" in type(e).__name__ or "FallbackExceptionGroup" in type(e).__name__ or "ExceptionGroup" in type(e).__name__:
                self.cb.error("every model on the rack went quiet. I'll keep the light on; try me again in a minute?")
            else:
                self.cb.error(f"{type(e).__name__}: {msg}")
        finally:
            self.busy = False
