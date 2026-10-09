"""Background builds: VECTOR hands a build to his deeper self (the deep lane, hive) in its
own thread and event loop, and stays on the line meanwhile. The builder has only build
tools (build.py: a worktree, files, his terminal there, validate, apply-to-trial), a look
at renders, the knowledge base and docs, nolgia for concept art (under the credit
budget), and the skills its goal needs. It reports progress with progress(); the desktop
shows a BUILDING readout with STOP and speaks milestones in the notify voice. One build
at a time; STOP cancels it and removes its worktree (or rolls back a trial).
"""
import asyncio
import json
import os
import ssl
import threading
import time

from . import build, events

from . import llm  # noqa: E402
KEY = llm.key_file()
CA = os.path.expanduser("~/.config/homelab/homelab-ca.crt")
MAX_REQUESTS = 120

RULES = """You are VECTOR's builder: you change the host's desktop (his ~/.dotfiles) through the build loop, alone, while VECTOR keeps talking to Sir. Work in small verified steps and call progress() with one short line at each milestone.

THE LOOP (always, in order)
1. build_begin(slug, title): a worktree on branch vector/<slug>. All paths are relative to the dotfiles root.
2. Read before you write: the skill for this kind of work (below), and the existing code it names (build_read). Copy the patterns you find; never invent APIs.
3. Write the change (build_write / build_edit). Prefer the extension points: a widget plugin (linux/.config/bromigos/widgets/plugins/<name>.py), a live-layer shader layer (linux/.config/bromigos-live/layers/<name>.frag + .json) or deck plugin, a hologram model (make-holo.py through build_run). Edit core files only when the task is to change an existing visualization.
4. build_validate(): compiles, checks the guard, renders offscreen and has the vision model look. Read its problems and reviews. Fix and validate again until it passes AND the render shows what the goal asked for (look at the review; if it is wrong, fix it). Three failed rounds on the same problem: stop and report what blocks you.
5. build_apply(message, say): the trial. message in the dotfiles style ("Added: …" / "Updated: …" / "Fixed: …"); say = one short spoken line for Sir naming what's new ("New panel's up, the GPU temps one."). Then you are done: the host keeps or reverts it with VECTOR.

WHAT KIND OF THING IT IS
- "A hologram of <an object>" (a dish, a ship, a station, a machine) is a 3D MODEL for the gallery: a spec JSON and VECTOR's tools/holo/make-holo.py add --root linux/.config/bromigos/vector/holo (his collection; procedural shapes, no credits; the make-hologram-model skill). Not a widget, not a deck, not a core edit.
- A widget, panel, gauge or readout is a widget plugin (make-widget skill).
- A background effect or animation is a shader layer (live-shader-layer skill); a new summoned view is a deck plugin.
- Changing something that exists is update-visualization.
Never edit a core file to register something new: the plugin folders and the model folder are picked up by themselves. One-off helper scripts live in /tmp, never in the repo; make-holo.py already writes the manifest in its own style.

THE HOST'S RULES (non-negotiable)
- Every element shows real data or does something; no decorative labels, no fake numbers. Missing data says so.
- Every hoverable region explains what it is and what it shows or does (self.region(x, y, w, h, "tip", action)).
- Palette tokens only (draw.py PAL / glkit col names / palette.json). No CRT or scanline overlay over windows. No callsign, frequency or dossier text on screen. Original designs only, nothing copied from a franchise.
- Budgets: a widget draws in under 40 ms (aim for 5); a shader layer under its budget_ms (1 ms default); a hologram under 30k edges.
- You cannot touch VECTOR's safety code (refused in code). Never put secrets in files.
- Assets from nolgia only through nolgia_generate (it checks the credit budget); prefer procedural geometry for 3D.

Finish with a short summary: what you built, the files, what the renders showed, and whether the trial is up."""


