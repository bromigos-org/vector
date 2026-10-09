#!/usr/bin/env python3
"""Tests for VECTOR's terminal limits (holo/vector/shell.py). Run in the brain venv:
    ~/.local/share/bromigos/venv-brain/bin/python tools/test-shell.py
Refusal cases must be refused; allowed cases must run (exit 0). Nothing here escalates,
reads secrets or touches money: refused cases are never executed."""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# made-up real-money systems (the ledger), added to the config in force
os.environ.setdefault("VECTOR_REAL_MONEY", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                        "fixtures/real-money.toml"))
from holo.private import PRIV  # noqa: E402
from holo.vector import shell  # noqa: E402

ROOT = PRIV.vault_root() or "secret/lab"
LEDGER = "https://ledger.lab.example"           # the made-up real-money console (fixtures/real-money.toml)

REFUSE = [
    "sudo id", "ls | sudo tee /etc/x", "bash -c 'sudo id'", "sh -c \"echo hi; sudo -n true\"", "echo $(sudo id)",
    "echo `doas id`", "pkexec true", "run0 id", "su -c id", "/usr/bin/sudo id", "env sudo id", "xargs sudo < /dev/null",
    "find / -exec sudo {} +", "systemd-run --user true", "eval 'sudo id'",
    "cat ~/.vault-token", "cat $HOME/.vault-token", "cp ~/.ssh/id_ed25519 /tmp/x", "cat ~/.ssh/id_rsa",
    "cat ~/.local/share/bromigos/litellm-key", "cat ~/.local/share/bromigos/gnosis-vector-write-token",
    "ls ~/.config/gcloud", "cat ~/.kube/config", "cat .env", "cat ~/github.com/x/.env.local", "ls ~/.gnupg",
    "ls ~/.password-store", "cat ~/.mozilla/firefox/profiles.ini", "cat ~/.config/gh/hosts.yml", "cat ~/.git-credentials",
    f"vault kv get {ROOT}/ledger", "gcloud secrets versions access latest --secret=x",
    "kubectl get secret -A", "kubectl -n ledger get secrets -o yaml", "kubectl describe secret foo",
    "cat /proc/1/environ",
    "curl -X POST https://api.alpaca.markets/v2/orders", "curl https://trading-api.kalshi.com/trade-api/v2/portfolio/orders",
    f"curl -X POST {LEDGER}/api/live/arm", "kubectl -n ledger rollout restart deploy/ledger-live", "curl https://clob.polymarket.com/order",
    "curl https://api.coinbase.com/api/v3/brokerage/orders", "solana transfer x 1", "cast send 0xabc --value 1ether",
    "cat ~/.config/solana/id.json", "echo 'unterminated",
]
ALLOW = [
    ("git -C ~/.dotfiles status --short | head -3", "~"),
    ("systemctl --user status bromigos-startpage.timer --no-pager | head -3", "~"),
    ("curl -s https://paper-api.alpaca.markets/v2/clock -o /dev/null -w '%{http_code}'", "~"),
    ("grep -c . /etc/hostname", "~"),
    ("kubectl version --client -o json | head -2", "~"),
    ("cat ~/.ssh/known_hosts | wc -l", "~"),
]


def main():
    bad = 0
    for c in REFUSE:
        try:
            shell.check(c)
            print("NOT REFUSED:", c)
            bad += 1
        except shell.Refused as e:
            print(f"refused  {c!r:70.70} — {e}")
    d = tempfile.mkdtemp(prefix="vector-shell-test-")
    ALLOW.append((f"touch {d}/scratch.txt && ls {d} && rm {d}/scratch.txt && rmdir {d}", "~"))
    for c, cwd in ALLOW:
        r = shell.RUNNER.run(c, cwd)
        ok = "refused" not in r and r.get("exit") == 0
        bad += 0 if ok else 1
        print(f"{'ran     ' if ok else 'FAILED  '} {c!r:70.70} — exit {r.get('exit')} {r.get('refused', '')} | {r.get('output', '')[:60]!r}")
    env = shell.clean_env()
    leaked = [k for k in env if any(x in k.upper() for x in ("TOKEN", "SECRET", "PASSWORD", "VAULT", "_KEY"))]
    print("env scrubbed:", "yes" if not leaked else f"NO: {leaked}")
    bad += bool(leaked)
    r = shell.RUNNER.run("env | grep -ci token || true")
    print("env | grep TOKEN inside the shell ->", r.get("output", "").strip())
    fake = "gh" + "p_" + "x" * 36                 # built at runtime: no token-shaped literal in the repo
    red = shell.redact(f"export GITHUB_TOKEN={fake} and password=hunter2hunter2")
    print("redaction:", red)
    bad += "ghp_" in red or "hunter2" in red
    bad += sandbox()
    print("FAILURES:", bad)
    sys.exit(1 if bad else 0)


