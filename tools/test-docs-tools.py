#!/usr/bin/env python3
"""VECTOR's docs tools (holo/vector/tools.py: docs_search, docs_read), in a throwaway home.

    python3 tools/test-docs-tools.py

  - a search with no repo reads every docs root; repo "dotfiles" reads the dotfiles and
    nothing else; a repo's name reads that repository alone;
  - docs_read reads a hit by the path the search returned, and refuses a file outside the
    docs roots and one with a secret-looking name.

Nothing here reads the real home: HOME is a temporary folder with made-up repositories.
Exit 1 on any failure (CI runs it).
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

TMP = tempfile.mkdtemp(prefix="vector-docs-test-")
os.environ.update(HOME=TMP, XDG_CONFIG_HOME=os.path.join(TMP, ".config"), XDG_STATE_HOME=os.path.join(TMP, ".state"),
                  XDG_RUNTIME_DIR=TMP)

try:                                   # CI: no bromigos-core, so no private overlay module; an empty one
    import bromigos_private  # noqa: F401
except ImportError:
    with open(os.path.join(TMP, "bromigos_private.py"), "w") as f:
        f.write("def data(): return {}\ndef present(): return False\ndef get(key, default=None): return default\n"
                "def url(name, path='', default=''): return default\ndef vault(name, default=''): return default\n"
                "def vault_root(default=''): return default\ndef lan_domain(default=''): return default\n"
                "def path(name, default=''): return default\nPATH = ''\n")
    sys.path.insert(0, TMP)

from holo.vector import tools  # noqa: E402

BAD = [0]


def ok(cond, what, detail=""):
    print(f"{'pass' if cond else 'FAIL'}  {what}" + (f"  ({detail})" if detail and not cond else ""))
    BAD[0] += not cond


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


# one made-up document per docs root: a repository in each owner's folder, and the dotfiles
DOT = os.path.join(TMP, ".dotfiles")
OWNERS = [r for r in tools.DOC_ROOTS if r != DOT]
ok(DOT in tools.DOC_ROOTS and len(OWNERS) == len(tools.DOC_ROOTS) - 1, "the dotfiles are one of the docs roots",
   str(tools.DOC_ROOTS))
for n, root in enumerate(OWNERS):
    write(os.path.join(root, f"repo{n}", "README.md"), f"# repo{n}\n\nThe beacon is lit in repo{n}.\n")
write(os.path.join(DOT, "README.md"), "# dotfiles\n\nThe beacon is lit in the dotfiles.\n")
write(os.path.join(DOT, "notes", "token-rotation.md"), "The beacon is lit in a secret-looking file.\n")
write(os.path.join(TMP, "elsewhere", "README.md"), "The beacon is lit outside the docs roots.\n")


def under(hit, root):
    return os.path.expanduser(hit["path"]).startswith(root + os.sep)


everything = tools.docs_search("beacon")["hits"]
ok(len(everything) == len(tools.DOC_ROOTS) and all(any(under(h, r) for h in everything) for r in tools.DOC_ROOTS),
   "no repo: every docs root is searched", str([h["path"] for h in everything]))
dot = tools.docs_search("beacon", repo="dotfiles")["hits"]
ok(len(dot) == 1 and under(dot[0], DOT), "repo dotfiles: the dotfiles, and nothing else",
   str([h["path"] for h in dot]))
one = tools.docs_search("beacon", repo="repo0")["hits"]
ok(len(one) == 1 and under(one[0], os.path.join(OWNERS[0], "repo0")), "a repo's name: that repository alone",
   str([h["path"] for h in one]))
ok(not any("token-rotation" in h["path"] for h in everything), "a secret-looking name is never searched")

r = tools.docs_read(dot[0]["path"]) if dot else {}
ok("lit in the dotfiles" in r.get("text", ""), "docs_read reads a hit by the path the search returned")
for path, what in ((os.path.join(TMP, "elsewhere", "README.md"), "a file outside the docs roots"),
                   (os.path.join(DOT, "notes", "token-rotation.md"), "a secret-looking name")):
    try:
        tools.docs_read(path)
        ok(False, f"docs_read refuses {what}")
    except ValueError:
        ok(True, f"docs_read refuses {what}")

print("all passed" if not BAD[0] else f"{BAD[0]} FAILED")
sys.exit(1 if BAD[0] else 0)
