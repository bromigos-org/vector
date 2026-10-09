"""VECTOR's skills: know-how he loads on demand, from the one shared directory
~/.config/bromigos/skills/ (dotfiles linux/.config/bromigos/skills/).

Installed extensions' skills (bromigos ext: ~/.local/share/bromigos/ext/active/skills)
come after the user's.

A skill is a Markdown file with YAML frontmatter (name, description, when_to_use), either
flat (`<name>.md`, as the live layer's skills are written) or Agent-Skills style
(`<name>/SKILL.md`). Each becomes a deferred Pydantic AI capability: the model sees the
name and description in its catalog and loads the body with `load_capability` when a task
needs it. The catalog is re-read when the directory changes (signature()), so a new or
edited skill is live on the next question.
"""
import os
import re

import yaml

from .. import private
from ..paths import EXT_ACTIVE

# the user's (first wins on a name), then installed extensions' (bromigos ext)
DIRS = [os.path.expanduser("~/.config/bromigos/skills"), os.path.join(EXT_ACTIVE, "skills")]
PACKAGED = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "skills")


def _dirs():
    """The user's skills, then plugins' (plugins.py), then the package's."""
    from . import plugins
    plugins.load()
    return list(dict.fromkeys(DIRS + plugins.SKILL_DIRS + [PACKAGED]))
FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}$")


def _files():
    for d in _dirs():
        if not os.path.isdir(d):
            continue
        for e in sorted(os.listdir(d)):
            p = os.path.join(d, e)
            if e.endswith(".md") and os.path.isfile(p) and e.lower() != "readme.md":
                yield p
            elif os.path.isfile(os.path.join(p, "SKILL.md")):
                yield os.path.join(p, "SKILL.md")


def signature():
    out = []
    for p in _files():
        try:
            st = os.stat(p)
            out.append((p, st.st_mtime_ns, st.st_size))
        except OSError:
            pass
    return tuple(out)


def _frontmatter(text):
    """YAML, or, when a value holds an unquoted colon (strict YAML refuses it), plain
    `key: value` lines, as most skill readers accept."""
    try:
        meta = yaml.safe_load(text)
        if isinstance(meta, dict):
            return meta
    except yaml.YAMLError:
        pass
    meta = {}
    for line in text.splitlines():
        k, sep, v = line.partition(":")
        if sep and re.fullmatch(r"[A-Za-z_][\w-]*", k.strip()):
            meta[k.strip()] = v.strip().strip('"').strip("'")
    return meta


