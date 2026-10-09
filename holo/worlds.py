"""The worlds' holograms: which world a collection belongs to, the gallery's order, and
each world's colours to draw its models in.

bromigOS ships hologram collections with its themes (bromigos-core; its docs/theming.md):

  SHARE/themes/<world>/holo/    a world's own models (Mire's stilt shack, Tidewell's choir, …)
  SHARE/theme/holo/             the Drift's: the craft and relays every world shares

and a theme extension (`bromigos ext install`) can carry its own `holo/` folder, linked
with its theme at ~/.local/share/bromigos/ext/active/themes/<world>/holo/. SHARE is
/usr/share/bromigos, or $BROMIGOS_SHARE_DIR (a bromigOS checkout, for a test). fmt.py
finds the models; this file only reads: the packaged folders are root's, the extensions'
are the host's (read-only to VECTOR's terminal: guard.py's WRITE_LOCKED), and a world's
colours are its colors.toml. Nothing here writes a file.

A world's models are drawn in that world's colours, as bromigos.org's contact sheet shows
them: its colors.toml (first-party, or the extension's, under the user's overlay in
~/.config/bromigos/themes/<world>/), resolved the way `bromigos theme set` resolves it.
The world in force, VECTOR's own models and the Drift's are drawn in palette.json itself,
so they look exactly like the desktop; and under high contrast every model is: the user's
contrast setting comes before a world's look.
"""
import colorsys
import os
import time
import tomllib

from .paths import CONFIG, EXT_ACTIVE

from . import paths  # noqa: E402,F401  (the shared libs on sys.path)
import bromigos_theme as T  # noqa: E402

SHARE = os.environ.get("BROMIGOS_SHARE_DIR") or "/usr/share/bromigos"
# The two groups that aren't a theme's world (a theme's name can't hold a colon).
VECTOR, DRIFT = ":vector", ":drift"
TITLES = {VECTOR: "VECTOR", DRIFT: "THE DRIFT"}
# First-party worlds in the order bromigos.org shows them; the rest (extensions') by name.
WORLDS = ["mire", "tidewell", "rime", "sunder", "cinder", "lowtown", "highwind", "deepvein"]


def world_dirs():
    """[(holo dir, group)]: the worlds' packaged collections, the Drift's, then the
    extensions' themes' (bromigos ext refuses a theme named like a first-party one; if
    one were there anyway, the first-party world keeps the name)."""
    out, seen = [], set()
    packaged = os.path.join(SHARE, "themes")
    for root in (packaged, os.path.join(EXT_ACTIVE, "themes")):
        try:
            names = sorted(os.listdir(root))
        except OSError:
            names = []
        for w in names:
            d = os.path.join(root, w, "holo")
            if w not in seen and os.path.isdir(d):
                seen.add(w)
                out.append((d, w))
        if root == packaged and os.path.isdir(os.path.join(SHARE, "theme", "holo")):
            out.append((os.path.join(SHARE, "theme", "holo"), DRIFT))
    return out


def current():
    """The theme in force (palette.json's), whose world's models come first."""
    try:
        T.version()                                 # a theme switch since (stats at most once a second)
        return T.name()
    except Exception:
        return "wick"


def rank(group, here=None):
    """Where a group sits in the gallery: the world in force first, then VECTOR's own,
    the Drift's, the first-party worlds, the extensions' by name."""
    here = current() if here is None else here
    if group == here:
        return (0, "")
    if group == VECTOR:
        return (1, "")
    if group == DRIFT:
        return (2, "")
    if group in WORLDS:
        return (3, f"{WORLDS.index(group):02d}")
    return (4, group)


# ------------------------------------------------------------------ a theme's files

def _theme_dirs(name):
    """The folders theme `name` is made of, base first: first-party or the extension's,
    then the user's overlay (docs/theming.md, "Overlay order")."""
    out = [d for d in (os.path.join(SHARE, "themes", name), os.path.join(EXT_ACTIVE, "themes", name))
           if os.path.isdir(d)][:1]
    user = os.path.join(CONFIG, "themes", name)
    if os.path.isdir(user):
        out.append(user)
    return out


def _toml(dirs, file):
    """The file merged over its overlays, table by table (a later key wins)."""
    doc = {}
    for d in dirs:
        try:
            with open(os.path.join(d, file), "rb") as f:
                part = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        for k, v in part.items():
            if isinstance(v, dict) and isinstance(doc.get(k), dict):
                doc[k] = {**doc[k], **v}
            else:
                doc[k] = v
    return doc


def _sig(dirs):
    out = []
    for d in dirs:
        for f in ("colors.toml", "theme.toml"):
            try:
                out.append(os.stat(os.path.join(d, f)).st_mtime_ns)
            except OSError:
                out.append(None)
    return tuple(dirs), tuple(out)


_titles = {}


def title(group):
    """A group's name in the gallery: a world's theme.toml name (MIRE), VECTOR, THE DRIFT."""
    if group in TITLES:
        return TITLES[group]
    dirs = _theme_dirs(group)
    sig = _sig(dirs)
    if group not in _titles or _titles[group][0] != sig:
        name = _toml(dirs, "theme.toml").get("name")
        _titles[group] = (sig, (name if isinstance(name, str) and name.strip() else group).upper())
    return _titles[group][1]


# ------------------------------------------------------------------ colours

def _hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _fmt(rgb):
    return "#" + "".join(f"{max(0, min(255, round(v * 255))):02x}" for v in rgb)


def _is_hex(v):
    return isinstance(v, str) and len(v) == 7 and v[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in v[1:])


