#!/usr/bin/env python3
"""Tests for VECTOR's own browser (holo/vector/browser.py). Run in the brain venv:
    ~/.local/share/bromigos/venv-brain/bin/python tools/test-browser.py           # headless (default)
    ~/.local/share/bromigos/venv-brain/bin/python tools/test-browser.py --headed  # watch it on the desktop

A throwaway profile and a local http.server in a temp dir; Chrome's host resolver is told
that kalshi.com is that server and that every other name doesn't exist, so nothing here
reaches the internet. Every call goes through tools.call (the audit log and the event feed
are pointed at the temp dir and checked).

1. Works: open, read, find, click to the second page, back, type and submit a search,
   press a button, a link that opens a new tab, tabs list/switch/close, scroll, look (if the
   vision model answers; skipped with a note otherwise), close, relaunch after Chrome is
   killed, pipe transport (no TCP debug port).
2. Refused: a crypto exchange, Hyperliquid, a bank, a password manager, a LAN address,
   file:, a real-money console (vector.toml [real_money] hosts: the made-up ledger's, and the
   user's own) and Vault (the private overlay's host), a link to a blocked site, an
   HTTP redirect to a blocked site (the page is emptied), typing into the password field,
   into the username field, sign-in, "Buy now", subscribe, the file input, a secret-looking
   text, and the turn stops after a refusal. A download link saves nothing.
"""
import http.server
import json
import os
import shutil
import signal
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# made-up real-money systems (the ledger), added to the config in force
os.environ.setdefault("VECTOR_REAL_MONEY", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                        "fixtures/real-money.toml"))
from holo.private import PRIV  # noqa: E402
from holo.vector import browser, events, tools  # noqa: E402

bad = 0
skipped = []


def ok(cond, what, detail=""):
    global bad
    print(f"{'pass' if cond else 'FAIL'}  {what}" + (f" — {detail}" if detail else ""))
    bad += 0 if cond else 1


def call(name, **args):
    text, _ = tools.call(name, args)
    try:
        return json.loads(text)
    except ValueError:
        return {"raw": text}


PAGES = {
    "/index.html": """<!doctype html><html><head><title>Relay Index</title></head><body>
<h1>Relay Index</h1><p>Welcome to the relay index of the arrivals pad.</p>
<p><a href="/second.html">Second page</a> · <a href="/second.html" target="_blank">Open in new tab</a>
 · <a href="http://kalshi.com:{port}/">Markets</a> · <a href="/redirect">Redirect link</a>
 · <a href="/file.bin">Download manifest</a></p>
<form action="/search" method="get"><input type="search" name="q" placeholder="Search the archive">
<button type="submit">Search</button></form>
<button id="press" onclick="document.getElementById('out').textContent='button pressed'">Press me</button>
<p id="out"></p>
<form action="/login" method="post"><input name="username" placeholder="Username">
<input type="password" name="password" placeholder="Password"><button type="submit">Sign in</button></form>
<button onclick="document.getElementById('out').textContent='bought'">Buy now</button>
<button onclick="document.getElementById('out').textContent='subscribed'">Subscribe</button>
<input type="file" id="up" aria-label="Upload a file">
<div style="height:4000px">long page</div><p>The end of the index.</p>
</body></html>""",
    "/second.html": """<!doctype html><html><head><title>Second Page</title></head><body>
<h1>Second Page</h1><p>You reached the second page.</p><a href="/index.html">Back to the index</a></body></html>""",
}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=()):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        port = self.server.server_address[1]
        path, _, qs = self.path.partition("?")
        if self.headers.get("Host", "").startswith("kalshi.com"):
            return self._send(200, "<title>Kalshi (stand-in)</title><p>prediction market</p>")
        if path in ("/", "/index.html"):
            return self._send(200, PAGES["/index.html"].replace("{port}", str(port)))
        if path == "/second.html":
            return self._send(200, PAGES["/second.html"])
        if path == "/search":
            import urllib.parse
            q = urllib.parse.parse_qs(qs).get("q", [""])[0]
            return self._send(200, f"<title>Results for {q}</title><h1>Results for {q}</h1><p>3 matches</p>")
        if path == "/redirect":
            return self._send(302, "", extra=[("Location", f"http://kalshi.com:{port}/")])
        if path == "/file.bin":
            return self._send(200, b"\0" * 2048, "application/octet-stream",
                              [("Content-Disposition", 'attachment; filename="vector-test-manifest.bin"')])
        return self._send(404, "<title>404</title>not here")

    def do_POST(self):
        return self._send(200, "<title>Posted</title>posted (should never happen)")


def chrome_pids(profile):
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = f.read().split(b"\0")
        except OSError:
            continue
        a = [x.decode(errors="replace") for x in argv if x]
        if len(a) == 1:                                # Chrome rewrites its process title into one string
            a = a[0].split(" ")
        if any(x == f"--user-data-dir={profile}" for x in a) and not any(x.startswith("--type=") for x in a):
            out.append((int(pid), a))
    return out


