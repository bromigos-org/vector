#!/usr/bin/env python3
"""create.py: VECTOR makes things on the homelab only, never a paid provider on his own.

    ~/.local/share/bromigos/venv-brain/bin/python tools/test-create.py          # stubbed CLI
    ~/.local/share/bromigos/venv-brain/bin/python tools/test-create.py --live   # + a real run on the homelab

The stub stands in for `bromigos art make`: it records every argv, writes the output file
and answers like the CLI (or fails, to play a homelab that's down). --live makes a real
cutout and a small hologram mesh on the homelab into a scratch folder and checks 0 credits.
"""
import json
import os
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["VECTOR_CREATE_QUIET"] = "1"

TMP = tempfile.mkdtemp(prefix="vector-create-test-")
CALLS = os.path.join(TMP, "calls.jsonl")
STUB = os.path.join(TMP, "bromigos")
with open(STUB, "w") as f:
    f.write(f"""#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
open({CALLS!r}, "a").write(json.dumps(a) + "\\n")
mode = os.environ.get("STUB_MODE", "ok")
if mode == "down":
    sys.stderr.write("comfyui:homelab: connection refused\\n"); sys.exit(1)
out = a[a.index("--out") + 1]
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, "wb").write(b"x")
prov = "nolgia" if mode == "paid" else a[a.index("--provider") + 1]
print(json.dumps({{"capability": a[2], "provider": prov, "model": "wf", "out": out,
                  "credits": 5 if mode == "paid" else 0, "licence": "test", "seconds": 0.1}}))
""")
os.chmod(STUB, os.stat(STUB).st_mode | stat.S_IEXEC)
CONF_LAB = os.path.join(TMP, "create.toml")
with open(CONF_LAB, "w") as f:
    f.write('format = 1\n[comfyui.homelab]\nendpoint = "@homelab:comfyui"\n'
            '[image.generate]\nproviders = ["comfyui:homelab", "none"]\ncomfyui = "qwen-image-2.1"\n'
            '[image.cutout]\nproviders = ["comfyui:homelab", "none"]\ncomfyui = "lucida"\n'
            '[model3d.generate]\nproviders = ["comfyui:homelab", "none"]\ncomfyui = "pixal3d"\n'
            '[video.loop]\nproviders = ["nolgia"]\n')
CONF_NONE = os.path.join(TMP, "create-none.toml")
with open(CONF_NONE, "w") as f:
    f.write("format = 1\n")

LIVE = "--live" in sys.argv
if not LIVE:
    os.environ["BROMIGOS_BIN"] = STUB
os.environ["BROMIGOS_CREATE_CONFIG"] = CONF_LAB

from holo.vector import create  # noqa: E402

create.OUT_ROOT = os.path.join(TMP, "out")
create.HOLO_RAW = os.path.join(TMP, "3d-raw")
create.LEDGER = os.path.join(TMP, "ledger.jsonl")
create.DEFAULT_CONF = os.path.join(TMP, "no-default.toml")   # the packaged default lists nolgia: leave it out
PIC = os.path.join(os.path.expanduser("~/Pictures"), ".vector-create-test.png")
os.makedirs(os.path.dirname(PIC), exist_ok=True)
from PIL import Image  # noqa: E402
Image.new("RGB", (64, 64), (200, 40, 40)).save(PIC)

fails = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def calls():
    try:
        return [json.loads(x) for x in open(CALLS)]
    except OSError:
        return []


def reset():
    if os.path.exists(CALLS):
        os.unlink(CALLS)
    os.environ.pop("STUB_MODE", None)


