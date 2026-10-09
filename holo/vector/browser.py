"""VECTOR's own browser: a separate Chrome, with its own profile, that he drives himself
through Playwright. It is not Sir's Chrome: Sir's profile, tabs and sign-ins are never
touched (a different user-data-dir is a different Chrome process; nothing here reads or
writes Sir's profile).

  browser_open(url, new_tab)        open a page (a new tab by default)
  browser_tabs(action, index)       list | switch | close
  browser_read(max_chars)           title, url and the visible text
  browser_find(query)               links, buttons and fields matching the words, with refs
  browser_click(ref | text)         click one
  browser_type(ref, text, submit)   type into a field (Enter to submit)
  browser_scroll(direction)         down | up | top | bottom
  browser_back()                    the previous page
  browser_look(question)            a screenshot of HIS tab to the lab's vision model (eyes._ask)
  browser_close()                   close his browser (the next call starts it again)

How it runs:
  * One persistent Chrome (/usr/bin/google-chrome-stable) on ~/.local/share/bromigos/vector-browser,
    over Playwright's pipe transport: no --remote-debugging-port, so no TCP debug port.
  * Visible, so Sir can watch: its window has the app id / class `vector-browser`.
  * Playwright's sync API isn't thread-safe, so one worker thread owns the browser and every
    tool call is a job on its queue, with a timeout. It starts on first use, relaunches if
    Sir closed it, and closes with the daemon (atexit).

Limits, enforced here (not by the prompt):
  * No sensitive sites: banking and payments, brokerages, crypto exchanges, wallets and
    prediction markets, password managers, Vault, admin consoles for money (BLOCKED_SITES,
    plus eyes' blocklist patterns on the host name, plus the private overlay's Vault host
    and the consoles of the user's real-money systems, vector.toml [real_money] hosts). Checked before every navigation, on every request the pages make (a
    context route), and after every action (an HTTP redirect skips the route).
  * No LAN hosts except the lab's own domain (reach._allowed_url, as web_fetch).
  * No logins: never types into a password, one-time-code or credential field, nor into
    any field of a form that has a password field, and never submits such a form. A page
    that asks for a login is reported, not signed into.
  * No purchases or payments: refuses clicking anything whose text, label, role or form
    reads checkout, place order, buy, pay, subscribe, confirm payment, add to cart, donate,
    upgrade, start a trial; never types into card or bank fields.
  * No downloads (accept_downloads=False), no uploads (file choosers are swallowed and
    file inputs refused), dialogs dismissed, no secret-looking text typed.
Refusals come back as {"refused": why}, so tools.call stops the turn; every call is in the
audit log (tools.call) and on the event feed (browser.action).
"""
import atexit
import io
import ipaddress
import os
import queue
import re
import threading
import time
import urllib.parse

HOME = os.path.expanduser("~")
PROFILE = os.path.join(HOME, ".local/share/bromigos/vector-browser")
CHROME = "/usr/bin/google-chrome-stable"
APP_ID = "vector-browser"
HEADLESS = os.environ.get("VECTOR_BROWSER_HEADLESS") == "1"   # tests only; his browser is visible
EXTRA_ARGS = []              # tests only (e.g. --host-resolver-rules); never set by a tool
ALLOW_LOCAL = set()          # "127.0.0.1:port" the tests serve on; never set by a tool
CALL_TIMEOUT = 45
LOOK_TIMEOUT = 120

