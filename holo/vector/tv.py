"""Den TV tools, using bromigOS's one channel library. tools.call audits every call.

The phone gets read and individual picks only. Creating, tuning and deleting whole
channels stay on the desktop. A whole-channel delete needs the operator's latest words
to ask for removal and name this exact channel; a caller's confirm flag alone is not enough.
"""
import importlib.util
import re
import sys

if '/usr/lib/bromigos/lib' not in sys.path:
    sys.path.append('/usr/lib/bromigos/lib')


def available():
    return importlib.util.find_spec('bromigos_tvchannels') is not None


def available_player():
    return all(importlib.util.find_spec(n) is not None for n in ("bromigos_tvplay", "bromigos_tvremote"))


def available_cast():
    return importlib.util.find_spec("bromigos_cast") is not None


def _cast():
    import os
    import bromigos_cast
    os.environ["BROMIGOS_CAST_CALLER"] = "vector"     # who asked, in the cast audit log
    return bromigos_cast


def _channels():
    import bromigos_tvchannels
    return bromigos_tvchannels


def tv_channels():
    """Every configured den TV channel, its contents, order, screens and now/next."""
    return _channels().Store().snapshot()


def tv_channel_add_item(channel: str, title: str, kind: str = 'show'):
    """Add one show, movie, YouTube URL or music pick to a picked channel."""
    if kind not in ('show','movie','youtube','music'):
        raise ValueError('kind: show, movie, youtube or music')
    return _channels().Store().item(channel,title,item_kind=kind)


def tv_channel_remove_item(channel: str, title: str):
    """Remove a pick from a channel; this never deletes its media files."""
    return _channels().Store().item(channel,title,remove=True)


def tv_channel_create(name: str, shows: list[str] | None = None, items: list[str] | None = None,
                      library: str | None = None, youtube: str | None = None, folder: str | None = None,
                      order: str = 'shuffle', bumpers: bool = False):
    """Create a channel in the shared config, without a session restart."""
    return _channels().Store().create(name,shows,items,library,youtube,folder,order,bumpers)


def tv_tune(channel: str, screen: str = 'big'):
    """Tune a channel by its name or config number; the library finds its screen number."""
    return _channels().Store().tune(channel,screen)


def tv_play(screen: str = 'big', title: str | None = None, id: str | None = None,
            url: str | None = None, source: str | None = None, resume: bool = False):
    """Play a downloaded episode/movie or YouTube video now, using the shared resolver."""
    import bromigos_tvplay
    return bromigos_tvplay.play(screen, title, id, url, source, resume)


def tv_remote(action: str, screen: str = 'big'):
    """One remote action on a named screen; does not capture the operator's keyboard."""
    import bromigos_tvremote
    from bromigos_tvplay import screen_name
    if action not in bromigos_tvremote.ACTIONS or action in ('digits','leave','cancel') or action.startswith('digit-'):
        raise ValueError('Use volume-up/down, channel-up/down, mute, skip, previous, open, sound or guide')
    return bromigos_tvremote.send('remote ' + action + ' --screen ' + screen_name(screen))


def tv_cast_targets():
    """Every screen to cast to: Chromecasts, DLNA TVs, AirPlay, Jellyfin's apps and the den's screens."""
    out = _cast().targets()
    return {k: out[k] for k in ("targets", "protocols")}


def tv_cast(target: str, title: str | None = None, id: str | None = None, source: str | None = None,
            url: str | None = None, screen: str | None = None, resume: bool = False):
    """Play on a cast target, named by its id or words from its name or room ("living room Chromecast").
    title, id with source, a YouTube url, or screen (what that den screen shows, from the same moment)."""
    if sum(bool(v) for v in (title, id, url, screen)) != 1:
        raise ValueError("Give exactly one of title, id, url or screen")
    return _cast().play(target, screen=screen, title=title, id_=id, source=source, url=url, resume=resume)


def tv_cast_control(target: str, action: str, value: float | None = None):
    """play, pause, stop, seek (value: seconds), volume (value: 0-100), volume-up, volume-down, mute, unmute."""
    return _cast().control(target, action, value)


def tv_cast_handback(target: str, screen: str = 'big'):
    """Stop a cast target and go on from the same moment on a den screen."""
    return _cast().handback(target, screen)


def tv_channel_delete(channel: str, confirm: bool = False):
    """Delete a whole channel only on an explicit request naming it. Media files stay."""
    from . import tools
    store = _channels().Store()
    doc = store.load(); index = store.index(doc,channel)
    name = store.display_name(doc.channels()[index],index)
    if not confirm:
        return {'name':name,'say':f'Removing {name} removes the channel only. Ask for an explicit request naming it.'}
    words = tools.LAST_USER_TEXT.get('text') or ''
    from .media import _norm
    target = re.escape(_norm(name))
    requested = re.search(r"\b(?:delete|remove|erase|trash|wipe|get rid of)\s+(?:the\s+)?"
                          r"(?:(?:den\s+tv\s+)?channel\s+)?(?:named\s+|called\s+)?(?:the\s+)?" + target +
                          r"\b(?!\s+(?:s|pick|item|episode|movie|show)\b)",_norm(words))
    if re.search(r"\b(?:don't|do not|never|stop|cancel)\b",words,re.I) or not requested:
        raise PermissionError(f'Deleting channel {name} needs the operator to request it by name')
    return store.remove(name)
