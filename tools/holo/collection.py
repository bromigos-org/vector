"""Where a hologram collection is: the folder the tools read and write.

A collection holds manifest.json, the baked NAME.holo.npz (what the renderers read),
and the sources: src/ (GLB masters), concepts/ (the images they came from),
previews/ (part splits and renders) and generations.jsonl (every paid job).

Collections on a machine, in the order VECTOR looks (holo/fmt.py):
  ~/.config/bromigos/vector/holo          the user's own models (made here by default)
  /usr/share/bromigos/homelab/holo        bromigos-homelab's (the rack)
  /usr/share/bromigos/vector/holo         bromigos-vector's (workstation, wick, emblem)
The packaged ones are read-only; their sources live in the repos (vector's assets/holo,
bromigOS's homelab/holo), which these tools bake with --root.

The chain (each step a separate tool, so a provider layer can run any of them):
  concept image -> (cutout) -> gen3d.py: image -> GLB -> make-holo.py glb: parts + bake
or, with no image at all, make-holo.py add: procedural shapes (prims.py) + bake.
"""
import os

USER = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "bromigos", "vector", "holo")


def resolve(arg=None):
    """--root, else $BROMIGOS_HOLO_ROOT, else the user's own collection (created if missing)."""
    root = os.path.abspath(os.path.expanduser(arg or os.environ.get("BROMIGOS_HOLO_ROOT") or USER))
    os.makedirs(root, exist_ok=True)
    return root


def pop_root(argv):
    """Take `--root DIR` (or --root=DIR) out of an argv list; returns (root or None, argv)."""
    out, root, i = [], None, 0
    while i < len(argv):
        a = argv[i]
        if a == "--root" and i + 1 < len(argv):
            root, i = argv[i + 1], i + 2
            continue
        if a.startswith("--root="):
            root = a.split("=", 1)[1]
        else:
            out.append(a)
        i += 1
    return root, out