# ------------------------------------------------------------------ the blocklist
# One list for every sensitive site category (eyes' window-title patterns are applied to
# the host name as well). An entry is a domain (and its subdomains) or domain/path-prefix.
BLOCKED_SITES = {
    "banking and payments": [
        "chase.com", "bankofamerica.com", "wellsfargo.com", "citi.com", "citibank.com", "usbank.com", "capitalone.com",
        "pnc.com", "ally.com", "discover.com", "americanexpress.com", "sofi.com", "chime.com", "truist.com", "td.com",
        "tdbank.com", "navyfederal.org", "usaa.com", "hsbc.com", "barclays.co.uk", "santander.com", "becu.org",
        "paypal.com", "venmo.com", "cash.app", "squareup.com", "zellepay.com", "wise.com", "revolut.com",
        "pay.google.com", "payments.google.com", "wallet.google.com", "plaid.com", "mint.intuit.com",
        "creditkarma.com", "experian.com", "equifax.com", "transunion.com", "irs.gov", "treasurydirect.gov"],
    "brokerages": [
        "schwab.com", "fidelity.com", "vanguard.com", "etrade.com", "robinhood.com", "tdameritrade.com",
        "interactivebrokers.com", "ibkr.com", "webull.com", "merrilledge.com", "wealthfront.com", "betterment.com",
        "public.com", "tastytrade.com", "firstrade.com", "m1.com", "alpaca.markets", "app.alpaca.markets",
        "tradestation.com", "ninjatrader.com", "tradovate.com"],
    "crypto exchanges, wallets and prediction markets": [
        "coinbase.com", "kraken.com", "binance.com", "binance.us", "gemini.com", "crypto.com", "kucoin.com", "okx.com",
        "bybit.com", "bitstamp.net", "bitfinex.com", "bitget.com", "mexc.com", "gate.io", "htx.com", "upbit.com",
        "hyperliquid.xyz", "hyperliquid.com", "kalshi.com", "polymarket.com", "manifold.markets", "predictit.org",
        "metamask.io", "phantom.app", "phantom.com", "ledger.com", "trezor.io", "exodus.com", "trustwallet.com",
        "rabby.io", "rainbow.me", "zerion.io", "blockchain.com", "uniswap.org", "app.uniswap.org", "jup.ag",
        "raydium.io", "pump.fun", "dydx.exchange", "dydx.trade", "drift.trade", "gmx.io", "aave.com", "app.aave.com",
        "curve.fi", "1inch.io", "opensea.io", "magiceden.io", "solflare.com", "backpack.exchange", "moonpay.com"],
    "password managers": [
        "bitwarden.com", "vault.bitwarden.com", "1password.com", "lastpass.com", "keepersecurity.com", "dashlane.com",
        "pass.proton.me", "account.proton.me", "nordpass.com", "roboform.com", "enpass.io", "keeper.io"],
    "Vault and secrets": ["portal.cloud.hashicorp.com", "app.hashicorp.com", "secretsmanager.console.aws.amazon.com"],
    "admin consoles for money": [
        "dashboard.stripe.com", "connect.stripe.com", "billing.stripe.com", "checkout.stripe.com", "appstoreconnect.apple.com",
        "play.google.com/console", "console.cloud.google.com/billing", "console.aws.amazon.com/billing",
        "portal.azure.com", "admin.shopify.com", "squareup.com/dashboard", "app.gusto.com", "quickbooks.intuit.com"],
}
# a host name that says what it is (mybank.example, credit-union.org, a wallet subdomain, a vault host)
HOST_WORDS = re.compile(r"(^|[.-])(bank|banking|creditunion|credit-union|fcu|wallet|vault|brokerage|trading|exchange)"
                        r"([.-]|$)|bank\.|wallet\.", re.I)
LOCAL_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".localhost")

_private_hosts = None


def _private_blocked_hosts():
    """Vault from the private overlay, and the consoles of the user's real-money systems
    (vector.toml [real_money] hosts, money.py): never written in this repo."""
    global _private_hosts
    if _private_hosts is None:
        out = set()
        try:
            from ..private import PRIV
            h = urllib.parse.urlparse(PRIV.url("vault") or "").hostname
            if h:
                out.add(h.lower())
        except Exception:
            pass
        try:
            from . import money
            out.update(money.HOSTS)
        except Exception:
            pass
        _private_hosts = out
    return _private_hosts


_eyes_cache = {"t": -1e9, "pats": []}


def _eyes_patterns():
    """eyes' blocklist patterns (its defaults plus eyes.json), re-read once a minute."""
    if time.monotonic() - _eyes_cache["t"] > 60:
        try:
            from . import eyes
            _eyes_cache["pats"] = eyes._conf()["titles"]
        except Exception:
            pass
        _eyes_cache["t"] = time.monotonic()
    return _eyes_cache["pats"]


def site_blocked(url):
    """-> why a URL is off limits as a sensitive site, or None (no network involved)."""
    u = urllib.parse.urlparse(url)
    if u.scheme in ("about", "data", "blob") and url.startswith(("about:blank", "data:", "blob:")):
        return None
    if u.scheme not in ("http", "https"):
        return f"{u.scheme or 'that'}: URLs are off limits (http and https only)"
    host = (u.hostname or "").lower().rstrip(".")
    if not host:
        return "no host in that URL"
    path = u.path or "/"
    for cat, entries in BLOCKED_SITES.items():
        for e in entries:
            d, _, p = e.partition("/")
            if (host == d or host.endswith("." + d)) and (not p or path.startswith("/" + p)):
                return f"{host} is {cat}: off limits for your browser"
    if host in _private_blocked_hosts():
        return f"{host} is a real-money console or Vault: off limits for your browser"
    if HOST_WORDS.search(host):
        return f"{host} looks like banking, a wallet, an exchange or Vault: off limits for your browser"
    for rx in _eyes_patterns():
        try:
            if re.search(rx, host, re.I):
                return f"{host} is on the eyes blocklist (banking, trading, wallets, Vault, passwords)"
        except re.error:
            continue
    return None


