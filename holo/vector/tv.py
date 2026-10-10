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
