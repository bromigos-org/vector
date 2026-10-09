#!/usr/bin/env python3
"""VECTOR and bromigOS's lab console (holo/vector/lab.py, guard.py's _lab_switch).

    ~/.local/share/bromigos/venv-brain/bin/python tools/test-lab.py

His terminal reads the lab and refuses whatever would change it, where AI runs or what it
costs; his lab tools queue jobs as his, never with --cloud, and cancel only his own. A stub
stands in for `bromigos-lab` (it records every argv and answers like the CLI), so nothing
here reaches a lab. Exit 1 on any failure.
"""
import json
import os
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

TMP = tempfile.mkdtemp(prefix="vector-lab-test-")
CALLS = os.path.join(TMP, "calls.jsonl")
STUB = os.path.join(TMP, "bromigos-lab")
with open(STUB, "w") as f:
    f.write(f"""#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
open({CALLS!r}, "a").write(json.dumps({{"argv": a, "by": os.environ.get("BROMIGOS_LAB_BY")}}) + "\\n")
jobs = {{"1009-1-image-aa": {{"id": "1009-1-image-aa", "kind": "image", "state": "succeeded", "by": "vector",
                              "outputs": ["/home/u/Pictures/bromigos/lab/image/1009-1-image-aa.png"], "created": 1}},
        "1009-2-voice-bb": {{"id": "1009-2-voice-bb", "kind": "voice", "state": "running", "by": "you", "created": 2}}}}
if a[:1] == ["status"]:
    print(json.dumps({{"configured": True, "state": "warn", "seconds": 1.0, "cards": [
        {{"id": "gpus", "title": "GPUs", "state": "ok", "summary": "4 GPUs", "columns": ["name", "vram"],
          "items": [{{"name": "RTX 5090", "vram": "14 / 31 GiB", "state": "ok"}}]}},
        {{"id": "deployments", "title": "GitOps apps", "state": "warn", "summary": "1 out of sync", "columns": ["name", "sync"],
          "items": [{{"name": "homelab-apps", "sync": "out_of_sync", "state": "warn"}}, {{"name": "x", "sync": "synced", "state": "ok"}}]}}]}}))
elif a[:1] == ["run"]:
    j = {{"id": "1009-3-" + a[1] + "-cc", "kind": a[1], "state": "queued", "by": os.environ.get("BROMIGOS_LAB_BY"),
         "created": 3, "prompt": a[a.index("--prompt") + 1] if "--prompt" in a else None}}
    if a[1] == "video":
        sys.stderr.write("Video jobs can't run on this lab: video goes to the lab once Wan 2.2 is wired\\n"); sys.exit(3)
    print(json.dumps(j))
elif a[:2] == ["jobs", "list"]:
    print(json.dumps({{"service": "running", "jobs": list(jobs.values())}}))
elif a[:2] == ["jobs", "logs"]:
    j = jobs.get(a[2])
    if not j:
        sys.stderr.write("no job " + a[2] + "\\n"); sys.exit(3)
    print(json.dumps({{"job": j, "log": "started\\nsucceeded\\n"}}))
elif a[:2] == ["jobs", "cancel"]:
    print(json.dumps(dict(jobs[a[2]], state="cancelled")))
else:
    sys.exit(2)
""")
os.chmod(STUB, os.stat(STUB).st_mode | stat.S_IEXEC)
os.environ["BROMIGOS_LAB_BIN"] = STUB

from holo.vector import guard, lab  # noqa: E402

BAD = [0]


def ok(cond, what, detail=""):
    print(f"{'pass' if cond else 'FAIL'}  {what}" + (f"  ({detail})" if detail and not cond else ""))
    BAD[0] += not cond


def refused(cmd):
    words = cmd.split()
    return guard.check_command(words, cmd)


# ------------------------------------------------------------------ his terminal
REFUSE = ["bromigos lab target cloud", "bromigos lab target order homelab local cloud", "bromigos lab target reset",
          "bromigos lab connect", "bromigos lab connect --yes", "bromigos setup homelab --yes",
          "bromigos lab models load qwen3:8b", "bromigos lab models unload comfyui", "bromigos lab models add flux",
          "bromigos lab models remove x", "bromigos lab models replace a b",
          "bromigos lab run image --prompt x", "bromigos lab run image --prompt x --cloud",
          "bromigos lab jobs cancel 1009-2-voice-bb", "bromigos lab jobs retry 1009-2", "bromigos lab deploy comfyui",
          "bromigos lab restart voice", "bromigos lab power wake gpu-box", "bromigos lab provision", "bromigos lab update",
          "/usr/lib/bromigos/bin/bromigos-lab target cloud", "python3 /usr/lib/bromigos/bin/bromigos-lab run voice --text hi",
          "bromigos-lab models unload comfyui",
          "touch " + os.path.join(guard._lab_state(), "jobs", "x.json")]