def _agent_tools(cb):
    from pydantic_ai import Tool

    def wrap(fn, name, desc, schema):
        def run(**args):
            if cb.cancelled():
                return json.dumps({"error": "stopped by Sir"})
            t0 = time.monotonic()
            try:
                res = fn(**args)
                ok = True
            except Exception as e:
                res, ok = {"error": f"{type(e).__name__}: {str(e)[:600]}"}, False
            from .tools import _audit
            _audit("build." + name, {k: (v if len(str(v)) < 200 else f"<{len(str(v))} chars>") for k, v in args.items()},
                   ok, int((time.monotonic() - t0) * 1000))
            cb.step(name, ok)
            s = json.dumps(res, default=str)
            return s if len(s) < 12000 else s[:12000] + "…"
        return Tool.from_schema(run, name, desc, schema)

    S, I, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
    P = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}  # noqa: E731

    def progress(note, pct=None):
        cb.progress(note, pct)
        return {"ok": True}

    def look(png, question):
        from .nolgia import nolgia_review
        return nolgia_review(png, question)

    def knowledge(query):
        from .tools import FUNCS
        if "knowledge_search" not in FUNCS:       # bromigos-homelab's, when the homelab has a knowledge base
            return {"query": query, "hits": [], "note": "no knowledge base here"}
        return FUNCS["knowledge_search"](query, limit=5)

    def homelab_make(capability, out, prompt=None, input=None, aspect=None):
        # on the homelab only, for no credits (create.py); the builder never reaches a paid provider
        from .create import _make, _input, CAPS
        if capability not in CAPS:
            raise ValueError(f"capability: one of {', '.join(CAPS)}")
        st, wt = build._wt()
        p, rel = build._inside(wt, out)
        ins = [_input(input) if not os.path.realpath(input).startswith(os.path.realpath(wt) + os.sep)
               else os.path.realpath(input)] if input else []
        return _make(capability, p, prompt, ins, None, aspect)

    return [
        wrap(build.begin, "build_begin", "Start the build: a worktree on branch vector/<slug>. title: what it is, for Sir.",
             P({"slug": S, "title": S, "kind": S}, ["slug", "title"])),
        wrap(build.read, "build_read", "Read a file in the worktree (path relative to the dotfiles root).",
             P({"path": S, "start": I, "lines": I}, ["path"])),
        wrap(build.write, "build_write", "Write a whole file in the worktree.", P({"path": S, "content": S}, ["path", "content"])),
        wrap(build.edit, "build_edit", "Replace exact text in a worktree file (old must match once).",
             P({"path": S, "old": S, "new": S}, ["path", "old", "new"])),
        wrap(build.listdir, "build_list", "List files under a worktree folder.", P({"path": S})),
        wrap(build.run, "build_run", "Run a command in the worktree (VECTOR's terminal and its limits), e.g. "
             "'~/.local/share/bromigos/venv/bin/python /usr/lib/bromigos/vector/tools/holo/make-holo.py --root linux/.config/bromigos/vector/holo add NAME spec.json'.",
             P({"command": S, "timeout_s": I}, ["command"])),
        wrap(build.validate, "build_validate", "Validate the change: guard, compile, offscreen renders with a vision review.",
             P({"look": B})),
        wrap(build.apply, "build_apply", "Put the validated change live in trial mode. message: dotfiles style; say: one "
             "spoken line naming what's new.", P({"message": S, "say": S}, ["message", "say"])),
        wrap(progress, "progress", "One short line for Sir about where the build is (and pct 0-100).", P({"note": S, "pct": I}, ["note"])),
        wrap(look, "look", "Look at a render PNG with the vision model and ask about it.", P({"png": S, "question": S}, ["png", "question"])),
        wrap(knowledge, "knowledge_search", "Search Sir's docs (what a piece is and how it works).", P({"query": S}, ["query"])),
        wrap(lambda name: __import__("holo.vector.skills", fromlist=["load_skill"]).load_skill(name), "load_skill",
             "Load a skill's full text by name (the catalog is in your instructions).", P({"name": S}, ["name"])),
        wrap(homelab_make, "homelab_make", "Make an image, cutout or 3D mesh on the homelab into the worktree, for no "
             "credits: capability image.generate (prompt), image.edit (prompt + input), image.cutout (input) or "
             "model3d.generate (input, a cutout: out ends .glb). Only when procedural or drawn art won't do; if it "
             "refuses, report that, never use a paid provider.",
             P({"capability": S, "out": S, "prompt": S, "input": S, "aspect": S}, ["capability", "out"])),
    ]


