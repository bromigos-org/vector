"""VECTOR's media tools: ask the homelab's *arr stack for films, shows, anime and music
(bromigOS's `bromigos lab media`, its docs/lab.md "Media requests"), at the best quality the
lab's profiles allow, in Sir's languages.

  media_search     find a title: year, kind, release dates, the profile it would get, the size,
                   whether it's already in the library or requested
  media_get        request it (a TV, film, anime, artist or album; a YouTube link goes to
                   Pinchflat). Without confirm it returns the plan, to tell Sir; with confirm
                   it requests. "Get season 2 of X", "keep up with X", "stop getting new
                   episodes of X" are its seasons, episodes and new options
  media_status     what's downloading (progress, time left), imported, failed and why, the
                   audio and subtitle languages, and for one title its seasons and monitoring
  media_upcoming   what's tracked and when it comes out
  media_language   Sir's audio and subtitle languages ("I prefer Japanese audio with subtitles")

Limits, here in code: nothing here deletes, removes or unmonitors a file or a title (no
command for it exists); a request needs confirm=true, which the tool description ties to Sir's
go-ahead; the lab's API keys stay in bromigos-homelab's config (the tools see answers only).
Every call goes through tools.call (the audit log, the event feed, stop-after-a-block).
"""
from .lab import _lab, available  # noqa: F401  (available: the tools' gate)

QUALITIES = ("best", "2160p", "1080p", "720p")
KINDS = ("movie", "tv", "anime", "music")


def _trim(it):
    keep = ("id", "kind", "title", "year", "summary", "release", "anime", "anime_why", "profile", "size", "episodes",
            "library", "arr", "artist", "album_type", "tracks", "youtube")
    out = {k: it.get(k) for k in keep if it.get(k) not in (None, "", [], {})}
    if isinstance(out.get("profile"), dict):
        out["profile"] = {k: out["profile"].get(k) for k in ("name", "why", "warnings") if out["profile"].get(k)}
    if it.get("seasons"):
        out["seasons"] = len([s for s in it["seasons"] if s.get("number")])
    return out


def media_search(query, kind=None, limit=6):
    """Find a film, show, anime or music on the lab (bromigos lab media search --json)."""
    args = ["media", "search", str(query)[:200], "--json", "--limit", str(max(1, min(int(limit or 6), 12)))]
    if kind in KINDS:
        args.append("--" + kind)
    code, doc, err = _lab(args, timeout=120)
    if doc is None:
        return {"found": [], "say": "Nothing found." if code == 4 else (err or "the lab didn't answer")[:300]}
    if isinstance(doc, dict) and doc.get("error"):
        return {"error": doc["error"]}
    found = [_trim(x) for x in doc]
    say = ("Tell Sir what you found in one line each (title, year, kind, the profile, the size, if it's in the "
           "library already). If more than one could be what he meant, ask which.") if found else "Nothing found."
    return {"found": found, "say": say}