_dns_cache = {}


def url_blocked(url, navigation=True):
    """-> why VECTOR's browser may not load this URL, or None. Navigations get the full LAN
    check (DNS included, cached a few minutes); sub-resources the cheap one."""
    why = site_blocked(url)
    if why:
        return why
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https"):
        return None                                   # about:blank, data:, blob:
    host = (u.hostname or "").lower()
    if u.netloc.lower() in ALLOW_LOCAL:
        return None
    from .reach import LAB_DOMAIN
    if LAB_DOMAIN and (host == LAB_DOMAIN or host.endswith("." + LAB_DOMAIN)):
        return None
    if host == "localhost" or host.endswith(LOCAL_SUFFIXES):
        return "LAN-only hosts are off limits (except the lab's own domain)"
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return "LAN and loopback addresses are off limits (except the lab's own domain)"
        return None
    except ValueError:
        pass
    if not navigation:
        return None
    hit = _dns_cache.get(host)
    if hit and time.monotonic() - hit[0] < 300:
        return hit[1]
    from .reach import _allowed_url
    why = _allowed_url(f"{u.scheme}://{host}/")
    if why == "can't resolve that host":
        why = None                                    # Chrome will say so itself; not a safety matter
    _dns_cache[host] = (time.monotonic(), why)
    return why


# ------------------------------------------------------------------ what may be clicked or typed
BUY = re.compile(
    r"\b(check ?out|place (your |my |the )?order|order now|buy( it| now)?|purchase|pay( now| with| securely)?|payment|"
    r"subscribe|subscription|confirm (and |& )?pay|complete (purchase|order|payment|checkout)|add to (cart|bag|basket)|"
    r"donate|upgrade( now| plan| to)?|start (my |your )?(free )?trial|book now|reserve now|send money|transfer funds|"
    r"withdraw|deposit|place (bet|trade|wager)|trade now|go pro|get premium)\b", re.I)
BUY_PATH = re.compile(r"/(checkout|payment|payments|pay|billing|subscribe|subscription|purchase|order|cart)(/|$|\?)", re.I)
CREDENTIAL = re.compile(r"passw|passcode|\bpin\b|one-time-code|otp|2fa|mfa|totp|security.?code|secret|api.?key|token",
                        re.I)
LOGIN_FIELD = re.compile(r"\b(username|user.?name|userid|user.?id|login|log.?in|sign.?in|identifier|account.?name)\b|"
                         r"identifierid|^email$", re.I)
PAYMENT = re.compile(r"cc-|card.?num|cardnumber|\bcvc|\bcvv|csc|card.?exp|expir|iban|routing|account.?num|\bssn\b|"
                     r"social.?security|tax.?id|bank", re.I)

_DESCRIBE = r"""el => {
  const t = s => (s || '').replace(/\s+/g, ' ').trim();
  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute('type') || (tag === 'button' ? 'submit' : '')).toLowerCase();
  const r = el.getBoundingClientRect(), st = getComputedStyle(el);
  const visible = r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none' && type !== 'hidden';
  const label = t(el.getAttribute('aria-label')) || (el.labels && el.labels[0] ? t(el.labels[0].innerText) : '');
  const text = t(el.innerText || el.textContent).slice(0, 160) || t(el.value).slice(0, 80)
               || t(el.getAttribute('alt')) || t((el.querySelector('img') || {}).alt);
  const f = el.form || el.closest('form');
  const fields = f ? [...f.querySelectorAll('input,textarea,select')] : [];
  const attrs = i => [i.getAttribute('autocomplete'), i.name, i.id, i.getAttribute('placeholder'),
                      i.getAttribute('aria-label')].filter(Boolean).join(' ');
  return {tag, type, role: (el.getAttribute('role') || '').toLowerCase(), text, label,
          placeholder: t(el.getAttribute('placeholder')), title: t(el.getAttribute('title')),
          name: el.getAttribute('name') || '', id: el.id || '', autocomplete: el.getAttribute('autocomplete') || '',
          href: tag === 'a' ? (el.href || '') : '', target: (el.getAttribute('target') || '').toLowerCase(), editable: !!el.isContentEditable, visible,
          disabled: !!el.disabled,
          form: f ? {action: f.action || '', method: (f.method || '').toLowerCase(),
                     password: fields.some(i => (i.type || '').toLowerCase() === 'password'),
                     fieldattrs: fields.map(attrs).join(' | ').slice(0, 2000),
                     buttons: [...f.querySelectorAll('button,input[type=submit],input[type=image],[role=button]')]
                              .map(b => t(b.innerText || b.value || b.getAttribute('aria-label'))).join(' | ').slice(0, 600)}
                  : null};
}"""
INTERACTIVE = ("a[href], button, input:not([type=hidden]), textarea, select, summary, [role=button], [role=link], "
               "[role=textbox], [role=searchbox], [role=combobox], [role=checkbox], [role=tab], [role=menuitem], "
               "[role=option], [role=switch], [contenteditable=''], [contenteditable=true]")


