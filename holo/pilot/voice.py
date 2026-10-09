"""Compatibility alias for holo.vector.voice (PILOT is now VECTOR). The live layer's codec
calls come through here, so a plain `tts` request speaks in the notify voice."""
from ..vector.voice import *  # noqa: F401,F403
from ..vector.voice import Voice as _Voice


class Voice(_Voice):
    def _req(self, obj, timeout=30.0):
        if obj.get("op") in ("tts", "tts_stream") and "role" not in obj:
            obj = dict(obj, role="notify")
        return super()._req(obj, timeout)