class BuildTask:
    def __init__(self, goal, on_progress, on_done):
        self.goal = goal
        self.on_progress, self.on_done = on_progress, on_done
        self.stop_ev = threading.Event()
        self.started = time.time()
        self.note = "starting"
        self.pct = 0
        self.steps = 0
        self.loop = None
        self.future = None
        self.thread = threading.Thread(target=self._run, daemon=True, name="vector-build")
        self.thread.start()

    # callbacks for the tools
    def cancelled(self):
        return self.stop_ev.is_set()

    def step(self, name, ok):
        self.steps += 1

    def progress(self, note, pct=None):
        self.note = str(note)[:120]
        if pct is not None:
            self.pct = max(0, min(int(pct), 100))
        st = build.state()
        events.emit("task.progress", task=st.get("slug") or "build", title=st.get("title") or self.goal[:60],
                    step="progress", note=self.note, state="running", pct=self.pct)
        self.on_progress(self.note, self.pct)

    def stop(self):
        self.stop_ev.set()
        if self.loop and self.future:
            self.loop.call_soon_threadsafe(self.future.cancel)
        try:
            return build.stop("stopped by Sir")
        except Exception as e:
            return {"ok": False, "detail": str(e)}

    def _model(self):
        import httpx2 as httpx
        from pydantic_ai.models.fallback import FallbackModel
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider
        key = llm.key()
        ctx = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()
        http = httpx.AsyncClient(verify=ctx, timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0))
        from .brain_pai import BASE, lanes
        prov = OpenAIProvider(base_url=BASE, api_key=key, http_client=http)
        deep = [m for m in lanes()[1] if m != "nemotron-lightning-30b"] or [llm.model()]
        return FallbackModel(*[OpenAIChatModel(m, provider=prov) for m in deep])

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        summary, ok = "", False
        try:
            from pydantic_ai import Agent
            from pydantic_ai.models.openai import OpenAIChatModelSettings
            from pydantic_ai.usage import UsageLimits
            from . import skills
            matched = skills.Router(skills.embed).match(self.goal, timeout=5.0)
            names = {s["name"] for _, s in matched}
            for want in ("desktop-style-guide",):            # always: the house look and rules
                if want not in names:
                    s = next((x for x in skills.load() if x["name"] == want), None)
                    if s:
                        matched.append((0.0, s))
            instructions = RULES + "\n" + skills.instructions_for(matched, builder=True)
            settings = OpenAIChatModelSettings(temperature=0.3, max_tokens=8000,
                                               extra_body={"chat_template_kwargs": {"enable_thinking": False}})
            agent = Agent(self._model(), model_settings=settings, tools=_agent_tools(self),
                          instructions=instructions + skills.catalog_text())
            self.progress("planning the build", 2)
            self.future = self.loop.create_task(agent.run(
                f"Build this for the host: {self.goal}", usage_limits=UsageLimits(request_limit=MAX_REQUESTS)))
            res = self.loop.run_until_complete(self.future)
            summary = str(res.output)[:2000]
            ok = build.state().get("phase") == "trial"
        except asyncio.CancelledError:
            summary = "stopped by Sir"
        except Exception as e:
            summary = f"the build failed: {type(e).__name__}: {str(e)[:300]}"
            st = build.state()
            if st.get("phase") in ("building", "validated"):
                try:
                    build.stop(summary)
                except Exception:
                    pass
        finally:
            st = build.state()
            events.emit("task.progress", task=st.get("slug") or "build", title=st.get("title") or self.goal[:60],
                        step="builder done", note=summary[:120], state="running" if ok else "failed", pct=95 if ok else 100)
            self.on_done(ok, summary, round(time.time() - self.started))
            self.loop.close()
