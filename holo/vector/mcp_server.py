"""VECTOR's tools as an MCP server: the read tools for background workers (stdio, or
`--http PORT` on 127.0.0.1), plus ACT, his desktop actions, for his brain on Hermes (mcp_lan.py
serves build() on the LAN behind a token). `--readonly` or VECTOR_MCP_READONLY=1 publishes
the read tools alone.

The read tools: Desktop actions (launch, panel, wallpaper, scan,
notes_append), memory writes (remember, forget) and the UI tools (show_hologram,
open_gallery, set_voice) are not. Every call still goes through tools.call(), so the
allowlist, the argument checks and the audit log are the same as on the desktop.

    ~/.local/share/bromigos/venv-brain/bin/python -m holo.vector.mcp_server
"""
import functools
import inspect
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from holo.vector import tools  # noqa: E402

READ_ONLY = ["system_stats", "lab_status", "k8s_get", "k8s_logs", "k8s_events", "argocd_apps", "prometheus_query",
             "lab_vault_status",
             "github", "gnosis_search", "knowledge_search", "web_search", "web_fetch", "docs_search", "docs_read", "notes_read", "time_now", "calendar_month",
             "media_search", "media_status", "media_upcoming", "media_recent", "tv_channels",
             "claude_sessions", "claude_search"]
# Claude Code off the desktop: its sessions and a search, and claude_read as the latest view
# only (CLAUDE_LATEST: a fixed signature, latest=True, no tool output). Never here: its memory
# files and logs (claude_memory, claude_logs), reading aloud and follow mode.
DESKTOP_ONLY = {"tv_channel_create", "tv_tune", "tv_channel_delete","claude_memory", "claude_logs", "claude_say_latest", "claude_follow"}


# Desktop-local actions for VECTOR's brain on Hermes (served on the LAN by mcp_lan.py).
# Every one runs through tools.call(), so its limits are enforced inside the tool, whoever
# calls: the terminal's refusals (run_detached), vector-operator's RBAC and admission
# (k8s_*), Vault's policy and no values back (vault_*), the nolgia credit cap (an over-cap
# spend needs the host's answer on the desktop line, so over MCP it fails closed), the eyes'
# blocklist and in-memory captures. Not here: the terminal itself (run_shell), the desktop
# UI and voice tools, memory writes, and the build loop (it lives in the desktop daemon).
ACT = ["k8s_restart", "k8s_scale", "k8s_delete_pod", "k8s_run_job", "argocd_sync", "argocd_refresh", "argocd_wait",
       "ci_watch", "kb_write", "vault_list", "vault_put", "vault_copy", "app_search", "launch_app", "run_detached",
       "open_path", "windows", "window", "changes_check", "github_repo_create", "nolgia_catalog", "nolgia_credits",
       "nolgia_read", "nolgia_generate", "nolgia_review", "look", "read_screen_text", "active_window",
       "conversation_history", "my_setup", "load_skill", "hologram_deck", "snapshot_create", "snapshot_list",
       "snapshot_undo", "briefing_now", "quiet", "media_get", "media_language", "tv_channel_add_item", "tv_channel_remove_item"]
DESTRUCTIVE = {"k8s_delete_pod", "window", "vault_put", "vault_copy", "github_repo_create", "run_detached"}
READ_ALSO = {"vault_list", "app_search", "windows", "changes_check", "nolgia_catalog", "nolgia_credits", "nolgia_read",
             "nolgia_review", "look", "read_screen_text", "active_window", "conversation_history", "my_setup",
             "load_skill", "argocd_wait", "ci_watch", "snapshot_list", "briefing_now"}


def _wrap(name):
    fn = tools.FUNCS[name]
    desc = tools.SPECS[name][0]

    @functools.wraps(fn)
    def call(**kwargs):
        result, _ = tools.call(name, {k: v for k, v in kwargs.items() if v is not None})
        try:
            return json.loads(result)
        except ValueError:
            return result
    sig = inspect.signature(fn)
    if name == "system_stats":                 # its `live` parameter is the desktop's, not the caller's
        sig = sig.replace(parameters=[])
    call.__signature__ = sig
    call.__doc__ = desc
    return call, desc


def claude_latest(session: str = "", limit: int = 10):
    """The latest messages of a Claude Code session (redacted): session is an id or a few words
    ('the bromigOS one'); empty picks the live one. Off the desktop this is the only view:
    no paging back, no tool output."""
    result, _ = tools.call("claude_read", {"session": session or None, "limit": max(1, min(int(limit or 10), 20)),
                                           "latest": True})
    try:
        return json.loads(result)
    except ValueError:
        return result


def build(desktop=None):
    """desktop: also publish ACT (the LAN server for Hermes does; VECTOR_MCP_READONLY=1 turns it off).
    The stdio server for local background workers stays read-only unless asked."""
    if desktop is None:          # the LAN server (mcp_lan) publishes the actions; local stdio stays read-only
        desktop = ("holo.vector.mcp_lan" in sys.modules or __import__("__main__").__spec__ is not None
                   and getattr(__import__("__main__").__spec__, "name", "") == "holo.vector.mcp_lan") \
            and os.environ.get("VECTOR_MCP_READONLY") != "1" and "--readonly" not in sys.argv
    server = MCPServer("vector", instructions="VECTOR's tools on the operator's workstation: read views of the "
                                              "machine, the homelab, the user's own systems and the docs, and (on the LAN server) "
                                              "his desktop actions, each with its limits enforced inside the tool. "
                                              "Nothing here trades, arms, or returns a secret value.")
    from . import plugins
    own = [n for n, t in plugins.TOOLS.items() if t.get("mcp") and t["level"] == "read" and n not in READ_ONLY]
    for name in READ_ONLY + own:            # and the plugins' read tools that ask to be here (plugins.tool mcp=True)
        if name not in tools.FUNCS or name in DESKTOP_ONLY:   # the homelab's read tools come with bromigos-homelab
            continue
        fn, desc = _wrap(name)
        server.add_tool(fn, name=name, description=desc,
                        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    if "claude_read" in tools.FUNCS:
        server.add_tool(claude_latest, name="claude_read", description=claude_latest.__doc__,
                        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    if desktop:
        for name in ACT:
            if name not in tools.FUNCS:
                continue
            fn, desc = _wrap(name)
            server.add_tool(fn, name=name, description=desc,
                            annotations=ToolAnnotations(readOnlyHint=name in READ_ALSO, destructiveHint=name in DESTRUCTIVE,
                                                        openWorldHint=name.startswith(("nolgia", "github", "ci_"))))
    return server


if __name__ == "__main__":
    srv = build()
    if "--http" in sys.argv:
        import anyio
        port = int(sys.argv[sys.argv.index("--http") + 1])
        anyio.run(lambda: srv.run_streamable_http_async(host="127.0.0.1", port=port))
    else:
        srv.run()
