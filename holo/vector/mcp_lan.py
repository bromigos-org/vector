"""VECTOR's tools on the LAN, for his brain on Hermes (the homelab's Hermes deployment).

The one assistant is VECTOR: Hermes in the homelab is the brain (phone, schedules, background
work), this desktop is the body. The brain reaches the desktop's tools here: the same server
mcp_server.build() publishes (and whatever it publishes, it publishes), served as streamable
HTTP on the workstation's LAN address, behind a bearer token. Every call still goes through
tools.call(), so VECTOR's own limits hold whoever calls: the allowlists and argument checks,
the shell's refusals, no real money, Vault values never returned. The Hermes side adds its own
per-tool allowlist on top (homelab repo, helm/agents).

    ~/.local/share/bromigos/venv-brain/bin/python -m holo.vector.mcp_lan
    (bromigos-vector-mcp.service; http://<this host>:8765/mcp)

  token     ~/.local/share/bromigos/vector-mcp-token (mode 600); the same value is in Vault
            (the private notes say where), which Hermes reads through ESO. Without a token
            file the server refuses to start.
  bind      the address this machine uses to reach the LAN gateway (private overlay
            netmap.gateway.ip; VECTOR_MCP_HOST overrides), port 8765 (VECTOR_MCP_PORT).
            Never 0.0.0.0 or loopback.
  clients   the private overlay's vector_mcp.clients (CIDRs), else the /16 around the bind
            address (VECTOR_MCP_CLIENTS overrides, comma-separated); then the token.
Every request is logged (time, client, status; never the token or the body) to
~/.local/state/bromigos/vector-mcp.log; tool calls are in the usual vector-audit.log.
"""
import hmac
import ipaddress
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

from holo.private import PRIV  # noqa: E402
from holo.vector import mcp_server  # noqa: E402

TOKEN_FILE = os.path.expanduser("~/.local/share/bromigos/vector-mcp-token")
LOG = os.path.expanduser("~/.local/state/bromigos/vector-mcp.log")
PORT = int(os.environ.get("VECTOR_MCP_PORT", "8765"))


def host_names(host):
    """The names a client may use for this host (DNS-rebinding protection checks Host): the
    hostname, the LAN's reverse-DNS name for the bound address, and <short>.<lan.domain>."""
    short = socket.gethostname().split(".")[0]
    names = {short, socket.getfqdn()}
    try:
        name, aliases, _ = socket.gethostbyaddr(host)
        names.update([name, *aliases])
    except OSError:
        pass
    domain = PRIV.get("lan.domain", "")
    if domain:
        names.add(f"{short}.{domain}")
    return sorted(n for n in names if n)


def client_networks(host):
    raw = os.environ.get("VECTOR_MCP_CLIENTS") or PRIV.get("vector_mcp.clients", [])
    if isinstance(raw, str):
        raw = raw.split(",")
    nets = [ipaddress.ip_network(c.strip(), strict=False) for c in raw if c and c.strip()]
    return nets or [ipaddress.ip_network(f"{host}/16", strict=False)]


def lan_address():
    """The source address of the route to the LAN gateway: the LAN interface, not 0.0.0.0."""
    if os.environ.get("VECTOR_MCP_HOST"):
        return os.environ["VECTOR_MCP_HOST"]
    gateway = PRIV.get("netmap.gateway.ip", "")
    if not gateway:
        sys.exit("vector-mcp: no LAN gateway in the private overlay (netmap.gateway.ip); set VECTOR_MCP_HOST")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((gateway, 9))            # no packet is sent; this only picks the route
        return s.getsockname()[0]
    finally:
        s.close()


def log(**kw):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **kw}) + "\n")


class Gate:
    """ASGI middleware: client network, then a constant-time bearer check. Lifespan passes through."""

    def __init__(self, app, token, clients):
        self.app = app
        self.token = token.encode()
        self.clients = clients

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        client = (scope.get("client") or ("?", 0))[0]
        try:
            ok_net = any(ipaddress.ip_address(client) in n for n in self.clients)
        except ValueError:
            ok_net = False
        auth = dict(scope.get("headers") or []).get(b"authorization", b"")
        ok_tok = auth.startswith(b"Bearer ") and hmac.compare_digest(auth[7:].strip(), self.token)
        if not (ok_net and ok_tok):
            log(client=client, path=scope.get("path"), status=403 if not ok_net else 401)
            body = b'{"error": "forbidden"}'
            await send({"type": "http.response.start", "status": 403 if not ok_net else 401,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        log(client=client, path=scope.get("path"), method=scope.get("method"), status="pass")
        return await self.app(scope, receive, send)


def main():
    try:
        with open(TOKEN_FILE) as f:
            token = f.read().strip()
    except OSError:
        sys.exit(f"vector-mcp: no token at {TOKEN_FILE}; refusing to serve on the LAN without one")
    if len(token) < 32:
        sys.exit("vector-mcp: token too short")
    host = lan_address()
    if host in ("0.0.0.0", "::") or host.startswith("127."):
        sys.exit(f"vector-mcp: refusing to bind {host}; set VECTOR_MCP_HOST to the LAN address")
    allowed = [f"{h}:{PORT}" for h in (host, *host_names(host))]
    clients = client_networks(host)
    server = mcp_server.build()
    app = server.streamable_http_app(host=host, transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True, allowed_hosts=allowed, allowed_origins=[]))
    import uvicorn
    log(event="start", host=host, port=PORT, clients=[str(n) for n in clients])
    uvicorn.run(Gate(app, token, clients), host=host, port=PORT, log_level="warning", proxy_headers=False)


if __name__ == "__main__":
    main()
