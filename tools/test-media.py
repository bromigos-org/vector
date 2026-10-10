#!/usr/bin/env python3
"""VECTOR's media requests (holo/vector/media.py, guard.py's media rule).

    ~/.local/share/bromigos/venv-brain/bin/python tools/test-media.py

His media tools plan first and request only with confirm (the tool he's told to call on
Sir's go-ahead), say which audio and monitoring he got, ask when a name is ambiguous, and
his terminal may read `bromigos lab media` but not request through it. A stub stands in for
`bromigos-lab` (it records every argv and answers like the CLI), so nothing here reaches a
lab. Exit 1 on any failure.
"""
import json
import os
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

TMP = tempfile.mkdtemp(prefix="vector-media-test-")
CALLS = os.path.join(TMP, "calls.jsonl")
STUB = os.path.join(TMP, "bromigos-lab")
with open(STUB, "w") as f:
    f.write(f"""#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
open({CALLS!r}, "a").write(json.dumps({{"argv": a, "by": os.environ.get("BROMIGOS_LAB_BY")}}) + "\\n")
item = {{"id": "tv:tvdb:90001", "kind": "tv", "title": "Starlight Harbor", "year": 1999, "anime": True,
         "seasons": [{{"number": 0}}, {{"number": 1}}], "size": 94000000000,
         "profile": {{"name": "Remux-1080p - Anime (English audio)", "why": "the best anime profile here"}},
         "summary": "Starlight Harbor, 1999, anime TV, 1 season, Sonarr profile Remux-1080p - Anime (English audio), about 94 GB"}}
plan = {{"item": item, "lines": ["Starlight Harbor, 1999, anime TV", "  Audio: you want English required, Japanese welcome as a second track. The profile: English audio required; dual audio (English + Japanese) ranked first"]}}
if a[:2] == ["media", "search"]:
    print(json.dumps([dict(item, raw={{"secret": "no"}})]))
elif a[:2] == ["media", "get"] and "Lantern" in a[2]:
    print(json.dumps({{"ambiguous": True, "matches": [{{"id": "tv:tvdb:90002", "summary": "The Lantern Office, 2005"}},
                                                     {{"id": "tv:tvdb:90003", "summary": "The Lantern Office, 2001"}}]}}))
    sys.exit(3)
elif a[:2] == ["media", "get"] and "--yes" not in a:
    print(json.dumps({{"plan": plan, "asked": True}})); sys.exit(2)
elif a[:2] == ["media", "get"]:
    print(json.dumps({{"plan": plan, "result": {{"route": "seerr", "monitoring": "Monitoring season 1 and specials; new episodes and seasons are picked up automatically", "profile": "Remux-1080p - Anime (English audio)", "lines": ["Requested through Seerr."]}}}}))
elif a[:2] == ["media", "status"]:
    print(json.dumps({{"downloads": [{{"title": "Starlight Harbor", "items": 2, "progress": 40.0, "audio": ["en", "ja"]}}],
                      "lines": ["Downloading:", "  Starlight Harbor: 2 items, 40%"], "titles": [], "requests": []}}))
elif a[:2] == ["media", "upcoming"]:
    print(json.dumps([{{"date": "2026-11-02", "title": "Paper Moon Cafe", "what": "S04E01"}}]))
elif a[:2] == ["media", "delete"]:
    plan = {{"scope": "title", "title": "Paper Moon Cafe", "size": 8000000000, "files": 4,
            "what": "Paper Moon Cafe: 4 seasons, 4 files, files and all"}}
    if "--yes" not in a:
        print(json.dumps({{"plan": plan, "asked": True}})); sys.exit(2)
    print(json.dumps(dict(plan, deleted=True, seerr=None, rescanned=["jellyfin"])))
elif a[:2] == ["media", "language"]:
    print(json.dumps({{"audio": ["en", "ja"], "anime_audio": a[3:4] if a[2] == "anime" else ["en", "ja"],
                      "subtitles": ["en"], "required": True}}))
else:
    sys.exit(2)
""")
os.chmod(STUB, os.stat(STUB).st_mode | stat.S_IEXEC)
os.environ["BROMIGOS_LAB_BIN"] = STUB

try:                                   # CI: no bromigos-core, so no private overlay module; an empty one
    import bromigos_private  # noqa: F401
except ImportError:
    with open(os.path.join(TMP, "bromigos_private.py"), "w") as f:
        f.write("def data(): return {}\ndef present(): return False\ndef get(key, default=None): return default\n"
                "def url(name, path='', default=''): return default\ndef vault(name, default=''): return default\n"
                "def vault_root(default=''): return default\ndef lan_domain(default=''): return default\n"
                "def path(name, default=''): return default\nPATH = ''\n")
    sys.path.insert(0, TMP)

from holo.vector import guard, media  # noqa: E402

BAD = [0]


def ok(cond, what, detail=""):
    print(f"{'pass' if cond else 'FAIL'}  {what}" + (f"  ({detail})" if detail and not cond else ""))
    BAD[0] += not cond


def calls():
    with open(CALLS) as f:
        return [json.loads(x) for x in f]


# ------------------------------------------------------------------ his terminal
for cmd in ("bromigos lab media status", "bromigos lab media search dune --movie", "bromigos lab media upcoming",
            "bromigos lab media language", "bromigos lab media language show", "bromigos lab media recent"):
    ok(guard.check_command(cmd.split(), cmd) is None, f"his terminal may read: {cmd}")
