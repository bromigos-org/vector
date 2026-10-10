#!/usr/bin/env python3
"""The dotfiles' privacy guard's sops check (lib/privacy_guard.py, check_sops): every value
in a *.sops.yaml must be encrypted, however it nests.

  - values under keys, list items and a list's lists (`- - ENC[…]`) that are encrypted pass;
  - a plaintext value in any of those places is reported with its line;
  - the sops metadata block is skipped, and a file without it is reported;
  - the documentation ranges fixtures use pass the public rules; private shapes don't.

Nothing is read from disk: the texts are inline.
Run: python3 tools/test-privacy-guard.py (the package's check() does).
"""
import os
import sys

TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TREE, "lib"))
import privacy_guard as G  # noqa: E402

E = "ENC[AES256_GCM,data:x,iv:y,tag:z,type:str]"
META = f"sops:\n    age:\n        - recipient: age1abc\n    lastmodified: \"2026-10-09T00:00:00Z\"\n    mac: {E}\n"

ENCRYPTED = f"""endpoints:
    lab: {E}
homelab:
    name: {E}
    constellation:
        ai:
            - {E}
            - {E}
        positions:
            - node: {E}
              at:
                - {E}
                - {E}
            - - {E}
              - {E}
        notes: |
            {E}
{META}"""

failed = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f": {detail}" if detail and not cond else ""))
    if not cond:
        failed.append(name)


probs = G.check_sops("x.sops.yaml", ENCRYPTED)
check("every encrypted value passes, nested lists too", probs == [], probs)

for label, line in [("a key's value", "    hub: relay-hub"),
                    ("a list item", "    - kestrel"),
                    ("a list's list item", "    - - heron"),
                    ("a list's list's key", "    - - node: plover")]:
    text = f"homelab:\n{line}\n{META}"
    probs = G.check_sops("x.sops.yaml", text)
    check(f"plaintext in {label} is caught", probs == ["line 2: plaintext value"], probs)

probs = G.check_sops("x.sops.yaml", f"homelab:\n    name: {E}\n")
check("a file without sops metadata is caught", any("no sops metadata" in p for p in probs), probs)

# the documentation ranges fixtures use (RFC 5737, RFC 3849, RFC 7042) pass the public rules;
# the private shapes they stand in for are still caught
RULES = [(label, r) for label, r in G.rules() if not label.startswith("private name")]
for line in ["ip 192.0.2.41/24 via 198.51.100.1, peer 203.0.113.2", "addr 2001:db8::1/64",
             "pair 00:00:5E:00:53:10", "bssid 00:00:5e:00:53:4f"]:
    check(f"documentation range passes: {line}", G.scan_lines([("t", 1, line)], RULES) == [])
for line, label in [("ssh 192.168.1.20", "private IPv4 address"),  # privacy: allow
                    ("pod 10.42.0.10", "private IPv4 address"),  # privacy: allow
                    ("pair AC:80:0A:00:00:01", "MAC address"),  # privacy: allow
                    ("pair 00:00:5E:00:54:10", "MAC address")]:  # privacy: allow
    bad = G.scan_lines([("t", 1, line)], RULES)
    check(f"still caught: {label} in {line!r}", [b[2] for b in bad] == [label], bad)

print(f"\n{'all passed' if not failed else f'{len(failed)} failed'}")
sys.exit(1 if failed else 0)