ALLOW = ["bromigos lab status --json", "bromigos lab", "bromigos lab doctor", "bromigos lab models",
         "bromigos lab models list --json", "bromigos lab models recommend", "bromigos lab jobs",
         "bromigos lab jobs list --json", "bromigos lab jobs logs 1009-1", "bromigos lab jobs 1009-1",
         "bromigos lab jobs watch", "bromigos lab target", "bromigos lab target check", "bromigos-lab status",
         "cat " + os.path.join(guard._lab_state(), "jobs", "x.log")]
for c in REFUSE:
    why = refused(c)
    ok(bool(why), f"refused: {c}", "allowed")
for c in ALLOW:
    why = refused(c)
    ok(not why, f"allowed: {c}", why or "")
ok(guard._lab_state().rstrip("/") + "/" in guard.WRITE_LOCKED, "the queue's state is read-only in his terminal")
# the lab's settings (~/.config/bromigos/lab.toml: the planner, the upgrade policy, the bench): read, never write
LAB_TOML = guard._lab_settings()
ok(LAB_TOML in guard.WRITE_LOCKED_FILES, "lab.toml is read-only in his terminal")
for c in ("echo '[upgrade]' >> " + LAB_TOML, "sed -i s/notify/apply/ " + LAB_TOML,
          "cp /tmp/x.toml ~/.config/bromigos/lab.toml", "tee ~/.config/bromigos/lab.toml",
          "python3 -c \"open('" + LAB_TOML + "', 'w').write('x')\"", "rm ~/.config/bromigos/lab.toml",
          "mv /tmp/x bromigos/lab.toml"):
    why = refused(c)
    ok(bool(why), f"refused: {c}", "allowed")
for c in ("bromigos lab models upgrades --apply", "bromigos lab models refresh --submit-bench",
          "bromigos lab models replace image"):
    why = refused(c)
    ok(bool(why), f"refused: {c}", "allowed")
for c in ("cat " + LAB_TOML, "grep policy ~/.config/bromigos/lab.toml", "bromigos lab models plan --json",
          "bromigos lab models upgrades", "bromigos lab models pick image 12", "bromigos lab models tiers video",
          "bromigos lab models refresh --hf", "bromigos lab models check"):
    why = refused(c)
    ok(not why, f"allowed: {c}", why or "")
ok(LAB_TOML not in guard.PROTECTED_ABS, "lab.toml isn't hashed: the host's Settings writes it without switching his terminal off")
ok("holo/vector/lab.py" in guard.HOLO_FILES, "lab.py is protected safety code")

# ------------------------------------------------------------------ his tools
st = lab.lab_console()
ok(st["state"] == "warn" and len(st["cards"]) == 2, "lab_console reads the cards")
dep = next(c for c in st["cards"] if c["id"] == "deployments")
ok([i["name"] for i in dep["items"]] == ["homelab-apps"], "only the items needing attention unless a card is asked")
pic = os.path.join(os.path.expanduser("~/Pictures"), f".vector-lab-test-{os.getpid()}.png")
os.makedirs(os.path.dirname(pic), exist_ok=True)
open(pic, "wb").write(b"x")
try:
    j = lab.lab_job_submit("cutout", inputs=[pic])
    ok(j["state"] == "queued" and j["by"] == "vector", "a job is queued as his")
    j = lab.lab_job_submit("image", prompt="a lighthouse at dusk")
    ok(j["kind"] == "image" and "say" in j, "an image job, with what to say")
    calls = [json.loads(x) for x in open(CALLS)]
    ok(all(c["by"] == "vector" for c in calls), "every call is made as vector (BROMIGOS_LAB_BY)")
    ok(not any("--cloud" in c["argv"] for c in calls), "no call ever asks for the cloud")
    r = lab.lab_job_submit("video", prompt="x")
    ok("refused" in r and "Wan 2.2" in r["refused"], "the lab's refusal is passed on in words")
    for bad in ("~/.ssh/id_ed25519", "/etc/passwd"):
        try:
            lab.lab_job_submit("cutout", inputs=[bad])
            ok(False, f"input {bad} refused")
        except ValueError:
            ok(True, f"input {bad} refused")
    try:
        lab._lab(["run", "image", "--cloud"])
        ok(False, "_lab refuses --cloud")
    except PermissionError:
        ok(True, "_lab refuses --cloud")
finally:
    os.unlink(pic)
js = lab.lab_jobs()
ok(len(js["jobs"]) == 2 and js["service"] == "running", "lab_jobs lists the queue")
one = lab.lab_jobs("1009-1-image-aa")
ok(one["job"]["outputs"][0].endswith(".png") and "succeeded" in one["log"], "one job with its result and log")
r = lab.lab_job_cancel("1009-2-voice-bb")
ok("refused" in r and "Sir's job" in r["refused"], "he can't cancel Sir's job")
r = lab.lab_job_cancel("1009-1-image-aa")
ok(r.get("state") == "cancelled", "he cancels his own")
print("FAILURES:", BAD[0])
sys.exit(1 if BAD[0] else 0)
