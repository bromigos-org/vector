#!/usr/bin/env python3
"""The dotfiles' privacy guard's sops check (lib/privacy_guard.py, check_sops): every value
in a *.sops.yaml must be encrypted, however it nests.

  - values under keys, list items and a list's lists (`- - ENC[…]`) that are encrypted pass;
  - a plaintext value in any of those places is reported with its line;
  - the sops metadata block is skipped, and a file without it is reported.

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

print(f"\n{'all passed' if not failed else f'{len(failed)} failed'}")
sys.exit(1 if failed else 0)