def resolve(doc):
    """A merged colors.toml -> (colors, tables) with every reference resolved, as
    `bromigos theme set` does (bromigos-core theme.rs, parse_colors): a value is #rrggbb or
    another colour's name (a role, a [colors] name, or table.key), chained up to 32 deep.
    ValueError on a name that isn't there, a loop, or a value that isn't a colour."""
    root, tables = {}, {}
    for k, v in doc.items():
        if isinstance(v, dict):
            if k == "colors":
                root.update({kk: vv for kk, vv in v.items()})
            else:
                tables[k] = dict(v)
        else:
            root[k] = v

    def look(ref):
        t, _, k = ref.partition(".")
        return tables.get(t, {}).get(k) if k else root.get(ref)

    def res(key, v):
        for _ in range(32):
            if not isinstance(v, str):
                raise ValueError(f"{key}: not a colour")
            if v.startswith("#"):
                if not _is_hex(v):
                    raise ValueError(f"{key}: {v!r} is not #rrggbb")
                return v.lower()
            nxt = look(v)
            if nxt is None:
                raise ValueError(f"{key}: no colour named {v!r}")
            v = nxt
        raise ValueError(f"{key}: the names refer to each other in a loop")
    colors = {k: res(k, v) for k, v in root.items()}
    return colors, {t: {k: res(f"{t}.{k}", v) for k, v in m.items()} for t, m in tables.items()}


class Palette:
    """A theme's colours, resolved: what bromigos_theme answers for the theme in force,
    for another theme (rgb, rgb255, retint_rgb: what render.col, lin and wick ask)."""

    def __init__(self, theme, colors, tables):
        self.theme = theme
        self.colors = {**T.WICK, **colors}
        self.tables = {t: dict(m) for t, m in T.WICK_TABLES.items()}
        for t, m in tables.items():
            self.tables.setdefault(t, {}).update(m)
        self._map = {}                              # a Wick colour -> this theme's (as retint does)
        for k, v in T.WICK.items():
            self._map.setdefault(v.lower(), self.colors.get(k, v).lower())
        for t, kv in T.WICK_TABLES.items():
            for k, v in kv.items():
                self._map.setdefault(v.lower(), self.tables.get(t, {}).get(k, v).lower())

    def __repr__(self):
        return f"<Palette {self.theme} primary {self.colors['primary']}>"

    def hex(self, n):  # noqa: A003
        if n.startswith("#"):
            return self.retint_hex(n)
        if "." in n:
            t, k = n.split(".", 1)
            return self.tables[t][k]
        return self.colors[T.LEGACY.get(n, n)]

    def rgb(self, n):
        return _hexrgb(self.hex(n))

    def rgb255(self, n):
        return tuple(round(v * 255) for v in self.rgb(n))

    def _shift(self, rgb_):
        """A Wick shade that isn't a role -> this theme, as bromigos_theme._shift does: dark
        greens onto the background -> panel -> selection ramp, other greens to primary's hue."""
        h, l, s = colorsys.rgb_to_hls(*rgb_)
        if s < 0.08 or not (70 <= h * 360 <= 170):
            return rgb_
        ramp = [(colorsys.rgb_to_hls(*_hexrgb(T.WICK[k]))[1], k) for k in ("background", "panel", "selection")]
        if l <= ramp[-1][0]:
            for (l0, k0), (l1, k1) in zip(ramp, ramp[1:]):
                if l <= l1:
                    t = max(0.0, (l - l0) / (l1 - l0))
                    a, b = _hexrgb(self.colors[k0]), _hexrgb(self.colors[k1])
                    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))
        ph, pl, ps = colorsys.rgb_to_hls(*_hexrgb(self.colors["primary"]))
        _, wl, ws = colorsys.rgb_to_hls(*_hexrgb(T.WICK["primary"]))
        return colorsys.hls_to_rgb(ph, min(1.0, l * (pl / wl) if wl else l), min(1.0, s * (ps / ws) if ws else s))

    def retint_hex(self, h):
        low = h.lower()
        return self._map.get(low) or _fmt(self._shift(_hexrgb(low)))

    def retint_rgb(self, rgb_):
        """A Wick colour as floats (0..1) -> this theme's, as floats. Alpha passes through."""
        return _hexrgb(self.retint_hex(_fmt(rgb_[:3]))) + tuple(rgb_[3:])


_palettes = {}       # theme -> (signature, Palette or None)
_checked = {}        # theme -> when its files were last looked at
RECHECK = 1.0        # seconds: a palette is re-read when its colors.toml changes


def palette(theme):
    """Theme `theme`'s resolved colours (a Palette), or None when it has none that resolve
    (no such theme, a colors.toml that doesn't parse): its models then draw in the palette
    in force. Re-read when the files change, looked at at most once a second."""
    now = time.monotonic()
    have = _palettes.get(theme)
    if have is not None and now - _checked.get(theme, 0) < RECHECK:
        return have[1]
    _checked[theme] = now
    dirs = _theme_dirs(theme)
    sig = _sig(dirs)
    if have is None or have[0] != sig:
        pal = None
        if dirs:
            try:
                colors, tables = resolve(_toml(dirs, "colors.toml"))
                if "primary" in colors:
                    pal = Palette(theme, colors, tables)
            except (ValueError, TypeError, KeyError):
                pal = None
        _palettes[theme] = have = (sig, pal)
    return have[1]


def palette_for(group):
    """The palette to draw a group's models in: its world's, or None for the palette in
    force (VECTOR's own, the Drift's, the world in force, and everything under high contrast)."""
    if not group or group in (VECTOR, DRIFT):
        return None
    try:
        if T.high_contrast() or group == T.name():
            return None
    except Exception:
        return None
    return palette(group)


def primary(group):
    """A group's own primary colour (rgb floats): how its name is lit in the gallery's strip."""
    p = palette_for(group)
    return p.rgb("primary") if p is not None else T.rgb("primary")