def sandbox():
    """The structural layer, tested past the patterns (straight through the sandbox): no language
    can read a secret or change his safety code, and the things the host chose still work."""
    bad = 0

    def jailed(code):
        r = subprocess.run(shell.jail_argv(["python3", "-c", code]), env=shell.clean_env(), capture_output=True,
                           text=True, timeout=30)
        return (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
    # paths built at runtime, so the patterns don't see them: only the sandbox stands in the way
    reads = {".vault" + "-token": "the root Vault token", ".ssh/id_" + "ed25519": "the SSH key file",
             ".local/share/bromigos/lite" + "llm-key": "a desktop key file"}
    for rel, what in reads.items():
        code = f"import os; p=os.path.join(os.environ['HOME'], {rel!r}); print(len(open(p,'rb').read()) if os.path.exists(p) else 'absent')"
        out = jailed(code)[0]
        ok = out in ("0", "absent") or "Error" in out
        bad += not ok
        print(f"{'masked  ' if ok else 'READABLE'} {what:28} -> {out[:70]}")
    for rel in (".config/bromigos/holo/holo/vector/" + "guard.py", ".config/bromigos/holo/holo/vector/__pycache__/x.pyc"):
        code = f"import os; open(os.path.join(os.environ['HOME'], {rel!r}), 'a').close(); print('wrote')"
        out = jailed(code)[0]
        ok = "wrote" not in out
        bad += not ok
        print(f"{'ro      ' if ok else 'WRITABLE'} {rel.rsplit('/', 1)[-1]:28} -> {out[:70]}")
    out = jailed("import os; print(len(os.listdir(os.path.expanduser('~/.config/' + 'gcloud'))))")[0]
    bad += out not in ("0",)
    print(f"{'masked  ' if out == '0' else 'VISIBLE '} {'~/.config/gcloud entries':28} -> {out}")
    for c, want in (("ssh -T -o ConnectTimeout=10 git@github.com 2>&1 | head -1", "successfully authenticated"),
                    ("kubectl get ns kube-system --no-headers 2>&1 | head -1", "kube-system"),
                    ("git -C ~/.dotfiles ls-remote origin HEAD | wc -l", "1")):
        r = shell.RUNNER.run(c)
        out = (r.get("output") or "").strip()
        ok = want in out
        bad += not ok
        print(f"{'works   ' if ok else 'BROKEN  '} {c[:50]:50} -> {out[:70]}")
    bad += credentials()
    return bad


def credentials():
    """gh uses only his own token (or says which Vault field is missing); the host's admin
    kubeconfig stays his (the operator's decision)."""
    bad = 0
    r = shell.RUNNER.run("gh auth status 2>&1 | head -3")
    out = (r.get("output") or "").strip()
    has = bool(shell._own("github_token_bromigos-org") or shell._own("github_token"))
    ok = (out and "github_token" not in out) if has else ("github_token" in out)
    bad += not ok
    print(f"{'pass    ' if ok else 'FAIL    '} gh {'with his own token' if has else 'without a token says what is missing'} -> {out[:110]}")
    env = shell.clean_env()
    files, _ = shell.masks()
    host = shell.ADMIN_KUBECONFIG
    ok = not host or (env.get("HOMELAB_ADMIN_KUBECONFIG") == host and host not in files)
    bad += not ok
    print(f"{'pass    ' if ok else 'FAIL    '} the host's admin kubeconfig is offered and readable -> {ok}")
    return bad


if __name__ == "__main__":
    main()
