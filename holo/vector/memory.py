"""VECTOR's long-term memory: one interface, a local store by default.

  recall(query, k, budget) -> (lines, stats)   the hot path, never longer than `budget` s
  remember(text, kind)     -> {"ok", ...}       returns at once
  forget(what)             -> {"ok", ...}       "" or "that": the last thing filed
  summarize(turns)                              end of a conversation: keep its durable facts
  warm(), refresh(), last_written

Memory() picks the store: vector.toml [memory] backend = "auto" (default) | "local" |
a plugin's name. auto takes the first store a plugin registered (the operator's Gnosis,
docs/plugins.md), else LocalMemory: SQLite in ~/.local/share/bromigos/vector-memory.sqlite
with embeddings from the model endpoint (llm.py; recall by cosine), falling back to
full-text search when the endpoint can't embed in time.

Every remember, forget, recall and search is written to the audit log with the text
hashed and truncated, never in full. Protected safety code (guard.py).
"""
import hashlib
import json
import os
import sqlite3
import ssl
import threading
import time
import urllib.request

import numpy as np

from . import llm

AUDIT = os.path.expanduser("~/.local/state/bromigos/vector-audit.log")
DB = os.path.expanduser("~/.local/share/bromigos/vector-memory.sqlite")
CA = os.path.expanduser("~/.config/homelab/homelab-ca.crt")
RECALL_BUDGET = 0.2
MIN_SCORE = 0.45


def digest(text):
    t = (text or "").strip()
    return {"sha1": hashlib.sha1(t.encode()).hexdigest()[:12], "head": t[:40], "chars": len(t)}


def audit(what, **kw):
    os.makedirs(os.path.dirname(AUDIT), exist_ok=True)
    with open(AUDIT, "a") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "tool": f"memory.{what}", **kw}) + "\n")


def ssl_ctx():
    return ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()


