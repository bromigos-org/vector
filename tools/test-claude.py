#!/usr/bin/env python3
"""VECTOR's Claude Code tools (holo/vector/claude.py), the redactor (redact.py) and the
speech converter (text.read_aloud), on synthetic transcripts in a throwaway folder.

    python3 tools/test-claude.py

Covers: every record type, paging forwards and back through a large file, the latest view
of a live session (what it's doing, its jobs and tasks), choosing between live sessions
(the focused herdr pane, a hint, a toss-up that asks), waiting on the operator, search,
memory, logs, follow mode (only finished replies, once), the redactor against every token
shape it knows, the speech converter, what the phone may call, and that nothing writes:
no file under the fixtures changes and no file is opened for writing. Every secret here
is made up. Exit 1 on any failure.
"""
import ast
import builtins
import hashlib
import io
import json
import os
import random
import re
import string
import subprocess
import sys
import tempfile
import time

REAL_OPEN = builtins.open
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

TMP = tempfile.mkdtemp(prefix="vector-claude-test-")
CH = os.path.join(TMP, "claude")
TASKS = os.path.join(TMP, "tasks")
BIN = os.path.join(TMP, "bin")
for d in (CH, TASKS, BIN):
    os.makedirs(d)
os.environ.update(VECTOR_CLAUDE_HOME=CH, VECTOR_CLAUDE_TASKS=TASKS, VECTOR_HERDR_BIN=os.path.join(BIN, "herdr"),
                  PATH=BIN + os.pathsep + os.environ.get("PATH", ""))

try:                                   # CI: no bromigos-core, so no private overlay module; an empty one
    import bromigos_private  # noqa: F401
except ImportError:
    with open(os.path.join(TMP, "bromigos_private.py"), "w") as f:
        f.write("def data(): return {}\ndef present(): return False\ndef get(key, default=None): return default\n"
                "def url(name, path='', default=''): return default\ndef vault(name, default=''): return default\n"
                "def vault_root(default=''): return default\ndef lan_domain(default=''): return default\n"
                "def path(name, default=''): return default\nPATH = ''\n")
    sys.path.insert(0, TMP)

BAD = [0]


def ok(cond, what, detail=""):
    print(f"{'pass' if cond else 'FAIL'}  {what}" + (f"  ({detail})" if detail and not cond else ""))
    BAD[0] += not cond


def rnd(n, alphabet=string.ascii_letters + string.digits, seed=None):
    r = random.Random(seed if seed is not None else n)
    return "".join(r.choice(alphabet) for _ in range(n))


# ================================================================== the made-up secrets
GH = "ghp_" + rnd(36, seed=1)
GH_PAT = "github_pat_" + rnd(60, string.ascii_letters + string.digits + "_", seed=2)
VAULT = "hvs." + rnd(90, seed=3)
VAULT_OLD = "s." + "Ab3" + rnd(21, seed=4)
AWS = "AKIA" + rnd(16, string.ascii_uppercase + string.digits, seed=5)
AWS_SECRET = rnd(40, string.ascii_letters + string.digits + "/+", seed=6)
SK = "sk-" + rnd(48, seed=7)
SK_ANT = "sk-ant-api03-" + rnd(80, string.ascii_letters + string.digits + "-_", seed=8)
SK_PROJ = "sk-proj-" + rnd(60, seed=9)
NOL = "nol_" + rnd(32, seed=10)
SLACK = "xoxb-" + "1234567890-" + rnd(24, seed=11)
JWT = "eyJ" + rnd(30, seed=12) + ".eyJ" + rnd(60, seed=13) + "." + rnd(43, seed=14)
AGE = "AGE-SECRET-KEY-1" + rnd(58, string.ascii_uppercase + string.digits, seed=15)
GOOGLE = "AIza" + rnd(35, string.ascii_letters + string.digits + "_-", seed=16)
HF = "hf_" + rnd(34, seed=17)
GLPAT = "glpat-" + rnd(20, seed=18)
NPM = "npm_" + rnd(36, seed=19)
STRIPE = "sk_live_" + rnd(24, seed=20)
TELEGRAM = "1234567890:AA" + rnd(33, string.ascii_letters + string.digits + "_-", seed=21)
BEARER = rnd(40, string.ascii_letters + string.digits + "._-", seed=22)
APIKEY_HDR = rnd(32, "0123456789abcdef", seed=23)
PASSWORD = "Tr0ub4dor&3-" + rnd(6, seed=24)
DB_PASS = "pg" + rnd(14, seed=25)
HEX_TOKEN = rnd(64, "0123456789abcdef", seed=26)
ENV_SECRET = rnd(30, seed=27)
VAULT_KV = rnd(20, seed=28)
COOKIE = rnd(40, seed=29)
CLIENT_SECRET = "GOCSPX-" + rnd(28, seed=30)
HIGH_ENTROPY = rnd(44, string.ascii_letters + string.digits + "+/", seed=31)
YA29 = "ya29." + rnd(60, string.ascii_letters + string.digits + "_-", seed=32)
BEGIN = "-----BEGIN"                  # split, so this file never holds a key block's header line itself
PRIVKEY = (BEGIN + " OPENSSH PRIVATE KEY-----\n" + "\n".join(rnd(70, seed=40 + i) for i in range(5)) +
           "\n-----END OPENSSH PRIVATE KEY-----")
RSA_CUT = BEGIN + " RSA PRIVATE KEY-----\n" + rnd(64, seed=50) + "\n" + rnd(64, seed=51)   # no END: cut off

SECRETS = [GH, GH_PAT, VAULT, VAULT_OLD, AWS, AWS_SECRET, SK, SK_ANT, SK_PROJ, NOL, SLACK, JWT, AGE, GOOGLE, HF, GLPAT,
           NPM, STRIPE, TELEGRAM, BEARER, APIKEY_HDR, PASSWORD, DB_PASS, HEX_TOKEN, ENV_SECRET, VAULT_KV, COOKIE,
           CLIENT_SECRET, HIGH_ENTROPY, YA29]
