"""VECTOR and the homelab Vault: he can wire secrets, never see them.

Identity: AppRole `vector` (policy `vector`, set up by the lab's Vault role). Vault itself
allows read/write under the lab's KV subtree (private overlay vault.root) and denies the trees of
real-money systems (venue, wallet and live-operator keys), entitlements (billing), his own credentials and every sys/auth/
policy/token path. role_id and secret_id are mode-600 files in ~/.local/share/bromigos/;
the login token (30 min, renewable to 8 h) lives only in this process's memory.

Three tools, none of which returns a value:
  vault_list  folder entries, or a secret's key NAMES
  vault_put   store one key with kv-patch semantics (other keys untouched); the value is
              generated here or typed by the operator in a local dialog
  vault_copy  one key from one path to another, Vault to Vault
To wire an app he writes an ExternalSecret in the lab's GitOps repo, so the value goes
Vault -> ESO -> the pod. The `vault` CLI and Vault's HTTP API stay refused in his shell.
The audit log records path, key and operation; never a value.
"""
import json
import os
import re
import secrets
import string
import subprocess
import threading
import time
import urllib.error
import urllib.request

from ..private import PRIV

HOME = os.path.expanduser("~")
ADDR = PRIV.url("vault")                           # private: endpoints.vault
ROLE_ID = os.path.join(HOME, ".local/share/bromigos/vault-vector-role-id")
SECRET_ID = os.path.join(HOME, ".local/share/bromigos/vault-vector-secret-id")
CA = os.path.join(HOME, ".config/homelab/homelab-ca.crt")
ROOT = PRIV.vault_root()                           # private: vault.root, "<mount>/<subtree>"
MOUNT, _, SUBTREE = (ROOT or "secret/").partition("/")
PATH_OK = (re.compile(rf"(?:{re.escape(MOUNT)}/)?({re.escape(SUBTREE)}(?:/[A-Za-z0-9_.-]+)*)/?$") if SUBTREE
           else re.compile(r"(?!)"))                # no overlay: nothing is allowed
KEY_OK = re.compile(r"[A-Za-z0-9_.-]{1,128}$")
CHARSETS = {"alnum": string.ascii_letters + string.digits, "hex": "0123456789abcdef",
            "urlsafe": string.ascii_letters + string.digits + "-_",
            "strong": string.ascii_letters + string.digits + "!#%+,-.:=@^_~"}


class VaultError(Exception):
    pass


def _ctx():
    import ssl
    return ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()