def media_get(what, confirm=False, kind=None, quality=None, audio=None, seasons=None, episodes=None, specials=False,
              new=None, when=None):
    """Plan, or with confirm make, a media request (bromigos lab media get --json)."""
    args = ["media", "get", str(what)[:300], "--json"]
    if kind in KINDS:
        args.append("--" + kind)
    if quality:
        if quality not in QUALITIES:
            raise ValueError(f"quality: {', '.join(QUALITIES)}")
        args += ["--quality", quality]
    if audio:
        args += ["--audio", str(audio)[:8]]
    if seasons:
        args += ["--seasons", str(seasons)[:40]]
    if episodes:
        args += ["--episodes", str(episodes)[:200]]
    if specials:
        args.append("--specials")
    if new is not None:
        args += ["--new", "yes" if new else "no"]
    if when:
        if when not in ("announced", "cinemas", "released"):
            raise ValueError("when: announced, cinemas or released")
        args += ["--when", when]
    if confirm:
        args.append("--yes")
    code, doc, err = _lab(args, timeout=240)
    if code == 3 and doc:
        return {"ambiguous": True, "matches": doc.get("matches"),
                "say": "More than one title matches: ask Sir which one (by year, or the US or UK version)."}
    if doc is None:
        return {"error": (err or "the lab didn't answer")[:300]}
    if doc.get("error"):
        return {"error": doc["error"]}
    plan = doc.get("plan") or {}
    lines = plan.get("lines") or []
    item = _trim(plan.get("item") or {})
    if not confirm:
        audio_line = next((ln.strip() for ln in lines if ln.strip().startswith("Audio:")), None)
        return {"plan": lines, "item": item,
                "say": "Tell Sir what you'd request, in a sentence or two: the title, year, the profile and why, the "
                       "size and the free space, what gets monitored" + (", and which audio he gets" if audio_line
                                                                        else "") +
                       ". If he already said plainly to download it and this is the one he meant, call media_get "
                       "again with confirm=true; otherwise ask."}
    res = doc.get("result") or {}
    return {"done": res.get("lines") or [], "item": item, "route": res.get("route"),
            "monitoring": res.get("monitoring"), "profile": res.get("profile"), "subtitles": res.get("subtitles"),
            "say": "Tell Sir it's requested, and what you set: the monitoring in his words (e.g. 'Monitoring seasons "
                   "1–3 and future episodes'), the profile, and for anime the audio (e.g. 'dual audio: English + "
                   "Japanese'). Say you'll tell him when it's in the library."}


def media_status(query=None, why=False):
    """Downloads, requests and one title's detail (bromigos lab media status --json)."""
    args = ["media", "status"] + ([str(query)[:200]] if query else []) + (["--why"] if why else []) + ["--json"]
    code, doc, err = _lab(args, timeout=180)
    if doc is None:
        return {"error": (err or "the lab didn't answer")[:300]}
    if doc.get("error"):
        return {"error": doc["error"]}
    downloads = [{k: g.get(k) for k in ("title", "items", "states", "progress", "eta_s", "speed", "size", "audio", "why")}
                 for g in doc.get("downloads") or []][:20]
    return {"lines": (doc.get("lines") or [])[:60], "downloads": downloads, "titles": doc.get("titles") or [],
            "requests": doc.get("requests") or [], "errors": doc.get("errors") or [],
            "say": "Answer from these lines: progress and time left, what's stuck and why, the audio and subtitle "
                   "languages, and for a show what's monitored and whether new episodes are picked up."}


def media_upcoming(days=90):
    code, doc, err = _lab(["media", "upcoming", "--days", str(max(1, min(int(days or 90), 400))), "--json"], timeout=120)
    if doc is None:
        return {"error": (err or "the lab didn't answer")[:300]}
    return {"upcoming": doc[:60], "say": "Read the dates out plainly (day and title); say which are already on disk."}


def media_recent(hours=24):
    code, doc, err = _lab(["media", "recent", "--hours", str(max(1, min(float(hours or 24), 24 * 14))), "--json"],
                          timeout=120)
    if doc is None:
        return {"error": (err or "the lab didn't answer")[:300]}
    return {"imported": doc[:40]}


def media_language(action="show", languages=None):
    """Sir's media languages: show, or set audio | anime | subtitles | required."""
    if action not in ("show", "set", "anime", "subtitles", "required"):
        raise ValueError("action: show, set, anime, subtitles or required")
    vals = [str(v)[:8] for v in (languages or [])][:4]
    if action != "show" and not vals:
        raise ValueError("languages: ISO 639-1 codes, best first (['ja', 'en']), 'same' for anime, yes|no for required")
    code, doc, err = _lab(["media", "language", action] + vals + ["--json"], timeout=60)
    if doc is None:
        return {"error": (err or "the lab didn't answer")[:300]}
    return dict(doc, say="Tell Sir what's set now, in a sentence (audio, anime, subtitles).")