def _kind(d):
    if d["tag"] == "a" or d["role"] == "link":
        return "link"
    if d["tag"] == "input":
        if d["type"] in ("submit", "button", "image", "reset"):
            return "button"
        return "password field" if d["type"] == "password" else f"field:{d['type'] or 'text'}"
    if d["tag"] in ("textarea",) or d["editable"] or d["role"] in ("textbox", "searchbox", "combobox"):
        return "field:text"
    if d["tag"] == "select":
        return "select"
    return d["role"] or ("button" if d["tag"] in ("button", "summary") else d["tag"])


def _words_of(d):
    return " ".join(x for x in (d["text"], d["label"], d["placeholder"], d["title"]) if x)


def _is_credential_field(d):
    if d["type"] == "password":
        return True
    return bool(CREDENTIAL.search(" ".join((d["autocomplete"], d["name"], d["id"], d["label"], d["placeholder"]))))


def _is_payment_field(d):
    return bool(PAYMENT.search(" ".join((d["autocomplete"], d["name"], d["id"], d["label"], d["placeholder"]))))


SIGN_IN = re.compile(r"\b(sign ?in|log ?in|sign ?up|register|create (an )?account|continue|next|submit|verify)\b", re.I)


def _click_refusal(d, page_login=False):
    words = _words_of(d) + " " + d.get("name", "")
    if page_login and SIGN_IN.search(words) and _kind(d) == "button":
        return "that would send a login form; you never sign in. Tell Sir the site needs his login."
    if d["tag"] == "input" and d["type"] == "file":
        return "uploads are off in your browser (no file inputs)"
    if d["type"] == "password":
        return "that's a password field; you never sign in anywhere"
    if BUY.search(words):
        return f"'{_words_of(d)[:60]}' looks like a purchase, payment or subscription; you never buy or pay"
    if d["href"]:
        why = site_blocked(d["href"]) if not d["href"].startswith("javascript:") else None
        if why:
            return why
        if BUY_PATH.search(urllib.parse.urlparse(d["href"]).path or ""):
            return "that link goes to a checkout, payment or billing page; you never buy or pay"
    f = d.get("form")
    submits = d["tag"] == "button" and d["type"] == "submit" or d["tag"] == "input" and d["type"] in ("submit", "image")
    if f and submits:
        if f["password"]:
            return "that submits a login form; you never sign in. Tell Sir the site needs his login."
        if PAYMENT.search(f["fieldattrs"]) or BUY_PATH.search(urllib.parse.urlparse(f["action"]).path or ""):
            return "that submits a payment or checkout form; you never buy or pay"
    return None


def _is_login_field(d, page_login):
    """A username or email field: on a page with a password field, or plainly a login or
    sign-up identifier (autocomplete username/email, an email input)."""
    attrs = " ".join((d["autocomplete"], d["name"], d["id"], d["label"], d["placeholder"]))
    if d["tag"] == "input" and d["type"] == "email" or re.search(r"\b(username|email)\b", d["autocomplete"], re.I):
        return True
    if LOGIN_FIELD.search(attrs) or LOGIN_FIELD.search(d["name"]) or LOGIN_FIELD.search(d["id"]):
        return True
    return bool(page_login and re.search(r"user|e-?mail|login|account|phone", attrs, re.I))


def _type_refusal(d, text, page_login=False):
    if d["tag"] == "input" and d["type"] in ("file",):
        return "uploads are off in your browser"
    if d["tag"] == "input" and d["type"] in ("submit", "button", "image", "reset", "checkbox", "radio", "range", "color"):
        return f"that's a {d['type']} control, not a text field (use browser_click)"
    if d["tag"] not in ("input", "textarea", "select") and not d["editable"] and d["role"] not in (
            "textbox", "searchbox", "combobox"):
        return "that isn't a text field (use browser_find to get a field's ref)"
    if _is_credential_field(d):
        return "that's a password or credential field; you never type into one. Tell Sir the site needs his login."
    if _is_payment_field(d):
        return "that's a card, bank or identity field; you never enter payment or identity details"
    f = d.get("form")
    if f and f["password"]:
        return "that field is part of a login form; you never sign in. Tell Sir the site needs his login."
    if _is_login_field(d, page_login):
        return ("that's a login or sign-up field (a username or email); you never sign in or sign up anywhere. "
                "Tell Sir the site needs his login.")
    if f and PAYMENT.search(f["fieldattrs"]):
        return "that field is part of a payment form; you never buy or pay"
    try:
        from .shell import redact
        if redact(text) != text:
            return "that text looks like a secret (a key, token or password); you never type secrets"
    except Exception:
        pass
    return None