SECRET_TEXT = [
    f"export GITHUB_TOKEN={GH}",
    f"pushing with {GH_PAT} now",
    f"VAULT_TOKEN={VAULT}",
    f"the old root token was {VAULT_OLD}",
    f"aws_access_key_id = {AWS}\naws_secret_access_key = {AWS_SECRET}",
    f"OPENAI_API_KEY={SK}", f"key {SK_ANT} in the env", f"OPENAI={SK_PROJ}",
    f"nolgia login {NOL}", f"slack {SLACK}", f"cookie jwt {JWT}", f"age key: {AGE}",
    f"maps key {GOOGLE}", f"HF_TOKEN={HF}", f"gitlab {GLPAT}", f"//registry.npmjs.org/:_authToken={NPM}",
    f"stripe {STRIPE}", f"bot {TELEGRAM}",
    f'curl -H "Authorization: Bearer {BEARER}" https://api.example.org/v1',
    f"X-Api-Key: {APIKEY_HDR}",
    f"password={PASSWORD}",
    f"postgres://app:{DB_PASS}@db.lan:5432/app",
    f"{HEX_TOKEN}",
    f"DB_SECRET='{ENV_SECRET}'",
    f"====== Data ======\nKey         Value\n---         -----\nseerr       {VAULT_KV}\n",
    f"Cookie: session={COOKIE}",
    f'{{"client_secret": "{CLIENT_SECRET}", "client_id": "123.apps.example"}}',
    f"signature_blob: {HIGH_ENTROPY}",
    f"Authorization: {YA29}",
    PRIVKEY, RSA_CUT,
]

# ================================================================== the redactor
from holo.vector import redact as R  # noqa: E402

for s in SECRET_TEXT:
    out = R.redact(s)
    left = [x for x in SECRETS if x in out]
    ok(not left and not R.leaks(out) and "PRIVATE KEY-----\n" not in out,
       f"redactor: {s.splitlines()[0][:40]!r}", f"left: {[x[:12] for x in left]} {R.leaks(out)}")
blob = "\n".join(SECRET_TEXT)
out = R.redact(blob)
ok(not [x for x in SECRETS if x in out] and not R.leaks(out), "redactor: every shape at once")
ok(all("[redacted: " in R.redact(s) for s in SECRET_TEXT), "redactor: each says [redacted: <kind>]")
# the privacy guard's own token shapes, generated from its list: none passes
import privacy_guard  # noqa: E402
samples = {"GitHub token": "gho_" + rnd(36, seed=60), "Vault token": "hvb." + rnd(40, seed=61),
           "AWS key id": "ASIA" + rnd(16, string.ascii_uppercase + string.digits, seed=62),
           "API key": "rk-" + rnd(30, seed=63), "nolgia token": "nol_" + rnd(20, seed=64),
           "Slack token": "xoxp-" + rnd(30, seed=65), "JWT": JWT, "private key block": PRIVKEY,
           "age secret key": AGE}
for label, p in privacy_guard.PUBLIC:
    if label in samples:
        out = R.redact(f"value: {samples[label]} and more")
        ok(not re.search(p, out), f"redactor: never passes the privacy guard's {label}")
ok(set(samples) <= {lbl for lbl, _ in privacy_guard.PUBLIC}, "redactor: the guard still has every shape tested")
# random fuzz: secrets embedded in JSON, prose and tables never survive
r = random.Random(7)
SELF_ID = [GH, GH_PAT, VAULT, VAULT_OLD, AWS, SK, SK_ANT, SK_PROJ, NOL, SLACK, JWT, AGE, GOOGLE, HF, GLPAT, NPM, STRIPE,
           TELEGRAM, YA29, COOKIE, HIGH_ENTROPY, BEARER]       # a shape, or 32+ mixed characters: caught anywhere
for i in range(300):
    keyed = r.random() < 0.5
    sec = r.choice(SECRETS if keyed else SELF_ID)
    tmpl = r.choice(["token: {s}", '"api_key": "{s}"', "PASSWORD={s}", "Bearer {s}", "secret={s}&x=1", "--token {s}",
                     "client_secret: '{s}'"] if keyed else ["{s}", "| x | {s} |", "ok {s} ok", "(see {s})"])
    out = R.redact(tmpl.format(s=sec))
    if sec in out and len(sec) >= 20:
        ok(False, f"redactor fuzz {i}: {tmpl}", sec[:10])
        break
else:
    ok(True, "redactor: 300 fuzzed placements")
# what stays: host names, LAN addresses, ids and hashes, ordinary prose
KEEP = ["relay.lan.example.org at 192.0.2.10 and 198.51.100.5", "session 83a008a0-6089-43e6-b399-65c1080219dc",
        "commit 1234567890abcdef1234567890abcdef12345678", "max_tokens=1500 and total_tokens: 4000",
        "the token configuration is fine", "secretName: vector-mcp", "vault kv get -field=github_token_bromigodev x",
        "the password field is required", "configSeedVersion: 2026-10-10-media", "token_file: ~/.local/share/x-token",
        "GH_TOKEN=$GH_TOKEN_BROMIGODEV", "password: <your password>", "api_key: ${API_KEY}",
        "see https://github.com/bromigos-org/vector/pull/12"]
for s in KEEP:
    ok(R.redact(s) == s, f"redactor keeps: {s[:44]!r}", R.redact(s))
ok(R.deep({"a": [f"x {GH}"], GH: 1}) == {"a": ["x [redacted: GitHub token]"], "[redacted: GitHub token]": 1},
   "redactor: deep() reaches lists, values and keys")

# ================================================================== the speech converter
from holo.vector.text import read_aloud  # noqa: E402

