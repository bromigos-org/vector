"""Live readings for the holograms. Every source runs on its own daemon thread and only
while something wants it (`want(name)`); a source nobody asked for in 45 s stops
polling. Nothing here writes anywhere.

  local    this machine: psutil + NVML via ctypes (no nvidia-smi fork), every 1.5 s
  lab      the homelab's /api/status with the read-only token, every 15 s
  <feed>   the user's own systems: a plugin's feed (plugins.feed), at its own pace
"""
import ctypes
import json
import os
import platform
import ssl
import threading
import time
import urllib.request

import psutil

CA = os.path.expanduser("~/.config/homelab/homelab-ca.crt")
from .private import PRIV  # noqa: E402  the operator's private values (empty on a fresh clone)
LAB_URL = PRIV.url("lab", "/api/status")
LAB_TOKEN = os.path.expanduser("~/.local/share/bromigos/lab-token")


def ssl_ctx():
    return ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()


def get_json(url, headers=None, timeout=12):
    if not url or url.startswith("/"):
        raise OSError("that service isn't configured on this machine (private overlay endpoints)")
    req = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx()) as r:
        return json.load(r)


class _Util(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _Mem(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class NVML:
    def __init__(self):
        self.ok = False
        try:
            self.lib = ctypes.CDLL("libnvidia-ml.so.1")
            if self.lib.nvmlInit_v2() != 0:
                return
            self.h = ctypes.c_void_p()
            if self.lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self.h)) != 0:
                return
            buf = ctypes.create_string_buffer(96)
            self.lib.nvmlDeviceGetName(self.h, buf, 96)
            self.name = buf.value.decode().replace("NVIDIA ", "").replace("GeForce ", "")
            self.ok = True
        except OSError:
            pass

    def read(self):
        if not self.ok:
            return None
        u, m, t, p, f = _Util(), _Mem(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        self.lib.nvmlDeviceGetUtilizationRates(self.h, ctypes.byref(u))
        self.lib.nvmlDeviceGetMemoryInfo(self.h, ctypes.byref(m))
        self.lib.nvmlDeviceGetTemperature(self.h, 0, ctypes.byref(t))
        self.lib.nvmlDeviceGetPowerUsage(self.h, ctypes.byref(p))
        fan = f.value if self.lib.nvmlDeviceGetFanSpeed(self.h, ctypes.byref(f)) == 0 else None
        return {"name": self.name, "util": u.gpu, "vram_used": m.used, "vram_total": m.total,
                "temp": t.value, "watts": p.value / 1000, "fan": fan}


class Source:
    every = 10.0

    def __init__(self):
        self.data = None
        self.err = None
        self.at = 0.0
        self.version = 0
        self.wanted = 0.0
        self.lock = threading.Lock()
        self.thread = None

    def want(self):
        self.wanted = time.monotonic()
        if not (self.thread and self.thread.is_alive()):
            self.thread = threading.Thread(target=self._loop, daemon=True, name=type(self).__name__)
            self.thread.start()

    def _loop(self):
        while time.monotonic() - self.wanted < 45:
            t0 = time.monotonic()
            try:
                d = self.read()
                with self.lock:
                    self.data, self.err, self.at = d, None, time.time()
                    self.version += 1
            except Exception as e:  # keep the last good reading; say what failed
                with self.lock:
                    self.err = str(e)[:120]
                    self.version += 1
            time.sleep(max(0.2, self.every - (time.monotonic() - t0)))

    def read(self):
        raise NotImplementedError


class Local(Source):
    every = 1.5

    def __init__(self):
        super().__init__()
        self.gpu = NVML()
        self._io = None
        self._net = None
        psutil.cpu_percent(None)

    def read(self):
        now = time.monotonic()
        cpu = psutil.cpu_percent(None, percpu=True)
        temps = psutil.sensors_temperatures() or {}
        tctl = next((t.current for t in temps.get("k10temp", []) if t.label in ("Tctl", "Tdie")), None)
        if tctl is None:
            tctl = next((t.current for ts in temps.values() for t in ts if "cpu" in (t.label or "").lower()), None)
        fans = []
        for chip, fs in (psutil.sensors_fans() or {}).items():
            for f in fs:
                if f.current > 0:
                    fans.append((f.label or chip, f.current))
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        disks = []
        seen = set()
        for p in psutil.disk_partitions(all=False):
            if p.fstype in ("squashfs", "tmpfs", "overlay") or p.device in seen or p.mountpoint.startswith(("/boot", "/snap", "/var/lib")):
                continue
            seen.add(p.device)
            try:
                u = psutil.disk_usage(p.mountpoint)
            except OSError:
                continue
            if u.total < 5e9:
                continue
            disks.append({"mount": p.mountpoint, "dev": os.path.basename(p.device), "pct": u.percent,
                          "used": u.used, "total": u.total})
        io = psutil.disk_io_counters()
        net = psutil.net_io_counters()
        rd = wr = rx = tx = 0.0
        if self._io:
            dt = max(now - self._io[0], 1e-3)
            rd = (io.read_bytes - self._io[1].read_bytes) / dt
            wr = (io.write_bytes - self._io[1].write_bytes) / dt
            rx = (net.bytes_recv - self._net.bytes_recv) / dt
            tx = (net.bytes_sent - self._net.bytes_sent) / dt
        self._io, self._net = (now, io), net
        return {
            "host": platform.node(), "kernel": platform.release(),
            "cpu": sum(cpu) / len(cpu), "cpu_max": max(cpu), "cores": len(cpu), "cpu_temp": tctl,
            "freq": (psutil.cpu_freq().current if psutil.cpu_freq() else None),
            "load": os.getloadavg(), "uptime": time.time() - psutil.boot_time(),
            "mem_used": vm.total - vm.available, "mem_total": vm.total, "mem_pct": vm.percent,
            "swap_used": sw.used, "swap_total": sw.total,
            "gpu": self.gpu.read(), "fans": fans, "disks": sorted(disks, key=lambda d: -d["pct"]),
            "disk_rd": rd, "disk_wr": wr, "net_rx": rx, "net_tx": tx,
        }


class Lab(Source):
    every = 15.0

    def read(self):
        with open(LAB_TOKEN) as f:
            tok = f.read().strip()
        return get_json(LAB_URL, {"Authorization": "Bearer " + tok})


class Feed(Source):
    """A plugin's feed (plugins.feed): its read(), every `every` seconds while wanted."""

    def __init__(self, read, every):
        super().__init__()
        self._read, self.every = read, every

    def read(self):
        return self._read()


def plugin_feeds():
    """The user's plugins' feeds (plugins.py), loading the plugins once; {} when they can't load."""
    try:
        from .vector import plugins
        plugins.load()
        return dict(plugins.FEEDS)
    except Exception:
        return {}


class Live:
    def __init__(self):
        self.sources = {"local": Local(), "lab": Lab()}
        for name, f in plugin_feeds().items():
            self.sources.setdefault(name, Feed(f["read"], f["every"]))

    def want(self, *names):
        for n in names:
            if n in self.sources:
                self.sources[n].want()

    def get(self, name):
        s = self.sources.get(name)
        if s is None:
            return None, "no such source here", 0.0
        with s.lock:
            return s.data, s.err, s.at

    def version(self):
        return sum(s.version for s in self.sources.values())