def _submit_refusal(d):
    f = d.get("form")
    if not f:
        return None
    if f["password"]:
        return "that would submit a login form; you never sign in"
    if PAYMENT.search(f["fieldattrs"]) or BUY_PATH.search(urllib.parse.urlparse(f["action"]).path or ""):
        return "that would submit a payment or checkout form; you never buy or pay"
    if BUY.search(f["buttons"]):
        return f"that form's button reads '{f['buttons'][:60]}': a purchase or subscription; you never buy or pay"
    return None


# ------------------------------------------------------------------ the worker
class _Browser:
    """Owns Playwright and the browser; every method runs on the worker thread only."""

    def __init__(self):
        self.pw = None
        self.ctx = None
        self.active = None
        self.refs = {}
        self.refs_page = None
        self.blocked = None          # (url, why) the route refused during the last action
        self.new_pages = []

    # ---- life cycle
    def _launch(self):
        from playwright.sync_api import sync_playwright
        if self.pw is None:
            self.pw = sync_playwright().start()
        os.makedirs(PROFILE, exist_ok=True)
        kw = dict(user_data_dir=PROFILE, executable_path=CHROME, headless=HEADLESS, accept_downloads=False,
                  chromium_sandbox=True,                  # Playwright turns Chrome's sandbox off by default
                  service_workers="block", permissions=[], args=[f"--class={APP_ID}", "--no-default-browser-check",
                                                                 "--disable-features=PasswordManagerOnboarding"]
                  + list(EXTRA_ARGS))
        if HEADLESS:
            kw["viewport"] = {"width": 1280, "height": 900}
        else:
            kw["no_viewport"] = True                      # the page follows the window Sir sees
        self.ctx = self.pw.chromium.launch_persistent_context(**kw)
        self.ctx.route("**/*", self._route)
        self.ctx.on("page", self._on_page)
        self.ctx.on("close", lambda *_: setattr(self, "ctx", None))
        for p in self.ctx.pages:
            self._wire(p)
        self.active = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.new_pages = []
        self.refs, self.refs_page = {}, None

    def _wire(self, page):
        page.on("filechooser", lambda fc: None)          # intercepted and never filled: no uploads
        page.on("dialog", lambda dlg: _quiet(dlg.dismiss))
        page.on("download", lambda dl: _quiet(dl.cancel))

    def _on_page(self, page):
        self._wire(page)
        self.new_pages.append(page)

    def _route(self, route, request):
        why = url_blocked(request.url, navigation=request.is_navigation_request())
        if why:
            if request.is_navigation_request() and _quiet(lambda: request.frame.parent_frame is None):
                self.blocked = (request.url, why)          # the page itself (an iframe is just dropped)
            _quiet(route.abort, "blockedbyclient")
        else:
            _quiet(route.continue_)

    def alive(self):
        if self.ctx is None:
            return False
        try:
            pages = [p for p in self.ctx.pages if not p.is_closed()]
            if not pages:
                self.active = self.ctx.new_page()
                return True
            if self.active is None or self.active.is_closed():
                self.active = pages[-1]
            self.active.evaluate("1")                     # a round trip: raises if Chrome is gone
            return True
        except Exception:
            self._drop()
            return False

    def _drop(self):
        try:
            if self.ctx is not None:
                self.ctx.close()
        except Exception:
            pass
        self.ctx, self.active, self.refs, self.refs_page = None, None, {}, None

    def ensure(self):
        if not self.alive():
            try:
                self._launch()
            except Exception:
                try:                                       # a dead driver: start Playwright afresh
                    if self.pw:
                        self.pw.stop()
                except Exception:
                    pass
                self.pw = None
                self._launch()
        self.blocked = None
        self.new_pages = []

    def shutdown(self):
        self._drop()
        try:
            if self.pw:
                self.pw.stop()
        except Exception:
            pass
        self.pw = None

    # ---- helpers
    def pages(self):
        return [p for p in self.ctx.pages if not p.is_closed()]

    def _after(self, page=None):
        """After an action: follow a new tab, wait for the page, and enforce the blocklist on
        where it ended up (an HTTP redirect isn't routed). -> a refusal dict or None."""
        if self.new_pages:
            np = [p for p in self.new_pages if not p.is_closed()]
            if np:
                self.active = np[-1]
                _quiet(self.active.bring_to_front)
            self.new_pages = []
        page = self.active
        _quiet(page.wait_for_load_state, "domcontentloaded", timeout=10000)
        for p in self.pages():
            why = url_blocked(p.url) if p.url.startswith(("http:", "https:")) else None
            if why:
                _quiet(p.goto, "about:blank", timeout=5000)
                self.blocked = self.blocked or (p.url, why)
        if self.blocked:
            url, why = self.blocked
            self.blocked = None
            return {"refused": f"{_short_url(url)}: {why}"}
        return None

    def state(self, page=None):
        p = page or self.active
        try:
            title = p.title()
        except Exception:
            title = ""
        out = {"title": title[:200], "url": p.url, "tab": self.pages().index(p) if p in self.pages() else None,
               "tabs": len(self.pages())}
        try:
            if p.evaluate("() => [...document.querySelectorAll('input[type=password]')].some(e => e.offsetParent)"):
                out["login_page"] = ("this page asks for a login; you never sign in. If Sir needs what's behind it, "
                                     "tell him the site wants his login.")
        except Exception:
            pass
        return out

    def describe(self, handle):
        return handle.evaluate(_DESCRIBE)

    def collect(self):
        """[(handle, description)] of the visible interactive elements of the active page."""
        handles = self.active.query_selector_all(INTERACTIVE)[:600]
        if not handles:
            return []
        descs = self.active.evaluate(f"els => els.map({_DESCRIBE})", handles)
        return [(h, d) for h, d in zip(handles, descs) if d["visible"]]

    def handle(self, ref):
        if self.refs_page is not self.active or ref not in self.refs:
            raise ValueError(f"no element {ref!r} on this page now; run browser_find again")
        return self.refs[ref]