REPLY = """## What changed

The **den TV** now follows the *screens* table:

| Screen | Shows |
|---|---|
| Big monitor | Shows |
| Scope | YouTube |

- Pushed `bromigOS` to main ✅
- CI is green → deployed 🚀

```bash
bromigos-holo restart
```

```python
def x():
    return 1
```

Run this:

```bash
cd ~/github.com/bromigos-org/vector
python3 tools/test-claude.py --all --verbose --no-cache --again
```

See [the PR](https://github.com/bromigos-org/vector/pull/12) and https://www.example.com/docs?x=1, file `/home/someone/github.com/x/holo/app.py`.
Token: GH_TOKEN_PLACEHOLDER
"""
spoken = read_aloud(REPLY.replace("GH_TOKEN_PLACEHOLDER", GH))
ok("What changed." in spoken and "##" not in spoken and "**" not in spoken, "speech: a heading is a sentence, no markup")
ok("Big monitor: Shows. Scope: YouTube." in spoken, "speech: a short table row by row", spoken)
ok("Pushed bromigOS to main." in spoken and "CI is green to deployed." in spoken, "speech: list items as sentences", spoken)
ok("The command is: bromigos-holo restart." in spoken, "speech: a short one-liner is spelled out", spoken)
ok("Some code is shown on screen." in spoken and "def x" not in spoken, "speech: code is 'shown on screen'")
ok("Some commands to run are shown on screen." in spoken, "speech: a longer command block is 'shown on screen'", spoken)
ok("the PR" in spoken and "github.com/bromigos-org" not in spoken and "example.com" in spoken and "?x=1" not in spoken,
   "speech: links are their words, URLs their site", spoken)
ok("a file path" in spoken and "/home/someone" not in spoken, "speech: file paths are 'a file path'")
ok(not re.search("[←-⇿\U0001F300-\U0001FAFF✅]", spoken), "speech: emoji and arrows are dropped")
ok(GH not in spoken and "something redacted" in spoken, "speech: redaction runs first")
big = "| Name | Size |\n|---|---|\n" + "\n".join(f"| item{i} | {i} GB |" for i in range(12))
ok(read_aloud(big).startswith("A table of 12 rows, by Name, Size. The first ones are item0, item1 and item2."),
   "speech: a long table is summarised", read_aloud(big))
three = "| Host | CPU | RAM |\n|---|---|---|\n| kestrel | 12% | 90% |\n| tern | 3% | 40% |"
ok(read_aloud(three) == "kestrel: CPU 12%, RAM 90%. tern: CPU 3%, RAM 40%.", "speech: a wider table names its columns",
   read_aloud(three))
ok(read_aloud("1. First thing\n2. Second thing") == "First thing. Second thing.", "speech: a numbered list",
   read_aloud("1. First thing\n2. Second thing"))

# ================================================================== the fixtures
P1 = os.path.join(CH, "projects", "-home-op-github-com-bromigos-org")
P2 = os.path.join(CH, "projects", "-home-op-github-com-atlas")
S1 = "11111111-1111-4111-8111-111111111111"
S2 = "22222222-2222-4222-8222-222222222222"
S3 = "33333333-3333-4333-8333-333333333333"          # big and old
JOB = "a0123456789abcdef"
for d in (P1, P2):
    os.makedirs(os.path.join(d, "memory"))


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


NOW = time.time()
uid = [0]


def rec(kind, t=None, **kw):
    uid[0] += 1
    base = {"type": kind, "uuid": f"u{uid[0]}", "sessionId": kw.pop("sid", S1), "cwd": kw.pop("cwd", "/home/op/github.com/bromigos-org"),
            "timestamp": iso(t or NOW - 600 + uid[0])}
    base.update(kw)
    return base


def user(text, **kw):
    return rec("user", message={"role": "user", "content": text}, **kw)


def assistant(blocks, stop="end_turn", mid=None, **kw):
    return rec("assistant", message={"id": mid or f"msg{uid[0]}", "role": "assistant", "content": blocks,
                                     "stop_reason": stop}, **kw)