if not LIVE:
    reset()
    r = create.create("image.cutout", inputs=[PIC], name="t")
    c = calls()
    check("cutout runs on the homelab", r.get("ok") and r["provider"] == "comfyui:homelab" and r["credits"] == 0, r)
    check("names the homelab explicitly", c and c[0][c[0].index("--provider") + 1] == "comfyui:homelab", c)
    check("caps credits at 0", c and c[0][c[0].index("--max-credits") + 1] == "0", c)
    check("never asks nolgia", all("nolgia" not in a for a in c), c)
    check("the file lands under OUT_ROOT", r.get("file", "").startswith(create.OUT_ROOT), r)

    reset()
    os.environ["STUB_MODE"] = "down"
    r = create.create("image.generate", prompt="a red cube", name="t")
    c = calls()
    check("homelab down: refuses", "refused" in r, r)
    check("homelab down: tried the homelab once, nothing else", len(c) == 1 and "nolgia" not in c[0], c)

    reset()
    os.environ["STUB_MODE"] = "paid"
    r = create.create("image.cutout", inputs=[PIC])
    check("a paid answer is refused, not passed on", "refused" in r, r)

    reset()
    r = create.create("video.loop", prompt="waves")
    check("no homelab model (video): refuses without running", "refused" in r and not calls(), r)

    reset()
    os.environ["BROMIGOS_CREATE_CONFIG"] = CONF_NONE
    r = create.create("image.generate", prompt="a red cube")
    check("no homelab configured: refuses without running", "refused" in r and not calls(), r)
    check("no homelab configured: capabilities serve nothing", create.capabilities()["serves"] == [])
    os.environ["BROMIGOS_CREATE_CONFIG"] = CONF_LAB

    reset()
    r = create.make_hologram("test-cube", subject="a red cube")
    c = calls()
    check("hologram chain: concept, cutout, 3D in order",
          [x[2] for x in c] == ["image.generate", "image.cutout", "model3d.generate"], c)
    check("hologram chain: all on the homelab", all(x[x.index("--provider") + 1] == "comfyui:homelab" for x in c), c)
    check("hologram chain: hands the bake to the build loop", "build_goal" in r and "build_start" in r.get("next", ""), r)
    check("hologram chain: costs nothing", r.get("credits") == 0 and all(s["credits"] == 0 for s in r["steps"]), r)

    reset()
    os.environ["STUB_MODE"] = "down"
    r = create.make_hologram("test-cube", subject="a red cube")
    check("hologram chain, homelab down: refuses at the first step", "refused" in r and len(calls()) == 1, r)
    reset()

    for bad in ("/etc/passwd", os.path.expanduser("~/.ssh/id_ed25519.png")):
        try:
            create.create("image.cutout", inputs=[bad])
            check(f"refuses input {bad}", False)
        except ValueError:
            check(f"refuses input {bad}", True)

    from holo.vector import shell
    for cmd in ("bromigos art make image.generate --prompt x --out /tmp/x.png --provider nolgia",
                "/usr/bin/bromigos art run mire"):
        try:
            shell.check(cmd)
            check(f"terminal refuses `{cmd[:40]}`", False)
        except shell.Refused:
            check(f"terminal refuses `{cmd[:40]}`", True)

    from holo.vector import tools
    for n in ("create", "make_hologram"):
        check(f"{n} is offered", n in tools.SPECS, tools.HIDDEN.get(n))
    check("nolgia_generate says: only when asked by name", "ONLY when Sir explicitly asks" in tools.SPECS.get(
        "nolgia_generate", ("ONLY when Sir explicitly asks",))[0])
else:
    os.environ.pop("BROMIGOS_BIN", None)
    os.environ["BROMIGOS_CREATE_CONFIG"] = os.path.expanduser("~/.config/bromigos/create.toml")
    r = create.create("image.cutout", inputs=[PIC], name="live-cutout")
    print(json.dumps(r))
    check("live cutout on the homelab, 0 credits", r.get("ok") and r["provider"].startswith("comfyui:") and r["credits"] == 0, r)
    r = create.make_hologram("test-mug", subject="a plain ceramic coffee mug with a handle")
    print(json.dumps({k: v for k, v in r.items() if k != "build_goal"}))
    check("live hologram mesh on the homelab, 0 credits",
          r.get("ok") and r.get("credits") == 0 and os.path.getsize(r["glb"]) > 1000, r)
    print("outputs:", TMP)

os.unlink(PIC)
print(f"\n{len(fails)} failures" if fails else "\nall passed")
sys.exit(1 if fails else 0)