def _quiet(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception:
        return None


def _short_url(u, n=120):
    return u if len(u) <= n else u[:n] + "…"


def _err(e):
    """A Playwright error's first line (they carry long call logs)."""
    msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return RuntimeError(msg[:300])


class _Worker:
    def __init__(self):
        self.q = queue.Queue()
        self.thread = None
        self.lock = threading.Lock()
        self.b = _Browser()

    def _loop(self):
        while True:
            job = self.q.get()
            if job is None:
                _quiet(self.b.shutdown)
                return
            fn, box, ev = job
            try:
                box["res"] = fn(self.b)
            except (ValueError, PermissionError) as e:
                box["err"] = e
            except Exception as e:
                box["err"] = _err(e)
            ev.set()

    def run(self, fn, timeout=CALL_TIMEOUT):
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._loop, daemon=True, name="vector-browser")
                self.thread.start()
        box, ev = {}, threading.Event()
        self.q.put((fn, box, ev))
        if not ev.wait(timeout):
            raise TimeoutError(f"your browser didn't finish within {timeout} s (it may still be loading)")
        if "err" in box:
            raise box["err"]
        return box["res"]

    def stop(self, timeout=8):
        if self.thread is None or not self.thread.is_alive():
            return
        self.q.put(None)
        self.thread.join(timeout)


WORKER = _Worker()
atexit.register(WORKER.stop)


def _emit(action, res, **extra):
    from . import events
    rec = {"action": action, **extra}
    if isinstance(res, dict):
        if res.get("refused"):
            rec["refused"] = str(res["refused"])[:160]
        if res.get("url"):
            u = urllib.parse.urlparse(res["url"])
            rec["host"] = u.hostname or res["url"][:40]
    events.emit("browser.action", **rec)


def _job(action, fn, timeout=CALL_TIMEOUT, **extra):
    def wrapped(b):
        b.ensure()
        return fn(b)
    try:
        res = WORKER.run(wrapped, timeout)
    except Exception as e:
        _emit(action, {"refused": None}, error=f"{type(e).__name__}: {str(e)[:100]}", **extra)
        raise
    _emit(action, res, **extra)
    return res