def write(path, recs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        for r_ in recs:
            f.write(json.dumps(r_) + "\n")


def append(path, recs, partial=None):
    with REAL_OPEN(path, "a") as f:
        for r_ in recs:
            f.write(json.dumps(r_) + "\n")
        if partial:
            f.write(partial)


S1_RECS = [
    {"type": "permission-mode", "permissionMode": "default", "sessionId": S1},
    rec("file-history-snapshot", messageId="m0", snapshot={}),
    user("<command-name>/clear</command-name>\n<command-message>clear</command-message>\n<command-args></command-args>"),
    user("Caveat: The messages below were generated by the user while running local commands. DO NOT respond."),
    user("make the den TV follow the screens table"),
    rec("ai-title", aiTitle="Den TV follows screens table"),
    {"type": "last-prompt", "lastPrompt": "make the den TV follow the screens table", "sessionId": S1},
    user("<system-reminder>internal note</system-reminder>", isMeta=True),
    assistant([{"type": "thinking", "thinking": "secret thoughts", "signature": "x"}], stop="tool_use", mid="mA"),
    assistant([{"type": "text", "text": "Looking at the screens config."}], stop="tool_use", mid="mA"),
    assistant([{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": f"export GH_TOKEN={GH} && gh pr list\nsecond line", "description": "list PRs"}}], stop="tool_use", mid="mA"),
    user([{"type": "tool_result", "tool_use_id": "tu1", "content": f"line1 {VAULT}\nline2\nline3\nline4\nline5\nline6\nline7\nline8\nline9\nline10"}]),
    assistant([{"type": "tool_use", "id": "tu2", "name": "Read", "input": {"file_path": "/home/op/x/widgets/den.py"}}], stop="tool_use", mid="mB"),
    user([{"type": "tool_result", "tool_use_id": "tu2", "content": [{"type": "text", "text": "def den(): pass"}], "is_error": False}]),
    user([{"type": "text", "text": "also the scope"}, {"type": "image", "source": {"type": "base64", "data": "AAAA"}}]),
    rec("attachment", attachment={"type": "queued_command", "prompt": "and keep YouTube on the scope", "commandMode": "prompt"}),
    rec("attachment", attachment={"type": "total_tokens_reminder", "n": 5}),
    rec("queue-operation", operation="enqueue", content="x"),
    rec("system", subtype="away_summary", content="Recap: working on the den TV."),
    user("<task-notification>\n<task-id>x</task-id>\n<summary>Agent \"check widgets\" completed</summary>\n</task-notification>"),
    user("This session is being continued from a previous conversation. Summary: den TV work.", isCompactSummary=True),
    {"type": "summary", "summary": "Den TV screens", "leafUuid": "u1"},
    assistant([{"type": "text", "text": "Done: the **den TV** follows the screens table.\n\n| Screen | Shows |\n|---|---|\n| Big monitor | Shows |"}], mid="mC"),
    rec("system", subtype="turn_duration", durationMs=1000),
    rec("mystery-new-record", foo="bar"),
]
F1 = os.path.join(P1, S1 + ".jsonl")
write(F1, S1_RECS)
write(os.path.join(P1, S1, "subagents", f"agent-{JOB}.jsonl"), [
    user("check the widgets", sid=S1, isSidechain=True),
    assistant([{"type": "text", "text": "Widgets look fine."}], sid=S1, isSidechain=True)])
with open(os.path.join(P1, S1, "subagents", f"agent-{JOB}.meta.json"), "w") as f:
    json.dump({"agentType": "Explore", "description": "check widgets", "toolUseId": "tu9"}, f)
write(os.path.join(P1, S1, "subagents", "agent-a1111111111111111.jsonl"), [
    user("long job", sid=S1, isSidechain=True), assistant([{"type": "tool_use", "id": "j", "name": "Bash", "input": {"command": "sleep 1"}}], stop="tool_use", sid=S1)])
with open(os.path.join(P1, S1, "subagents", "agent-a1111111111111111.meta.json"), "w") as f:
    json.dump({"agentType": "general-purpose", "description": "build the thing"}, f)

F2 = os.path.join(P2, S2 + ".jsonl")
write(F2, [user("research the Atlas momentum agents", sid=S2, cwd="/home/op/github.com/atlas"),
           rec("ai-title", aiTitle="Atlas momentum research", sid=S2, cwd="/home/op/github.com/atlas"),
           assistant([{"type": "text", "text": "Started the backtest."}], stop="tool_use", sid=S2, cwd="/home/op/github.com/atlas"),
           assistant([{"type": "tool_use", "id": "b1", "name": "Bash", "input": {"command": "python backtest.py"}}], stop="tool_use", sid=S2, cwd="/home/op/github.com/atlas")])

# a big, old session: 6000 turns, for paging and the tail
F3 = os.path.join(P1, S3 + ".jsonl")
with open(F3, "w") as f:
    for i in range(3000):
        f.write(json.dumps(user(f"question number {i}", t=NOW - 86400 * 3 + i)) + "\n")
        f.write(json.dumps(assistant([{"type": "text", "text": f"answer number {i} " + "x" * 200}],
                                     t=NOW - 86400 * 3 + i, mid=f"big{i}")) + "\n")
old = NOW - 86400 * 3
os.utime(F3, (old, old))
# a session from two months ago, out of search's default range
S4 = "44444444-4444-4444-8444-444444444444"
F4 = os.path.join(P2, S4 + ".jsonl")
write(F4, [user("the den TV long ago", sid=S4)])
os.utime(F4, (NOW - 86400 * 60, NOW - 86400 * 60))

with open(os.path.join(P1, "memory", "MEMORY.md"), "w") as f:
    f.write(f"# Memory index\n\n- [Screens](screens.md) — the den TV follows the screens table\n- key {GH}\n")
with open(os.path.join(P1, "memory", "screens.md"), "w") as f:
    f.write("The den TV shows what the screens table says.\n")
with open(os.path.join(CH, "history.jsonl"), "w") as f:
    for i in range(5):
        f.write(json.dumps({"display": f"prompt {i}" + (f" {SK}" if i == 4 else ""), "pastedContents": {"1": {"content": "pasted secret"}} if i == 3 else {},
                            "timestamp": int((NOW - 100 + i) * 1000), "project": "/home/op/github.com/bromigos-org", "sessionId": S1}) + "\n")
os.makedirs(os.path.join(CH, "debug"))
with open(os.path.join(CH, "debug", S1 + ".txt"), "w") as f:
    f.write(f"[DEBUG] start\n[DEBUG] auth header Authorization: Bearer {BEARER}\n[DEBUG] done\n")
# what must never be opened
os.makedirs(os.path.join(CH, "sessions"))
os.makedirs(os.path.join(CH, "session-env", S1))
FORBIDDEN = {
    os.path.join(CH, ".credentials.json"): json.dumps({"claudeAiOauth": {"accessToken": "sk-ant-oat01-" + rnd(60, seed=90)}}),
    os.path.join(CH, "sessions", "4242.abcdef.key"): rnd(64, seed=91),
    os.path.join(CH, "session-env", S1, "env"): "SECRET=" + rnd(30, seed=92),
    os.path.join(CH, "settings.json"): json.dumps({"env": {"TOKEN": rnd(30, seed=93)}}),
    os.path.join(CH, "projects", "-home-op-github-com-bromigos-org", S1, "tool-results", "x.txt"): "raw output",
}
for p, body in FORBIDDEN.items():
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(body)
# a running "claude" process in Claude Code's registry (argv0 claude), for S1
proc = subprocess.Popen(["bash", "-c", "exec -a claude sleep 120"])
time.sleep(0.2)
with open(os.path.join(CH, "sessions", f"{proc.pid}.json"), "w") as f:
    json.dump({"pid": proc.pid, "sessionId": S1, "cwd": "/home/op/github.com/bromigos-org", "status": "idle",
               "kind": "interactive"}, f)
# background task output for S1
TD = os.path.join(TASKS, os.path.basename(P1), S1, "tasks")
os.makedirs(TD)
with open(os.path.join(TD, "bk1.output"), "w") as f:
    f.write(f"building...\nstep 2 of 3 token={ENV_SECRET}\n")
os.symlink(os.path.join(P1, S1, "subagents", f"agent-{JOB}.jsonl"), os.path.join(TD, f"{JOB}.output"))

# herdr: S1 in the left pane (focused), S2 on the right
HERDR_SNAPSHOT = {"result": {"snapshot": {
    "focused_pane_id": "w1:p1",
    "workspaces": [{"workspace_id": "w1", "label": "bromigos-org"}],
    "tabs": [{"tab_id": "w1:t1", "label": "1", "workspace_id": "w1"}],
    "layouts": [{"tab_id": "w1:t1", "area": {"x": 0, "y": 0, "width": 200, "height": 50},
                 "panes": [{"pane_id": "w1:p1", "rect": {"x": 0, "y": 0, "width": 100, "height": 50}},
                           {"pane_id": "w1:p2", "rect": {"x": 100, "y": 0, "width": 100, "height": 50}}]}],
    "agents": [
        {"agent": "claude", "agent_status": "idle", "pane_id": "w1:p1", "tab_id": "w1:t1", "workspace_id": "w1",
         "agent_session": {"agent": "claude", "kind": "id", "source": "herdr:claude", "value": S1},
         "terminal_title_stripped": "✳ Den TV"},
        {"agent": "claude", "agent_status": "working", "pane_id": "w1:p2", "tab_id": "w1:t1", "workspace_id": "w1",
         "agent_session": {"agent": "claude", "kind": "id", "source": "herdr:claude", "value": S2},
         "terminal_title_stripped": "ATLAS"}]}}}
HERDR_CALLS = os.path.join(TMP, "herdr-calls")
with open(os.path.join(BIN, "herdr"), "w") as f:
    f.write(f"#!/bin/sh\necho \"$@\" >> {HERDR_CALLS}\ncat {os.path.join(TMP, 'herdr.json')}\n")
with open(os.path.join(TMP, "herdr.json"), "w") as f:
    json.dump(HERDR_SNAPSHOT, f)
with open(os.path.join(BIN, "hyprctl"), "w") as f:
    f.write("#!/bin/sh\necho '{}'\n")
for b in ("herdr", "hyprctl"):
    os.chmod(os.path.join(BIN, b), 0o755)


def tree_state():
    out = {}
    for base in (CH, TASKS):
        for d, _, files in os.walk(base):
            for fn in files:
                p = os.path.join(d, fn)
                if os.path.islink(p):
                    continue
                st = os.stat(p)
                with open(p, "rb") as f:
                    out[p] = (st.st_size, st.st_mtime_ns, hashlib.sha1(f.read()).hexdigest())
    return out


# ================================================================== nothing writes, nothing forbidden is opened
from holo.vector import claude as C  # noqa: E402

OPENED = []
_real_open = builtins.open
_real_io_open = io.open


def spy_open(file, mode="r", *a, **kw):
    OPENED.append((os.path.realpath(file) if isinstance(file, (str, bytes, os.PathLike)) else file, mode))
    return _real_open(file, mode, *a, **kw)


WRITES = []
_guards = {}
for name in ("remove", "unlink", "rename", "replace", "rmdir", "mkdir", "makedirs", "truncate", "chmod", "utime",
             "symlink", "link"):
    fn = getattr(os, name)

    def guard(*a, _n=name, _f=fn, **kw):
        WRITES.append((_n, a[:1]))
        return _f(*a, **kw)
    _guards[name] = (fn, guard)


def spying(on):
    builtins.open = spy_open if on else _real_open
    io.open = spy_open if on else _real_io_open
    for name, (fn, guard) in _guards.items():
        setattr(os, name, guard if on else fn)


before = tree_state()
spying(True)
try:
    # ---------------------------------------------------------------- sessions
    res = C.claude_sessions()
    ids = [x["session"] for x in res["sessions"]]
    ok(ids[:2] == [S1, S2] or ids[:2] == [S2, S1], "sessions: newest first, the old big one after", str(ids))
    row = next(x for x in res["sessions"] if x["session"] == S1)
    ok(row["title"] == "Den TV follows screens table", "sessions: the title is Claude Code's own (ai-title)", row.get("title"))
    ok(row["project"] == "/home/op/github.com/bromigos-org", "sessions: the project path from the transcript's cwd")
    ok(row["live"] and row.get("pane") == "bromigos-org · tab 1" and row.get("position") == "left",
       "sessions: live, with herdr's pane and its position", json.dumps(row)[:300])
    ok(row.get("waiting_on_you") is True, "sessions: S1 ended its turn and is idle: waiting on you")
    ok(row["jobs"]["total"] == 2 and any(j["description"] == "check widgets" and j["status"] == "done"
                                         for j in row["jobs"]["latest"]),
       "sessions: its background jobs, with description and status", json.dumps(row.get("jobs")))
    ok(row["messages"] >= 6, "sessions: message count", row["messages"])
    ok(any("Den TV" in w for w in res["waiting_on_you"]) and not any("Atlas" in w for w in res["waiting_on_you"]),
       "sessions: waiting_on_you lists S1, not the working S2", str(res["waiting_on_you"]))
    s2 = next(x for x in res["sessions"] if x["session"] == S2)
    ok(s2.get("doing", "").startswith("running Bash: python backtest.py"), "sessions: what a busy session is doing", s2.get("doing"))
    det = C.claude_sessions(session=S1[:8])
    ok(len(det["jobs"]) == 2 and det["jobs_total"] == 2 and det["live"], "sessions: one session's detail lists all its jobs")
    jd = C.claude_sessions(session=JOB)
    ok(jd.get("job") == JOB and jd.get("of_session") == S1 and jd.get("status") == "done", "sessions: a job by its agent id", json.dumps(jd)[:200])
    ok("claude.ai" in res["note"], "sessions: says cloud sessions aren't here")

    # ---------------------------------------------------------------- choosing a session
    r0 = C.claude_read()
    ok(r0["session"] == S1 and r0["chosen"] == "the focused pane", "choose: two live, the focused herdr pane wins", r0.get("chosen"))
    ok(C.claude_read(session="the Atlas one")["session"] == S2, "choose: a hint ('the Atlas one')")
    ok(C.claude_read(session="right pane")["session"] == S2, "choose: 'right pane' from herdr's layout")
    ok(C.claude_read(session="left pane")["session"] == S1, "choose: 'left pane'")
    unfocused = json.loads(json.dumps(HERDR_SNAPSHOT))
    unfocused["result"]["snapshot"]["focused_pane_id"] = "w9:p9"
    with _real_open(os.path.join(TMP, "herdr.json"), "w") as f:
        json.dump(unfocused, f)
    amb = C.claude_read()
    ok(amb.get("ambiguous") and amb["ask"].startswith("There are two: ") and amb["ask"].endswith("Which one?")
       and len(amb["choices"]) == 2, "choose: two live, none focused, both recent: ask", json.dumps(amb)[:300])
    ok("bromigos-org: Den TV follows screens table" in amb["ask"] and "atlas: Atlas momentum research" in amb["ask"],
       "choose: the question names project and topic", amb.get("ask"))
    with _real_open(os.path.join(TMP, "herdr.json"), "w") as f:
        json.dump(HERDR_SNAPSHOT, f)

    # ---------------------------------------------------------------- reading
    r1 = C.claude_read(session=S1)
    whos = [m["who"] for m in r1["messages"]]
    texts = " | ".join(m["text"] for m in r1["messages"])
    ok("tool" not in whos and "result" not in whos, "read: without include_tools no calls or results")
    ok("make the den TV follow the screens table" in texts and "Done: the **den TV**" in texts, "read: prompts and replies")
    ok("/clear" in texts and "Caveat" not in texts and "internal note" not in texts and "secret thoughts" not in texts,
       "read: a slash command as typed; no caveats, meta records or thinking")
    ok("and keep YouTube on the scope" in texts and "[an image]" in texts, "read: queued prompts and images")
    ok(any(m["who"] == "summary" for m in r1["messages"]) and any(m["who"] == "job" for m in r1["messages"]),
       "read: compact summaries and job notices")
    ok(r1["now"]["waiting_on_you"] and any(t["task"] == "bk1" for t in r1["now"]["tasks_running"]),
       "read: latest view of a live session: waiting, and its running task", json.dumps(r1.get("now"))[:300])
    ok(ENV_SECRET not in json.dumps(r1), "read: a task's output is redacted")
    r2 = C.claude_read(session=S1, include_tools=True)
    t2 = json.dumps(r2)
    ok(any(m["who"] == "tool" and m["text"].startswith("Bash: export GH_TOKEN=[redacted") for m in r2["messages"]),
       "read: tool calls with a short, redacted argument", t2[:200])
    res_msgs = [m for m in r2["messages"] if m["who"] == "result"]
    ok(res_msgs and res_msgs[0]["text"].count("\n") <= 8 and "2 more lines" in res_msgs[0]["text"],
       "read: a result's first 8 lines")
    ok(GH not in t2 and VAULT not in t2 and not R.leaks(t2), "read: no secret survives")
    # paging the big file forwards: every message once, in order
    seen, cur, pages = [], "b0", 0
    while cur and pages < 400:
        pg = C.claude_read(session=S3, since=cur, limit=60)
        seen += [m["text"] for m in pg["messages"]]
        cur = pg["next"]
        pages += 1
    nums = [int(re.search(r"number (\d+)", t).group(1)) for t in seen]
    ok(len(seen) == 6000 and nums == sorted(nums) and len(set(zip(nums, [t[0] for t in seen]))) == 6000,
       f"read: paging forward covers all 6000 messages once ({pages} pages)", f"{len(seen)}")
    lt = C.claude_read(session=S3, limit=10)
    ok([m["text"][:20] for m in lt["messages"]][-1].startswith("answer number 2999") and lt["older"],
       "read: latest view of a big file is its tail")
    back = C.claude_read(session=S3, before=lt["older"], limit=10)
    ok(back["messages"][-1]["text"].startswith("answer number 2994"), "read: before= pages back", back["messages"][-1]["text"][:30])
    ts = C.claude_read(session=S3, since=iso(NOW - 86400 * 3 + 2990).replace(".000Z", "Z"), limit=4)
    ok(ts["messages"] and "number 2990" in ts["messages"][0]["text"], "read: since= a time")
    jr = C.claude_read(session=JOB)
    ok(jr.get("job_of") == S1 and jr["messages"][-1]["text"] == "Widgets look fine." and jr["status"] == "done",
       "read: a job's own conversation")

    # ---------------------------------------------------------------- live: a growing transcript, partial lines
    append(F1, [user("one more thing")], partial='{"type": "assistant", "message": {"content": [{"type": "te')
    lv = C.claude_read(session=S1, limit=3)
    ok(lv["messages"][-1]["text"] == "one more thing" and not lv["now"]["waiting_on_you"],
       "live: the newest prompt; a half-written line is skipped; not waiting any more", json.dumps(lv["now"])[:200])

    # ---------------------------------------------------------------- search
    sr = C.claude_search("den TV")
    ok(sr["matches"] >= 3 and all("den TV" in x["snippet"] or "den tv" in x["snippet"].lower() for x in sr["results"]),
       "search: words, with snippets", json.dumps(sr)[:300])
    ok(all(x["session"] != S4 for x in sr["results"]), "search: days= keeps out the two-month-old session")
    ok(any(x["session"] == S4 for x in C.claude_search("den TV", days=90)["results"]), "search: days=90 finds it")
    ok(not C.claude_search("den TV", project="atlas")["results"], "search: project= filters")
    ok(C.claude_search('"follows the screens"')["matches"] >= 1 and C.claude_search('"screens the follows"')["matches"] == 0,
       "search: a \"phrase\"")
    ok(any(x.get("job") == JOB for x in C.claude_search("widgets look fine")["results"]), "search: reaches background jobs")
    gs = C.claude_search("gh pr list", include_tools=True)
    ok(gs["matches"] >= 1 and GH not in json.dumps(gs), "search: a hit in a tool call is redacted")
    ok(C.claude_search("tv")["matches"] >= 1 and C.claude_search("hidd")["matches"] == 0, "search: whole words only")

    # ---------------------------------------------------------------- memory and logs
    mm = C.claude_memory()
    ok(len(mm["projects"]) == 2, "memory: projects with memory")
    mi = C.claude_memory(project="bromigos-org")
    ok("den TV follows" in mi["index"] and GH not in mi["index"] and "screens.md" in mi["files"], "memory: the index, redacted")
    ok("screens table" in C.claude_memory(file="screens")["text"], "memory: one file")
    ok(C.claude_memory(query="den TV")["matches"] == 2, "memory: a search across files")
    hl = C.claude_logs("history")
    ok(len(hl["history"]) == 5 and hl["history"][3].get("pasted") == 1 and SK not in json.dumps(hl)
       and "pasted secret" not in json.dumps(hl), "logs: prompt history, pastes counted never shown, redacted")
    dl = C.claude_logs("debug")
    ok(dl["file"] == S1 + ".txt" and BEARER not in json.dumps(dl) and len(dl["lines"]) == 3, "logs: debug log, redacted")
    tl = C.claude_logs("tasks")
    ok([t["task"] for t in tl["tasks"]] == ["bk1"] and tl["tasks"][0]["status"] == "running", "logs: task output (not the job links)")

    # ---------------------------------------------------------------- read aloud
    said = []
    C.SPEAKER = lambda text, who: said.append((text, who)) or True
    sl = C.claude_say_latest(session=S1)
    ok(sl["spoken"] and said and said[-1][0].startswith("Done: the den TV follows the screens table. Big monitor: Shows."),
       "say: the latest final reply, speech-friendly", said[-1][0] if said else "")
    ok("text" not in sl and "Don't repeat it" in sl["note"], "say: spoken, so the model isn't handed it to repeat")
    sa = C.claude_say_latest(about="widgets look fine")
    ok(sa["spoken"] and said[-1][0] == "Widgets look fine.", "say: what Claude said about X")
    C.SPEAKER = None
    sn = C.claude_say_latest(session=S1)
    ok(not sn["spoken"] and sn["text"].startswith("Done: the den TV"), "say: off the desktop the text comes back")
    ok(C.claude_say_latest(session=S2)["found"] is False, "say: no finished reply yet says so")

    # ---------------------------------------------------------------- follow mode
    said.clear()
    C.SPEAKER = lambda text, who: said.append((text, who)) or True
    fo = C.Follow()
    fo.SETTLE_S = 0.2
    fo.on = True
    fo._watch(F1, "bromigos-org: Den TV")
    fo.poll()
    ok(not said, "follow: nothing old is read")
    # finish the half-written line from before as tool chatter, then a real reply
    with _real_open(F1, "a") as f:
        f.write('xt", "text": "Let me check."}], "stop_reason": "tool_use", "id": "mD"}, "timestamp": "' + iso(NOW) + '"}\n')
    append(F1, [assistant([{"type": "tool_use", "id": "t9", "name": "Bash", "input": {"command": "ls"}}], stop="tool_use", mid="mD"),
                user([{"type": "tool_result", "tool_use_id": "t9", "content": "x"}])])
    fo.poll()
    ok(not said, "follow: tool chatter is never read")
    append(F1, [assistant([{"type": "text", "text": "All done. The scope keeps YouTube."}], mid="mE")],
           partial='{"type": "system", "subtype": "turn_dur')
    fo.poll()
    ok(not said, "follow: a final reply waits for its turn to end")
    with _real_open(F1, "a") as f:
        f.write('ation", "durationMs": 5}\n')
    fo.poll()
    ok(len(said) == 1 and said[0][0] == "All done. The scope keeps YouTube.", "follow: spoken once its turn ends", str(said))
    fo.poll()
    ok(len(said) == 1, "follow: and only once")
    append(F1, [assistant([{"type": "text", "text": f"Part one, key {GH}."}], mid="mF"),
                assistant([{"type": "text", "text": "Part two."}], mid="mF")])
    fo.poll()
    time.sleep(0.3)
    fo.poll()
    ok(len(said) == 2 and said[1][0] == "Part one, key something redacted. Part two." and GH not in said[1][0],
       "follow: no turn marker: spoken once settled, all its blocks, redacted", str(said[-1:]))
    fa = C.Follow()
    fa.SETTLE_S = 0.1
    fa.on, fa.target = True, "all"
    fa._watch(F1, "bromigos-org: Den TV")
    fa._watch(F2, "atlas: Atlas momentum research")
    append(F2, [assistant([{"type": "text", "text": "Backtest finished."}], sid=S2, mid="mG"),
                rec("system", subtype="turn_duration", sid=S2)])
    fa.poll()
    ok(said[-1][0] == "From atlas: Backtest finished.", "follow all: says which session each reply is from", said[-1][0])
    st = C.FOLLOW.start(S2)
    ok(st["on"] and st["target"] == S2, "follow: on for a chosen session")
    ok(C.claude_follow("status")["on"] and not C.claude_follow("off")["on"], "follow: status, then off")
    C.SPEAKER = None
    try:
        C.claude_follow("on")
        ok(False, "follow: refuses off the desktop")
    except RuntimeError:
        ok(True, "follow: refuses off the desktop (no voice)")
finally:
    spying(False)

writes_here = [w for w in WRITES if str(w[1][0] if w[1] else "").startswith((CH, TASKS))]
ok(not writes_here, "read only: no remove, rename, mkdir, chmod or utime under Claude's folders", str(writes_here[:3]))
wmodes = [(p, m) for p, m in OPENED if isinstance(p, str) and p.startswith((os.path.realpath(CH), os.path.realpath(TASKS)))
          and any(c in m for c in "wax+")]
# the test's own appends happen with _real_open, so anything here is the tools'
ok(not wmodes, "read only: nothing under Claude's folders opened for writing", str(wmodes[:3]))
forbidden_opened = [p for p, _ in OPENED if p in {os.path.realpath(x) for x in FORBIDDEN}]
ok(not forbidden_opened, "never opened: credentials, session keys, session env, settings, raw tool results", str(forbidden_opened))
after = tree_state()
changed = [p for p in before if p not in (F1, F2) and before[p] != after.get(p)]
ok(not changed, "read only: every fixture file but the two the test appended to is unchanged", str(changed[:3]))
for p in FORBIDDEN:
    ok(not C._readable(p), f"allowlist refuses {os.path.relpath(p, CH)}")
ok(C._readable(F1) and C._readable(os.path.join(TD, "bk1.output")) and not C._readable(os.path.join(TD, f"{JOB}.output")),
   "allowlist: transcripts and task output yes, a task link no (it's a job transcript)")
ok(not C._readable("/etc/passwd") and not C._readable(os.path.join(CH, "projects", "..", "settings.json")),
   "allowlist: nothing outside, no .. tricks")

# the module itself: no write calls anywhere in it
src = _real_open(os.path.join(REPO, "holo/vector/claude.py")).read()
tree = ast.parse(src)
bad = []
for node in ast.walk(tree):
    if isinstance(node, ast.Call):
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if name in ("remove", "unlink", "rename", "replace", "rmtree", "rmdir", "mkdir", "makedirs", "write",
                    "write_text", "write_bytes", "truncate", "chmod", "utime", "symlink", "touch", "kill", "send",
                    "sendall", "Popen"):
            if not (name == "replace" and isinstance(f, ast.Attribute)):        # str.replace
                bad.append((name, node.lineno))
        if name == "open" and isinstance(f, ast.Name):
            mode = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else "r"
            if any(c in mode for c in "wax+"):
                bad.append(("open " + mode, node.lineno))
        if name == "run" and isinstance(f, ast.Attribute):
            argv = node.args[0] if node.args else None
            words = [e.value for e in getattr(argv, "elts", []) if isinstance(e, ast.Constant)]
            if words and words[0] not in ("hyprctl",) and words != []:
                bad.append(("run " + " ".join(map(str, words)), node.lineno))
ok(not bad, "read only: claude.py has no write, delete, send or spawn calls", str(bad))
herdr_args = _real_open(HERDR_CALLS).read().split("\n") if os.path.exists(HERDR_CALLS) else []
ok(herdr_args and all(a in ("api snapshot", "agent list", "") for a in herdr_args),
   "herdr: only listed (api snapshot, agent list), never sent anything", str(set(herdr_args)))

# ================================================================== the phone gets three, the desktop all
ms = ast.parse(_real_open(os.path.join(REPO, "holo/vector/mcp_server.py")).read())
lists = {t.id: ast.literal_eval(n.value) for n in ms.body if isinstance(n, ast.Assign) for t in n.targets
         if isinstance(t, ast.Name) and t.id in ("READ_ONLY", "ACT", "DESKTOP_ONLY")}
ok({"claude_sessions", "claude_search"} <= set(lists["READ_ONLY"]), "mcp: sessions and search off the desktop")
ok(not {"claude_memory", "claude_logs", "claude_say_latest", "claude_follow", "claude_read"} & set(lists["READ_ONLY"] + lists["ACT"]),
   "mcp: no memory, logs, speech or full reading off the desktop")
fn = next(n for n in ms.body if isinstance(n, ast.FunctionDef) and n.name == "claude_latest")
body = ast.unparse(fn)
ok([a.arg for a in fn.args.args] == ["session", "limit"] and "'latest': True" in body and "include_tools" not in body
   and "min(int(limit or 10), 20)" in body, "mcp: claude_read is the latest view only (session, limit; at most 20)")

# ================================================================== the gate: listed, audited with what was read
try:
    os.environ.update(HOME=TMP, XDG_RUNTIME_DIR=TMP, XDG_STATE_HOME=os.path.join(TMP, "state"))
    from holo.vector import tools  # noqa: E402
    have_tools = True
except Exception as e:                  # the desktop's modules aren't all importable everywhere
    have_tools = False
    print(f"skip  the gate ({type(e).__name__}: {str(e)[:80]})")
if have_tools:
    tools.AUDIT = os.path.join(TMP, "audit.log")
    tools.STATE = TMP
    for n in ("claude_sessions", "claude_read", "claude_search", "claude_memory", "claude_logs", "claude_say_latest",
              "claude_follow"):
        ok(n in tools.SPECS and n in tools.FUNCS, f"gate: {n} is in his allowlist")
    ok({"claude_sessions", "claude_read", "claude_search"} <= tools.WHILE_OFF and "claude_follow" not in tools.WHILE_OFF,
       "gate: the read tools work while he's switched off; follow mode doesn't")
    out, _ = tools.call("claude_read", {"session": S1, "limit": 5})
    out2, _ = tools.call("claude_search", {"query": "den TV"})
    recs = [json.loads(x) for x in _real_open(tools.AUDIT).read().splitlines()]
    rd = next(r_ for r_ in recs if r_["tool"] == "claude_read")
    ok(rd["read"]["session"] == S1 and "range" in rd["read"] and "den TV" not in json.dumps(rd),
       "audit: what was read (session, byte range), never the content", json.dumps(rd)[:200])
    sq = next(r_ for r_ in recs if r_["tool"] == "claude_search")
    ok(sq["args"]["query"].get("sha1") and "_read" not in out, "audit: a search's words are hashed; no internals reach the model")

proc.kill()
print(f"\n{'all passed' if not BAD[0] else str(BAD[0]) + ' failed'}")
sys.exit(1 if BAD[0] else 0)