for cmd in ("bromigos lab media get 'Starlight Harbor' --yes", "bromigos lab media language set ja",
            "bromigos-lab media get x --yes", "bromigos lab media language anime ja"):
    why = guard.check_command(cmd.split(), cmd)
    ok(bool(why) and "media_get" in why, f"his terminal refuses: {cmd}", why)

# ------------------------------------------------------------------ the tools
r = media.media_search("starlight", kind="anime")
ok(r["found"][0]["id"] == "tv:tvdb:90001" and "raw" not in r["found"][0], "search trims the answer")
ok(calls()[-1]["argv"][:5] == ["media", "search", "starlight", "--json", "--limit"] and "--anime" in calls()[-1]["argv"],
   "search asks the lab", calls()[-1]["argv"])

r = media.media_get("Starlight Harbor", seasons="all", specials=True)
argv = calls()[-1]["argv"]
ok("--yes" not in argv and r.get("plan"), "without confirm: only the plan", argv)
ok("which audio" in r["say"], "the plan's words ask him to say which audio (anime)")
ok("confirm=true" in r["say"], "and when to confirm")

r = media.media_get("Starlight Harbor", confirm=True, seasons="all", specials=True, new=True)
argv = calls()[-1]["argv"]
ok(argv[-1] == "--yes" and "--specials" in argv and argv[argv.index("--new") + 1] == "yes", "confirm requests it", argv)
ok(r["monitoring"].startswith("Monitoring season 1 and specials") and "dual audio" in r["say"],
   "he confirms what he set, and the audio")
ok(calls()[-1]["by"] == "vector", "his requests are recorded as his")

r = media.media_get("The Lantern Office", confirm=True)
ok(r.get("ambiguous") and len(r["matches"]) == 2 and "ask Sir which" in r["say"], "ambiguous: he asks which")

r = media.media_get("Paper Moon Cafe", seasons="none", new=False)
argv = calls()[-1]["argv"]
ok(argv[argv.index("--seasons") + 1] == "none" and argv[argv.index("--new") + 1] == "no",
   "'stop getting new episodes' is seasons none, new no", argv)

for bad in ({"quality": "8k"}, {"when": "tomorrow"}):
    try:
        media.media_get("x", **bad)
        ok(False, f"refuses {bad}")
    except ValueError:
        ok(True, f"refuses {bad}")

r = media.media_status("starlight")
ok(r["downloads"][0]["audio"] == ["en", "ja"] and r["lines"], "status: progress and audio")
ok(media.media_upcoming(30)["upcoming"][0]["what"] == "S04E01", "upcoming")
r = media.media_language("anime", ["ja"])
ok(r["anime_audio"] == ["ja"] and calls()[-1]["argv"][:4] == ["media", "language", "anime", "ja"],
   "'I prefer the Japanese audio' sets anime ja")
try:
    media.media_language("set")
    ok(False, "set needs languages")
except ValueError:
    ok(True, "set needs languages")

# ------------------------------------------------------------------ deletes: only on Sir's own words
from holo.vector import tools as T  # noqa: E402
r = media.media_delete("Paper Moon Cafe")
ok(r.get("plan") and "--yes" not in calls()[-1]["argv"], "a delete plans first and deletes nothing")
for said in ("", "what's in my library?", "delete it", "delete The Office", "paper moon cafe is good"):
    T.LAST_USER_TEXT["text"] = said
    try:
        media.media_delete("Paper Moon Cafe", confirm=True)
        ok(False, f"refused when Sir said {said!r}")
    except PermissionError:
        ok("--yes" not in calls()[-1]["argv"], f"refused when Sir said {said!r}")
T.LAST_USER_TEXT["text"] = "yes, delete Paper Moon Cafe please"
r = media.media_delete("Paper Moon Cafe", confirm=True)
ok(calls()[-1]["argv"][-1] == "--yes" and r.get("deleted"), "deleted when Sir named it and asked")
T.LAST_USER_TEXT["text"] = ""
for cmd in ("bromigos media delete x --yes", "bromigos lab media delete x --yes", "bromigos-media monitor x --off",
            "bromigos lab media profile x WEB-1080p"):
    why = guard.check_command(cmd.split(), cmd)
    ok(bool(why) and "media_delete" in why, f"his terminal refuses: {cmd}", why)
ok(guard.check_command("bromigos media library tv".split(), "bromigos media library tv") is None,
   "his terminal may read the library")

# ------------------------------------------------------------------ the tool table
from holo.vector import tools  # noqa: E402
ok(all(n in tools.CORE or n in tools.HIDDEN for n in ("media_search", "media_get", "media_status", "media_upcoming",
                                                     "media_recent", "media_language")), "the tools are in the table")
ok({"media_search", "media_status", "media_upcoming", "media_recent"} <= tools.WHILE_OFF and
   not {"media_get", "media_delete"} & tools.WHILE_OFF, "switched off: reads only")
try:
    from holo.vector import mcp_server  # noqa: E402  (needs the mcp package: the brain's venv)
except ImportError:
    print("skip  MCP lists (no mcp package here)")
else:
    ok("media_get" in mcp_server.ACT and "media_get" not in mcp_server.READ_ONLY, "MCP: media_get is an action")
    ok("media_delete" not in mcp_server.ACT + mcp_server.READ_ONLY, "MCP: no media_delete (the phone never deletes)")

print("FAILED" if BAD[0] else "all passed")
sys.exit(1 if BAD[0] else 0)