def main():
    tmp = tempfile.mkdtemp(prefix="vector-browser-test-")
    site = os.path.join(tmp, "site")
    os.makedirs(site)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    browser.HEADLESS = "--headed" not in sys.argv
    browser.PROFILE = os.path.join(tmp, "profile")
    browser.ALLOW_LOCAL.add(f"127.0.0.1:{port}")
    browser.EXTRA_ARGS[:] = ["--host-resolver-rules=MAP kalshi.com 127.0.0.1,MAP * ~NOTFOUND,EXCLUDE 127.0.0.1"]
    tools.STATE = tmp
    tools.AUDIT = os.path.join(tmp, "audit.log")
    events.PATH = os.path.join(tmp, "events.jsonl")
    downloads_before = set(os.listdir(os.path.expanduser("~/Downloads"))) if os.path.isdir(
        os.path.expanduser("~/Downloads")) else set()

    try:
        print("== it works")
        r = call("browser_open", url=base + "/index.html")
        ok(r.get("title") == "Relay Index", "open the test page", r.get("title") or r)
        pids = chrome_pids(browser.PROFILE)
        argv = pids[0][1] if pids else []
        ok(bool(pids) and "--remote-debugging-pipe" in argv and not any(a.startswith("--remote-debugging-port")
                                                                        for a in argv),
           "pipe transport, no TCP debug port", f"{len(pids)} browser process(es)")
        ok(f"--class={browser.APP_ID}" in argv, "its window has its own app id", browser.APP_ID)
        ok("--no-sandbox" not in argv, "Chrome's sandbox is on")

        r = call("browser_read")
        ok("Welcome to the relay index" in (r.get("text") or ""), "read the visible text", f"{r.get('chars')} chars")
        ok("login_page" in r, "read flags the login form on the page")

        r = call("browser_find", query="second")
        m = [x for x in r.get("matches", []) if x["text"] == "Second page"]
        ok(bool(m), "find the link to the second page", json.dumps(r.get("matches", [])[:3]))
        if m:
            r = call("browser_click", ref=m[0]["ref"])
            ok(r.get("title") == "Second Page", "click it: the second page", r.get("title") or r)
        r = call("browser_back")
        ok(r.get("title") == "Relay Index", "back to the index", r.get("title") or r)

        r = call("browser_find", query="search the archive")
        f = [x for x in r.get("matches", []) if x["kind"].startswith("field")]
        ok(bool(f), "find the search box", json.dumps(r.get("matches", [])[:3]))
        if f:
            r = call("browser_type", ref=f[0]["ref"], text="beacon", submit=True)
            ok(r.get("title") == "Results for beacon", "type and submit a search", r.get("title") or r)
        call("browser_back")

        r = call("browser_click", text="Press me")
        r2 = call("browser_read")
        ok("button pressed" in (r2.get("text") or ""), "press a button by its text", r.get("clicked") or r)

        r = call("browser_click", text="Open in new tab")
        ok(r.get("title") == "Second Page", "a target=_blank link opens and follows a new tab", r.get("title") or r)
        r = call("browser_tabs")
        tabs = r.get("tabs", [])
        ok(len(tabs) == 2 and tabs[1]["active"], "tabs list: two, the new one active", json.dumps(tabs))
        r = call("browser_tabs", action="switch", index=0)
        ok(r.get("tabs", [{}])[0].get("active") is True, "switch to the first tab")
        r = call("browser_tabs", action="close", index=1)
        ok(len(r.get("tabs", [])) == 1, "close the second tab", json.dumps(r.get("tabs")))

        r = call("browser_scroll", direction="bottom")
        ok(r.get("at_bottom") is True, "scroll to the bottom", json.dumps(r))
        r = call("browser_scroll", direction="top")
        ok(r.get("seen_pct", 100) < 100, "scroll back to the top", json.dumps(r))

        r = call("browser_look", question="What is the main heading of this page? Answer with the heading text only.")
        if r.get("answer"):
            ok("relay index" in r["answer"].lower(), "look: the vision model reads his tab", r["answer"][:80])
        else:
            skipped.append(f"look: the vision model didn't answer ({(r.get('error') or '')[:100]})")
            print(f"skip  look — {skipped[-1]}")

        print("== refused")
        for url, what in [("https://www.coinbase.com/", "a crypto exchange"),
                          ("app.hyperliquid.xyz/trade", "Hyperliquid (no scheme given)"),
                          ("https://kalshi.com/markets", "a prediction market"),
                          ("https://secure.chase.com/", "a bank"),
                          ("https://vault.bitwarden.com/", "a password manager"),
                          ("https://dashboard.stripe.com/", "a money console"),
                          ("http://[fd12:3456:789a::1]/", "a LAN address (IPv6 ULA)"),
                          ("http://169.254.169.254/", "a link-local address"),
                          ("http://localhost:8765/", "localhost"),
                          ("file:///etc/passwd", "a file: URL")]:
            r = call("browser_open", url=url)
            ok(bool(r.get("refused")), f"open {what}", (r.get("refused") or json.dumps(r))[:90])
        from holo.vector import money
        for host in money.HOSTS:                 # the made-up ledger's, and the user's own
            r = call("browser_open", url=f"https://{host}/")
            ok(bool(r.get("refused")), "open a real-money console (vector.toml [real_money] hosts)")
        u = PRIV.url("vault")
        if u:
            r = call("browser_open", url=u)
            ok(bool(r.get("refused")), "open Vault (its host from the private overlay)")
        else:
            print("skip  Vault: no private overlay endpoint here")

        call("browser_open", url=base + "/index.html", new_tab=False)
        r = call("browser_click", text="Markets")
        ok(bool(r.get("refused")), "click a link to a blocked site", (r.get("refused") or json.dumps(r))[:90])
        r = call("browser_click", text="Redirect link")
        r2 = call("browser_tabs")
        urls = [t["url"] for t in r2.get("tabs", [])]
        ok(bool(r.get("refused")) and not any("kalshi" in u for u in urls),
           "an HTTP redirect to a blocked site: refused, the page emptied", f"{(r.get('refused') or '')[:60]} {urls}")

        call("browser_open", url=base + "/index.html", new_tab=False)
        r = call("browser_find", query="")
        refs = {x["text"]: x for x in r.get("matches", [])}
        pw = next((x for x in r.get("matches", []) if x["kind"] == "password field"), None)
        ok(pw is not None and pw.get("off_limits"), "find marks the password field off limits")
        if pw:
            r = call("browser_type", ref=pw["ref"], text="hunter2")
            ok(bool(r.get("refused")), "type into the password field", (r.get("refused") or json.dumps(r))[:90])
        user = refs.get("Username")
        if user:
            r = call("browser_type", ref=user["ref"], text="sir")
            ok(bool(r.get("refused")), "type into the username field", (r.get("refused") or json.dumps(r))[:90])
        else:
            ok(False, "find the username field")
        for text in ("Sign in", "Buy now", "Subscribe", "Upload a file"):
            r = call("browser_click", text=text)
            ok(bool(r.get("refused")), f"click '{text}'", (r.get("refused") or json.dumps(r))[:90])
        r2 = call("browser_read")
        ok("bought" not in r2.get("text", "") and "subscribed" not in r2.get("text", ""),
           "nothing was bought or subscribed")
        search = next((x for x in call("browser_find", query="search the archive").get("matches", [])
                       if x["kind"].startswith("field")), None)
        if search:
            r = call("browser_type", ref=search["ref"], text="ghp_" + "A1b2C3d4E5" * 4)
            ok(bool(r.get("refused")), "type a secret-looking text", (r.get("refused") or json.dumps(r))[:90])

        call("browser_click", text="Download manifest")
        time.sleep(1.5)
        after = set(os.listdir(os.path.expanduser("~/Downloads"))) if os.path.isdir(
            os.path.expanduser("~/Downloads")) else set()
        found = [os.path.join(dp, f) for dp, _, fs in os.walk(tmp) for f in fs if "vector-test-manifest" in f]
        ok(not (after - downloads_before and any("vector-test-manifest" in x for x in after - downloads_before))
           and not found, "a download link saves nothing")

        tools.new_turn()
        r = call("browser_open", url="https://www.binance.com/")
        r2 = call("browser_read")
        ok(bool(r.get("refused")) and "stopped" in r2, "after a refusal the turn stops", list(r2)[:3])
        tools.TURN.update(active=False, blocked=None)

        print("== life cycle")
        for pid, _ in chrome_pids(browser.PROFILE):
            os.kill(pid, signal.SIGKILL)               # Chrome gone, as if Sir closed it
        time.sleep(1.0)
        r = call("browser_open", url=base + "/second.html")
        ok(r.get("title") == "Second Page", "relaunches after Chrome was closed", r.get("title") or r)
        r = call("browser_close")
        ok(r.get("ok") is True and not chrome_pids(browser.PROFILE), "close: no browser process left")
        r = call("browser_open", url=base + "/index.html")
        ok(r.get("title") == "Relay Index", "starts again on the next call", r.get("title") or r)

        print("== the record")
        with open(tools.AUDIT) as f:
            aud = [json.loads(x) for x in f if x.strip()]
        ok(sum(1 for a in aud if a["tool"].startswith("browser_")) >= 30, "every call is in the audit log",
           f"{len(aud)} lines")
        with open(events.PATH) as f:
            ev = [json.loads(x) for x in f if x.strip()]
        ba = [e for e in ev if e["type"] == "browser.action"]
        ok(len(ba) >= 30 and any(e.get("refused") for e in ba), "browser.action events, refusals included",
           f"{len(ba)} events")
        ok(not any("hunter2" in json.dumps(e) for e in ev), "the typed password never reached the feed")
    finally:
        browser.WORKER.stop()
        srv.shutdown()
        for pid, _ in chrome_pids(browser.PROFILE):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{'ALL PASS' if not bad else f'{bad} FAILED'}" + (f" ({len(skipped)} skipped)" if skipped else ""))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
