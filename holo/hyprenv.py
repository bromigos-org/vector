"""The live Hyprland instance, whoever started us. HYPRLAND_INSTANCE_SIGNATURE goes stale
when Hyprland restarts (a re-login) while a process launched from an old session, or from
systemd's user environment, keeps running; then every hyprctl call fails. live() finds the
instance whose socket answers and puts it in os.environ (cheap: a stat, then one connect
only when the current one is dead)."""
import os
import socket

RUN = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")


def _alive(sig):
    if not sig:
        return False
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(0.5)
    try:
        s.connect(os.path.join(RUN, "hypr", sig, ".socket.sock"))
        return True
    except OSError:
        return False
    finally:
        s.close()


def live():
    """-> the live signature (and os.environ updated to it), or None."""
    cur = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if _alive(cur):
        return cur
    base = os.path.join(RUN, "hypr")
    try:
        cands = sorted(os.listdir(base), key=lambda d: os.path.getmtime(os.path.join(base, d)), reverse=True)
    except OSError:
        return None
    for sig in cands:
        if _alive(sig):
            os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = sig
            return sig
    return None