class Client:
    def __init__(self):
        self.lock = threading.Lock()
        self.token = None
        self.expires = 0.0

    def _token(self):
        with self.lock:
            if self.token and time.monotonic() < self.expires - 60:
                return self.token
            try:
                with open(ROLE_ID) as f:
                    rid = f.read().strip()
                with open(SECRET_ID) as f:
                    sid = f.read().strip()
            except OSError:
                raise VaultError("no Vault identity on this machine (vault-vector-role-id / secret-id)") from None
            req = urllib.request.Request(f"{ADDR}/v1/auth/approle/login", method="POST",
                                         data=json.dumps({"role_id": rid, "secret_id": sid}).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=15, context=_ctx()) as r:
                auth = json.load(r)["auth"]
            self.token = auth["client_token"]
            self.expires = time.monotonic() + float(auth.get("lease_duration") or 1800)
            return self.token

    def _req(self, method, path, body=None, timeout=15):
        url = f"{ADDR}/v1/{path}"
        hdr = {"Content-Type": "application/json", "X-Vault-Token": self._token()}
        if method == "LIST":
            method, url = "GET", url + "?list=true"
        if method == "PATCH":
            hdr["Content-Type"] = "application/merge-patch+json"
        req = urllib.request.Request(url, method=method, headers=hdr,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            where = path.split("?")[0].replace(f"{MOUNT}/data/", "secret/").replace(f"{MOUNT}/metadata/", "secret/")
            if e.code == 403:
                raise VaultError(f"denied by Vault policy ({where})") from None
            if e.code == 404:
                raise VaultError(f"not found ({where})") from None
            raise VaultError(f"Vault answered {e.code} ({where})") from None

    def _data(self, path):
        return (self._req("GET", f"{MOUNT}/data/{path}").get("data") or {}).get("data") or {}

    def key_names(self, path):
        return sorted(self._data(path))

    def entries(self, path):
        return (self._req("LIST", f"{MOUNT}/metadata/{path.rstrip('/')}").get("data") or {}).get("keys") or []

    def patch(self, path, data):
        """kv patch: other keys untouched; creates the secret when it doesn't exist."""
        try:
            self._req("PATCH", f"{MOUNT}/data/{path}", {"data": data})
        except VaultError as e:
            if "not found" not in str(e):
                raise
            self._req("POST", f"{MOUNT}/data/{path}", {"data": data, "options": {"cas": 0}})

    def copy(self, sp, sk, dp, dk):
        data = self._data(sp)
        if sk not in data:
            raise VaultError(f"no key {sk} at secret/{sp}")
        self.patch(dp, {dk: data[sk]})


CLIENT = Client()


OWN = f"{SUBTREE}/vector" if SUBTREE else None    # his own credentials (AppRole ids, GitHub tokens)


def norm_path(p):
    p = (p or "").strip()
    m = PATH_OK.match(p)
    if not m or ".." in p:
        raise VaultError(f"path must be under {ROOT}/ (e.g. {ROOT}/litellm)" if ROOT
                         else "no Vault configured on this machine (private overlay vault.root)")
    if OWN and (m.group(1) == OWN or m.group(1).startswith(OWN + "/")):
        raise VaultError("that is VECTOR's own credentials; his tools never list, write or copy them")
    return m.group(1)


_OWN = {}


def own_field(field):
    """One field of his own credentials, for this desktop's code to hand to his sandbox (his
    GitHub tokens as GH_TOKEN*). Never returned by a tool, never logged. None while the field
    doesn't exist. Cached 5 min (1 min if absent)."""
    if not OWN:
        return None
    hit = _OWN.get(field)
    if hit and time.monotonic() - hit[0] < (300 if hit[1] else 60):
        return hit[1]
    try:
        val = CLIENT._data(OWN).get(field) or None
    except VaultError:
        val = None
    _OWN[field] = (time.monotonic(), val)
    return val


def norm_key(k):
    if not KEY_OK.match(k or ""):
        raise VaultError("key must be letters, digits, _ . -")
    return k


def _audit(op, path, key=None, ok=True, err=None, **extra):
    from .tools import _audit as audit
    audit("vault_" + op, {"path": f"secret/{path}", **({"key": key} if key else {}), **extra}, ok, 0, err=err)


def _operator_prompt(path, key):
    """A local password dialog; what the operator types goes straight to Vault."""
    title = "VECTOR · store a secret"
    text = f"Value for {key} at secret/{path}\n(it goes straight to Vault; VECTOR never sees it)"
    for argv in (["zenity", "--entry", "--hide-text", "--title", title, "--text", text],
                 ["kdialog", "--title", title, "--password", text]):
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            raise VaultError("the operator didn't answer the dialog within 5 minutes") from None
        if r.returncode != 0:
            raise VaultError("the operator cancelled the dialog")
        val = r.stdout.rstrip("\n")
        if not val:
            raise VaultError("the operator entered nothing")
        return val
    raise VaultError("no dialog program on this machine (zenity or kdialog)")


# ------------------------------------------------------------------ tools
def vault_list(path=None):
    """A folder's entries, or a secret's key names. Never values."""
    p = norm_path(path or ROOT)
    try:
        try:
            names = CLIENT.entries(p)
            kind = "folder"
        except VaultError as e:
            if "not found" not in str(e):
                raise
            names, kind = CLIENT.key_names(p), "secret"
        _audit("list", p, n=len(names), kind=kind)
        return {"path": f"secret/{p}", "kind": kind, ("keys" if kind == "secret" else "entries"): names,
                "note": "names only, never values" + ("; a trailing / is a folder" if kind == "folder" else "")}
    except VaultError as e:
        _audit("list", p, ok=False, err=e)
        raise


def vault_put(path, key, value_from, length=32, charset="alnum"):
    """value_from: generate (random, length 12-256, charset alnum|hex|urlsafe|strong) or
    operator_prompt (a local dialog the operator types or pastes into)."""
    p, k = norm_path(path), norm_key(key)
    vf = (value_from or "").strip()
    try:
        if vf == "generate":
            n = max(12, min(int(length or 32), 256))
            chars = CHARSETS.get(charset or "alnum")
            if not chars:
                raise VaultError(f"charset must be one of {sorted(CHARSETS)}")
            val, how = "".join(secrets.choice(chars) for _ in range(n)), f"generated, {n} {charset or 'alnum'}"
        elif vf == "operator_prompt":
            val, how = _operator_prompt(p, k), "typed by the operator"
        else:
            raise VaultError("value_from must be generate or operator_prompt")
        CLIENT.patch(p, {k: val})
        del val
        _audit("put", p, k, how=how)
        return {"ok": True, "path": f"secret/{p}", "key": k, "how": how, "value": "(stored; never shown)"}
    except VaultError as e:
        _audit("put", p, k, ok=False, err=e)
        raise


def vault_copy(src, dst):
    """src and dst as <vault.root>/<path>#<key>; the value moves Vault to Vault."""
    try:
        (sp, sk), (dp, dk) = (s.rsplit("#", 1) for s in (src, dst))
    except ValueError:
        raise VaultError(f"src and dst look like {ROOT or '<vault root>'}/<path>#<key>") from None
    sp, sk, dp, dk = norm_path(sp), norm_key(sk), norm_path(dp), norm_key(dk)
    try:
        CLIENT.copy(sp, sk, dp, dk)
        _audit("copy", dp, dk, src=f"secret/{sp}#{sk}")
        return {"ok": True, "from": f"secret/{sp}#{sk}", "to": f"secret/{dp}#{dk}", "value": "(copied; never shown)"}
    except VaultError as e:
        _audit("copy", dp, dk, ok=False, err=e, src=f"secret/{sp}#{sk}")
        raise