# ------------------------------------------------------------------ tools
def browser_open(url, new_tab=True):
    u = (url or "").strip()
    if not u:
        raise ValueError("url: a web address")
    if not re.match(r"^[a-z][a-z0-9+.-]*:", u, re.I):
        u = "https://" + u
    if len(u) > 2000:
        raise ValueError("url too long")
    why = url_blocked(u)
    if why:
        _emit("open", {"refused": why, "url": u})
        return {"refused": why, "url": _short_url(u)}

    def go(b):
        pages = b.pages()
        blank = len(pages) == 1 and pages[0].url in ("about:blank", "", "chrome://newtab/", "chrome://new-tab-page/")
        if new_tab and not blank:
            b.new_pages = []
            b.active = b.ctx.new_page()
            b.new_pages = []
        b.refs, b.refs_page = {}, None
        try:
            b.active.goto(u, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            if not b.blocked:
                ref = b._after()
                return ref or {"error": str(_err(e)), **b.state()}
        _quiet(b.active.bring_to_front)
        ref = b._after()
        return ref or b.state()
    return _job("open", go, url=_short_url(u, 80))


def browser_tabs(action="list", index=None):
    a = (action or "list").strip().lower()
    if a not in ("list", "switch", "close"):
        raise ValueError("action: list, switch or close")

    def go(b):
        pages = b.pages()
        if a != "list":
            if index is None or not (0 <= int(index) < len(pages)):
                raise ValueError(f"index: 0 to {len(pages) - 1} (from browser_tabs list)")
            p = pages[int(index)]
            if a == "switch":
                b.active = p
                _quiet(p.bring_to_front)
            elif len(pages) == 1:
                p.goto("about:blank")                      # the last tab: keep the window, empty it
            else:
                p.close()
                if b.active is p or b.active.is_closed():
                    b.active = b.pages()[max(0, int(index) - 1)]
                    _quiet(b.active.bring_to_front)
            b.refs, b.refs_page = {}, None
        out = []
        for i, p in enumerate(b.pages()):
            out.append({"index": i, "title": (_quiet(p.title) or "")[:120], "url": _short_url(p.url),
                        "active": p is b.active})
        return {"tabs": out, **({"did": a} if a != "list" else {})}
    return _job("tabs", go, op=a)


def browser_read(max_chars=6000):
    n = max(200, min(int(max_chars or 6000), 20000))

    def go(b):
        try:
            raw = b.active.inner_text("body", timeout=5000)
        except Exception:
            raw = ""
        lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in raw.splitlines()]
        text = "\n".join(ln for ln in lines if ln)
        out = b.state()
        out.update(chars=len(text), text=text[:n] + (f"…[{len(text) - n} more chars; scroll or raise max_chars]"
                                                       if len(text) > n else ""))
        return out
    return _job("read", go)


def browser_find(query="", limit=25):
    q = (query or "").strip().lower()
    words = [w for w in re.findall(r"\w+", q)]
    lim = max(1, min(int(limit or 25), 60))

    def go(b):
        items = b.collect()
        scored = []
        for h, d in items:
            hay = " ".join((_words_of(d), d["name"], d["href"], _kind(d))).lower()
            if words:
                s = sum(1 for w in words if w in hay)
                if s == 0:
                    continue
                s += 2 if q and q in hay else 0
                s += 1 if q and _words_of(d).lower() == q else 0
            else:
                s = 0
            scored.append((s, h, d))
        scored.sort(key=lambda x: -x[0])
        b.refs, b.refs_page = {}, b.active
        out = []
        for i, (s, h, d) in enumerate(scored[:lim], 1):
            ref = f"e{i}"
            b.refs[ref] = h
            row = {"ref": ref, "kind": _kind(d), "text": (_words_of(d) or d["name"])[:100]}
            if d["href"]:
                row["href"] = _short_url(d["href"], 100)
            if _is_credential_field(d) or (d.get("form") or {}).get("password"):
                row["off_limits"] = "login field"
            elif _click_refusal(d):
                row["off_limits"] = "purchase, payment or blocked site"
            if d["disabled"]:
                row["disabled"] = True
            out.append(row)
        res = {"url": b.active.url, "title": (_quiet(b.active.title) or "")[:120], "matches": out,
               "of": len(items)}
        if not out:
            res["note"] = "nothing matches; try other words, or browser_read to see the page"
        return res
    return _job("find", go, query=q[:60])


def _pick_by_text(b, text):
    t = text.strip().lower()
    best, best_s = None, 0
    for h, d in b.collect():
        w = _words_of(d).lower()
        if not w:
            continue
        s = 3 if w == t else 2 if w.startswith(t) else 1 if t in w else 0
        if s and _kind(d) in ("link", "button"):
            s += 0.5
        if s > best_s:
            best, best_s = (h, d), s
    if not best:
        raise ValueError(f"nothing clickable reads {text!r}; use browser_find")
    return best