def embed(texts, timeout=5.0, ctx=None):
    """Unit embeddings for texts from the model endpoint (llm.py)."""
    req = urllib.request.Request(llm.url("/embeddings"),
                                 data=json.dumps({"model": llm.embed_model(), "input": texts}).encode(),
                                 headers={"Authorization": "Bearer " + llm.key(), "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx or ssl_ctx()) as r:
        data = json.load(r)["data"]
    m = np.array([d["embedding"] for d in sorted(data, key=lambda d: d["index"])], np.float32)
    return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-9)


def _settings():
    try:
        import tomllib

        from ..paths import conf
        with open(conf("vector.toml"), "rb") as f:
            return tomllib.load(f).get("memory", {}) or {}
    except (OSError, ValueError):
        return {}


def Memory():  # noqa: N802  (called like the class it used to be)
    """The store in force (see above)."""
    from . import plugins
    plugins.load()
    want = str(_settings().get("backend", "auto"))
    if want == "auto":
        want = next(iter(plugins.MEMORY), "local")
    if want != "local" and want in plugins.MEMORY:
        try:
            return plugins.MEMORY[want]()
        except Exception as e:
            audit("backend", ok=False, backend=want, err=f"{type(e).__name__}: {str(e)[:100]}")
    return LocalMemory()


class LocalMemory:
    """SQLite on this machine: notes, their embeddings, and a full-text index."""

    def __init__(self, path=DB):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        self.db = sqlite3.connect(path, check_same_thread=False)
        if new:
            os.chmod(path, 0o600)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, content TEXT NOT NULL, kind TEXT,
                                              created REAL, emb BLOB);
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(content, content='notes', content_rowid='id');
        """)
        self.lock = threading.Lock()
        self.ctx = ssl_ctx()
        self.last_written = None
        self.backend = "local"

    def embed(self, texts, timeout=5.0):
        return embed(texts, timeout, self.ctx)

    # ------------------------------------------------------------------ plumbing
    def _rows(self):
        with self.lock:
            return self.db.execute("SELECT id, content, kind, emb FROM notes").fetchall()

    def _add(self, text, kind, emb):
        with self.lock:
            cur = self.db.execute("INSERT INTO notes (content, kind, created, emb) VALUES (?, ?, ?, ?)",
                                  (text, kind, time.time(), emb.astype(np.float32).tobytes() if emb is not None else None))
            self.db.execute("INSERT INTO notes_fts (rowid, content) VALUES (?, ?)", (cur.lastrowid, text))
            self.db.commit()
            return cur.lastrowid

    def _delete(self, nid):
        with self.lock:
            row = self.db.execute("SELECT content FROM notes WHERE id = ?", (nid,)).fetchone()
            if row:
                self.db.execute("INSERT INTO notes_fts (notes_fts, rowid, content) VALUES ('delete', ?, ?)", (nid, row[0]))
                self.db.execute("DELETE FROM notes WHERE id = ?", (nid,))
                self.db.commit()
            return row[0] if row else None

    def _backfill(self):
        """Embed notes filed while the endpoint couldn't (in the background)."""
        with self.lock:
            todo = self.db.execute("SELECT id, content FROM notes WHERE emb IS NULL LIMIT 64").fetchall()
        if not todo:
            return
        try:
            embs = self.embed([c for _, c in todo], timeout=15)
        except Exception:
            return
        with self.lock:
            for (nid, _), e in zip(todo, embs):
                self.db.execute("UPDATE notes SET emb = ? WHERE id = ?", (e.astype(np.float32).tobytes(), nid))
            self.db.commit()

    def _fts(self, query, k):
        words = [w for w in "".join(c if c.isalnum() else " " for c in query.lower()).split() if len(w) > 2][:12]
        if not words:
            return []
        q = " OR ".join(f'"{w}"' for w in words)
        with self.lock:
            return self.db.execute("SELECT rowid, content FROM notes_fts WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?",
                                   (q, k)).fetchall()

    # ------------------------------------------------------------------ recall (hot path)
    def recall(self, query, k=5, budget=RECALL_BUDGET):
        t0 = time.monotonic()
        lines, ids, src, err = [], [], "none", None
        rows = [r for r in self._rows() if r[3]]
        if rows:
            try:
                q = self.embed([query[:500]], timeout=budget)[0]
                m = np.stack([np.frombuffer(r[3], np.float32) for r in rows])
                s = m @ q
                order = np.argsort(-s)[:k]
                hits = [i for i in order if s[i] >= MIN_SCORE]
                lines, ids, src = [rows[i][1] for i in hits], [rows[i][0] for i in hits], "local"
            except Exception as e:
                err = str(e)[:80]
        if src == "none" and time.monotonic() - t0 < budget:
            try:
                got = self._fts(query, k)
                lines, ids, src = [c for _, c in got], [i for i, _ in got], "text"
            except Exception as e:
                err = err or str(e)[:80]
        stats = {"recall_ms": int((time.monotonic() - t0) * 1000), "recall_source": src, "recalled": len(lines)}
        if src == "none":
            stats["recall_skipped"] = err or "nothing filed"
        audit("recall", query=digest(query), source=src, hits=len(lines), ms=stats["recall_ms"])
        from . import events
        events.emit("memory.recall", space="vector", ids=[str(i) for i in ids], n=len(lines), ms=stats["recall_ms"],
                    source=src)
        threading.Thread(target=self._backfill, daemon=True).start()
        return lines, stats

    # ------------------------------------------------------------------ write / forget
    def remember(self, text, kind="note"):
        text = " ".join(text.split())[:400]
        if len(text) < 4:
            raise ValueError("nothing to remember")
        try:
            emb = self.embed([text], timeout=1.0)[0]
        except Exception:
            emb = None                           # filed now, embedded later (_backfill)
        nid = self._add(text, kind, emb)
        self.last_written = [nid]
        audit("remember", content=digest(text), kind=kind, ids=[nid])
        from . import events
        events.emit("memory.file", space="vector", category=kind)
        return {"ok": True, "filed_under": kind}

    def forget(self, what=""):
        target = None
        if (not what or what.strip().lower() in ("that", "it", "the last thing", "last")) and self.last_written:
            target = self.last_written[0]
        elif what:
            rows = [r for r in self._rows() if r[3]]
            try:
                q = self.embed([what])[0]
                s = np.stack([np.frombuffer(r[3], np.float32) for r in rows]) @ q if rows else np.array([])
                i = int(np.argmax(s)) if len(s) else -1
                if i >= 0 and s[i] >= 0.5:
                    target = rows[i][0]
            except Exception:
                got = self._fts(what, 1)
                target = got[0][0] if got else None
        content = self._delete(target) if target is not None else None
        if content is None:
            audit("forget", query=digest(what), found=False)
            return {"ok": False, "detail": "nothing in my own memory matches that"}
        if self.last_written and target in self.last_written:
            self.last_written = None
        audit("forget", query=digest(what), id=target, content=digest(content))
        from . import events
        events.emit("memory.forget", space="vector", n=1, ids=[str(target)])
        return {"ok": True, "forgot": content[:120]}

    def warm(self):
        def go():
            try:
                self.embed(["warm"], timeout=2.0)
            except Exception:
                pass
            self._backfill()
        threading.Thread(target=go, daemon=True).start()

    def refresh(self):
        self._backfill()

    def summarize(self, turns):
        """End of a conversation: the model picks up to three durable facts about the host
        (preferences, plans, people, his setup) and they're filed as notes. Background, small."""
        msgs = [m for m in turns[-12:] if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
                and m["content"].strip()]
        if len([m for m in msgs if m["role"] == "user"]) < 2:
            return None
        talk = "\n".join(f"{m['role']}: {m['content'][:500]}" for m in msgs)
        body = {"model": llm.model(), "max_tokens": 200, "temperature": 0.1,
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "system", "content": "From this conversation, list up to three durable facts worth "
                              "remembering about the user (preferences, plans, people, their setup), one short sentence "
                              "each, as a JSON array of strings. [] when there are none. Never secrets or credentials."},
                             {"role": "user", "content": talk}]}
        try:
            req = urllib.request.Request(llm.url("/chat/completions"), data=json.dumps(body).encode(),
                                         headers={"Authorization": "Bearer " + llm.key(), "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60, context=self.ctx) as r:
                out = json.load(r)["choices"][0]["message"]["content"] or ""
            facts = json.loads(out[out.find("["):out.rfind("]") + 1] or "[]")
            for f in [x for x in facts if isinstance(x, str)][:3]:
                self.remember(f, kind="conversation")
            audit("summary", turns=len(msgs), facts=len(facts))
            return True
        except Exception as e:
            audit("summary", ok=False, err=str(e)[:120])
            return False