def load():
    """-> [{name, description, when, body, path}] for every well-formed skill."""
    skills, seen = [], set()
    for p in _files():
        try:
            with open(p, errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        m = FRONT.match(text)
        if not m:
            continue
        meta = _frontmatter(m.group(1))
        name = str(meta.get("name") or "").strip()
        desc = " ".join(str(meta.get("description") or "").split())
        if not NAME.match(name) or not desc or name in seen:
            continue
        seen.add(name)
        when = " ".join(str(meta.get("when_to_use") or "").split())
        skills.append({"name": name, "description": desc, "when": when, "body": private.fill(m.group(2).strip()), "path": p,
                       "triggers": str(meta.get("triggers") or "").strip() or None})
    return skills


def capabilities():
    """Deferred capabilities for an Agent (loaded by the model with load_capability)."""
    from pydantic_ai.capabilities import Capability
    caps = []
    for s in load():
        desc = s["description"] + (f" Use when: {s['when']}" if s["when"] else "")
        body = (f"# Skill: {s['name']}\n(source: {s['path']}; files it names are relative to "
                f"{os.path.dirname(s['path'])})\n\n{s['body']}")
        caps.append(Capability(id=s["name"], description=desc[:1500], instructions=body, defer_loading=True))
    return caps


class Router:
    """Attach the skills a question needs, by meaning, and by a skill's own triggers.

    Embeddings (the local model Gnosis uses): each skill is scored half by its whole
    description and half by its best-matching when_to_use phrase (a long list of situations
    then doesn't dilute it), each relative to that skill's own baseline: the 90th percentile
    of its scores over NEUTRAL questions (hub skills resemble everything). Skills at least
    MARGIN above baseline, best first, at most MAX, go into that turn's instructions.
    Measured on 23 labelled questions: 13/15 right skills, 2/8 false positives; the misses
    were short fault reports ("M3 does nothing"), which a skill's `triggers` (a regex in its
    frontmatter) catch: a skill whose triggers match the question is attached first."""
    MARGIN = 0.03
    MAX = 2
    NEUTRAL = ["hey, how are you?", "what's the weather like?", "what did we talk about yesterday?",
               "tell me something interesting", "what time is it?", "thanks, that's all", "good morning, VECTOR",
               "who are you, exactly?", "how's the cluster doing?", "what's my GPU temperature?",
               "change your voice to the scientist", "how is the garden doing today?", "what's in my field notes?",
               "search the web for rust release notes", "show me the gallery", "remember that I like dark roast"]

    def __init__(self, embed):
        self.embed = embed
        self.sig = None
        self.items = []
        self.m = self.whole = self.owner = self.pbase = self.wbase = None

    @staticmethod
    def _texts(s):
        out = []
        when = re.sub(r"^(the host|sir|you)\b[^—:]*?(—|:)\s*", "", s["when"] or "", flags=re.I)
        for ph in re.split(r"\s+—\s+|;\s+|,\s+(?:or\s+)?|\.\s+", when):
            ph = ph.strip(" .")
            if len(ph.split()) >= 2:
                out.append(ph)
        return [f"{s['name'].replace('-', ' ')}: {s['description']}"] + out

    def _refresh(self):
        sig = signature()
        if sig == self.sig:
            return
        import numpy as np
        items = load()
        texts, owner = [], []
        for i, sk in enumerate(items):
            for t in self._texts(sk):
                texts.append(t)
                owner.append(i)
        if items:
            self.owner = np.array(owner)
            self.m = self.embed(texts, timeout=30.0)
            self.whole = self.embed([f"{x['name'].replace('-', ' ')}: {x['description']} Use when: {x['when']}"
                                     for x in items], timeout=20.0)
            neu = self.embed(self.NEUTRAL, timeout=20.0)
            nraw = neu @ self.m.T
            nP = np.stack([[nraw[j, self.owner == i].max() for i in range(len(items))] for j in range(len(self.NEUTRAL))])
            self.pbase = np.percentile(nP, 90, axis=0)
            self.wbase = np.percentile(neu @ self.whole.T, 90, axis=0)
        else:
            self.m = None
        self.items, self.sig = items, sig

    def scores(self, question, timeout=1.0):
        self._refresh()
        if self.m is None:
            return []
        import numpy as np
        q = self.embed([question[:1000]], timeout=timeout)[0]
        raw = self.m @ q
        P = np.array([raw[self.owner == i].max() for i in range(len(self.items))]) - self.pbase
        W = self.whole @ q - self.wbase
        S = 0.5 * W + 0.5 * P
        return sorted(((float(S[i]), self.items[i]) for i in range(len(self.items))), key=lambda x: -x[0])

    def match(self, question, timeout=1.0):
        """-> [(score, skill)] best first; [] on any failure (the turn just goes without)."""
        out = []
        for sk in load():                          # a skill's own triggers first
            t = sk.get("triggers")
            if t:
                try:
                    if re.search(t, question or "", re.I):
                        out.append((1.0, sk))
                except re.error:
                    pass
        try:
            have = {s["name"] for _, s in out}
            out += [x for x in self.scores(question, timeout) if x[0] >= self.MARGIN and x[1]["name"] not in have]
        except Exception as e:
            print(f"skills: router skipped ({type(e).__name__}: {str(e)[:100]})", flush=True)
        return out[:self.MAX]

    def warm(self):
        import threading

        def go():
            try:
                self._refresh()
            except Exception as e:
                print(f"skills: router warm failed ({type(e).__name__}: {str(e)[:100]})", flush=True)
        threading.Thread(target=go, daemon=True, name="skills-warm").start()


def embed(texts, timeout=5.0):
    """Unit embeddings from the model endpoint (llm.py; the model memory uses too)."""
    from .memory import embed as _embed
    return _embed(texts, timeout)


BUILD_SKILLS = {"make-widget", "make-hologram-model", "live-shader-layer", "update-visualization", "make-sound-cue",
                "hologram-build", "live-layer-animation"}
BUILD_DIRECTIVE = """
THIS IS A BUILD TASK (the host wants something made or changed on the desktop)
The host has already asked: don't ask whether to build it and don't look for an existing one first. Call build_start now, with the whole goal in the host's words plus any details he gave (no guessed file names). Then say one short line that your builder has started and END YOUR TURN. Do not call build_status unless the host asks how it's going. Do not read, write or inspect the desktop's files yourself, do not use your terminal for it, and never switch your terminal off: the builder does the work in the background, validates it and puts it up for a trial, and you'll announce it.
"""


MAKE = re.compile(r"\b(make|build|create|add|design|draw|new|change|restyle|redo|update|turn .* into|give me a|"
                  r"put .* on|show .* as|i want a|could you add|replace)\b", re.I)


def instructions_for(matched, builder=False, question=""):
    """The matched skills' bodies. For VECTOR's own voice (builder=False), a build skill
    becomes one directive instead: the build belongs to the builder (build_start)."""
    if not matched:
        return ""
    if not builder and any(s["name"] in BUILD_SKILLS for _, s in matched) and MAKE.search(question or ""):
        rest = [(sc, s) for sc, s in matched if s["name"] not in BUILD_SKILLS]
        return BUILD_DIRECTIVE + (instructions_for(rest, builder=True) if rest else "")
    parts = [f"\nSKILLS FOR THIS TASK (loaded for you; follow them)\n"]
    for _, s in matched:
        parts.append(f"# Skill: {s['name']} (source: {s['path']})\n\n{s['body']}\n")
    return "\n".join(parts)


def catalog_text():
    """One line per skill, inside VECTOR's single system prompt (a second system message,
    as Pydantic AI's deferred capabilities send it, made hive stop calling tools)."""
    items = load()
    if not items:
        return ""
    lines = [f"- {s['name']}: {s['description'][:150]}" for s in items]
    return ("\nYOUR SKILLS (know-how; call load_skill with a name before a task it covers, unless it's already "
            "attached below):\n" + "\n".join(lines) + "\n")


def load_skill(name):
    """A skill's full text, as a tool result."""
    for s in load():
        if s["name"] == (name or "").strip():
            return {"skill": s["name"], "source": s["path"], "text": s["body"][:20000]}
    raise ValueError(f"no skill {name!r}; skills: {[s['name'] for s in load()]}")