def browser_click(ref=None, text=None):
    if not ref and not text:
        raise ValueError("give a ref (from browser_find) or the element's text")

    def go(b):
        if ref:
            h = b.handle(ref)
            d = b.describe(h)
        else:
            h, d = _pick_by_text(b, text)
        why = _click_refusal(d, page_login=bool(b.state().get("login_page")))
        if why:
            return {"refused": why, "element": _words_of(d)[:80]}
        b.new_pages = []
        if d.get("target") and d["target"] != "_self" and d["href"]:
            try:                                           # a link that opens a tab: wait for it
                with b.ctx.expect_page(timeout=8000):
                    h.click(timeout=8000)
            except Exception:
                pass
        else:
            h.click(timeout=8000)
            b.active.wait_for_timeout(150)                 # let a popup or navigation announce itself
        b.refs, b.refs_page = {}, None
        ref_ = b._after()
        if ref_:
            return ref_
        return {"clicked": (_words_of(d) or d["name"])[:80], **b.state()}
    return _job("click", go, target=(ref or text or "")[:60])


def browser_type(ref, text, submit=False):
    if text is None or len(str(text)) > 2000:
        raise ValueError("text: up to 2000 characters")
    text = str(text)

    def go(b):
        h = b.handle(ref)
        d = b.describe(h)
        why = _type_refusal(d, text, page_login=bool(b.state().get("login_page")))
        if not why and submit:
            why = _submit_refusal(d)
        if why:
            return {"refused": why, "field": (_words_of(d) or d["name"])[:80]}
        if d["tag"] == "select":
            h.select_option(label=text, timeout=5000)
        else:
            h.fill(text, timeout=8000)
        if submit:
            b.new_pages = []
            h.press("Enter", timeout=8000)
            b.refs, b.refs_page = {}, None
            ref_ = b._after()
            if ref_:
                return ref_
            return {"typed": len(text), "submitted": True, **b.state()}
        return {"typed": len(text), "field": (_words_of(d) or d["name"])[:80]}
    return _job("type", go, ref=str(ref)[:10], submit=bool(submit))


def browser_scroll(direction="down"):
    dr = (direction or "down").strip().lower()
    js = {"down": "window.scrollBy(0, window.innerHeight * 0.85)", "up": "window.scrollBy(0, -window.innerHeight * 0.85)",
          "top": "window.scrollTo(0, 0)", "bottom": "window.scrollTo(0, document.documentElement.scrollHeight)"}
    if dr not in js:
        raise ValueError("direction: down, up, top or bottom")

    def go(b):
        b.active.evaluate(f"() => {{ {js[dr]}; }}")
        b.active.wait_for_timeout(250)
        pos = b.active.evaluate("() => [window.scrollY, document.documentElement.scrollHeight, window.innerHeight]")
        y, total, vh = pos
        pct = 100 if total <= vh else round(100 * min(1, (y + vh) / total))
        return {"scrolled": dr, "seen_pct": pct, "at_bottom": y + vh >= total - 2, "url": b.active.url}
    return _job("scroll", go, direction=dr)


def browser_back():
    def go(b):
        r = b.active.go_back(wait_until="domcontentloaded", timeout=20000)
        b.refs, b.refs_page = {}, None
        if r is None and b.active.url in ("about:blank", ""):
            return {"note": "no earlier page in this tab", **b.state()}
        ref_ = b._after()
        return ref_ or {"back": True, **b.state()}
    return _job("back", go)


def browser_look(question="What's on this page?"):
    q = (question or "What's on this page?").strip()[:600]

    def shot(b):
        why = url_blocked(b.active.url) if b.active.url not in ("about:blank", "") else None
        if why:
            return {"refused": why}
        jpeg = b.active.screenshot(type="jpeg", quality=85, timeout=15000)
        return {"jpeg": jpeg, **b.state()}
    got = _job("look", shot)
    if got.get("refused"):
        return got
    jpeg = got.pop("jpeg")
    from PIL import Image
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    w, h = im.size
    scale = min(1.0, 1600 / max(w, h))
    if scale < 1.0:
        im = im.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    del jpeg, im
    from . import eyes
    t0 = time.monotonic()
    prompt = (f"This is a screenshot of a web page in VECTOR's own browser: \"{got.get('title')}\" at {got.get('url')}. "
              f"Answer the question precisely and briefly, from what is visible only; quote any text that matters "
              f"exactly; say so if something isn't visible.\n\nQuestion: {q}")
    answer = eyes._ask(buf.getvalue(), prompt)
    return {"answer": answer, "title": got.get("title"), "url": got.get("url"),
            "seconds": round(time.monotonic() - t0, 2), "image": "your own tab, not kept"}


def browser_close():
    def go(b):
        b.shutdown()
        return {"ok": True, "closed": "your browser (it starts again on the next browser call)"}
    if WORKER.thread is None or not WORKER.thread.is_alive():
        return {"ok": True, "closed": "it wasn't open"}
    res = WORKER.run(go)
    _emit("close", res)
    return res
