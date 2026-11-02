#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Knulli WebUI - web service for Knulli / Batocera devices.

Serves (on port 8088 by default) a web page with:
  * a dashboard with system data and telemetry;
  * a ROM manager (by system, favorites, full list),
    with filters on gamelist.xml metadata, upload, download and deletion.

Uses only the Python 3 standard library (>= 3.7), so it runs on the
Python already bundled with Knulli/Batocera without installing anything.
"""

import argparse
import ast
import base64
import collections
import fcntl
import glob
import gzip
import hashlib
import hmac
import http.client
import json
import logging
import os
import re
import shutil
import signal
import socket
import stat
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "1.2.1"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
ICONS_DIR = os.path.join(STATIC_DIR, "icons", "systems")
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "knulli-webui"

log = logging.getLogger("knulli-webui")

DEFAULT_CONFIG = {
    "host": "0.0.0.0",
    "port": 8088,
    "roms_dir": "/userdata/roms",
    "userdata_dir": "/userdata",
    "es_systems_files": [
        "/usr/share/emulationstation/es_systems.cfg",
        "/userdata/system/configs/emulationstation/es_systems_*.cfg",
    ],
    # EmulationStation internal HTTP API (Batocera/Knulli)
    "es_api": "http://127.0.0.1:1234",
    # After upload/deletion, ask EmulationStation to reload the games
    "es_reload": True,
    # EmulationStation custom collections (custom-<name>.cfg) and its settings file
    "collections_dir": "/userdata/system/configs/emulationstation/collections",
    "es_settings_file": "/userdata/system/configs/emulationstation/es_settings.cfg",
    # Battery saves and save states; naming rules from the first es_savestates.cfg found
    "saves_dir": "/userdata/saves",
    "es_savestates_files": [
        "/userdata/system/configs/emulationstation/es_savestates.cfg",
        "/usr/share/emulationstation/es_savestates.cfg",
    ],
    # BIOS list: the `systems` dict of Batocera's checker script (also looked up in PATH);
    # if missing, the readme.txt Knulli generates from it at build time. Paths are relative to userdata_dir
    "bios_db": "/usr/bin/batocera-systems",
    "bios_readme_files": [
        "/userdata/bios/readme.txt",
        "/usr/share/batocera/datainit/bios/readme.txt",
    ],
    "screenshots_dir": "/userdata/screenshots",
    # Live screen: "auto" (grim on Wayland, else the framebuffer), "grim", "fbdev", or "command"
    # (screen_command prints a PNG/JPEG/BMP image on stdout, e.g. an ffmpeg kmsgrab pipeline)
    "screen_capture": "auto",
    "screen_command": "",
    "framebuffer": "/dev/fb0",
    "screen_jpeg_quality": 70,
    # Remote control: input events are written to the controller configured in EmulationStation
    "allow_remote_control": True,
    "es_input_files": [
        "/userdata/system/configs/emulationstation/es_input.cfg",
        "/usr/share/emulationstation/es_input.cfg",
    ],
    # Force the controller's event node (e.g. "/dev/input/event1") instead of matching es_input.cfg
    "pad_device": "",
    # HTTP Basic authentication: leave empty to disable it
    "username": "",
    "password": "",
    "allow_upload": True,
    "allow_delete": True,
    # 0 = no limit
    "max_upload_mb": 0,
    "sample_interval": 2.0,
}


# --------------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------------

def read_file(path, default=None):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().replace("\x00", "").strip()
    except OSError:
        return default


def read_int(path, default=None):
    try:
        return int(read_file(path))
    except (TypeError, ValueError):
        return default


def run_cmd(args, timeout=2):
    try:
        res = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, timeout=timeout)
        return res.stdout.strip() if res.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def is_within(path, root):
    """Lexical check (does not follow symlinks, which may legitimately
    point to external storage) that `path` is inside `root`."""
    path = os.path.normpath(path)
    root = os.path.normpath(root)
    return path.startswith(root + os.sep)


def es_date(value):
    """'20240115T203000' -> '2024-01-15T20:30:00' (None if invalid)."""
    m = re.match(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2}))?", value or "")
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    return "%s-%s-%sT%s:%s:%s" % (y, mo, d, h or "00", mi or "00", s or "00")


def dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def content_disposition(name, attachment=True):
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "'").replace("?", "_")
    kind = "attachment" if attachment else "inline"
    return "%s; filename=\"%s\"; filename*=UTF-8''%s" % (kind, ascii_name, urllib.parse.quote(name))


MIME_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".bmp": "image/bmp", ".ico": "image/x-icon",
    ".mp4": "video/mp4", ".webm": "video/webm", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".pdf": "application/pdf", ".zip": "application/zip", ".txt": "text/plain; charset=utf-8",
}
MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
              ".mp4", ".webm", ".mkv", ".avi", ".pdf"}


def mime_for(path):
    return MIME_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


# --------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------

KNOWN_EMULATORS = {
    "retroarch", "ppsspp", "ppssppsdl", "drastic", "mupen64plus", "flycast", "dolphin-emu",
    "dolphin-emu-nog", "pcsx2", "aethersx2", "nethersx2", "duckstation", "duckstation-nog",
    "yabasanshiro", "mednafen", "scummvm", "amiberry", "dosbox", "dosbox-staging", "dosbox-x",
    "hypseus", "openbor", "citra", "azahar", "lime3ds", "melonds", "mame", "advmame",
    "solarus-run", "gzdoom", "lzdoom", "ikemen", "rpcs3", "cemu", "yuzu", "ryujinx", "xemu",
    "vita3k", "supermodel", "redream", "hatari", "fs-uae", "x64", "xroar", "openmsx", "ruffle",
    "easyrpg-player", "devilutionx", "eduke32", "raze", "ecwolf", "box64", "mgba", "pico8",
    "love", "vpinball", "moonlight", "portmaster",
}
# /proc/<pid>/comm holds at most 15 characters (TASK_COMM_LEN - 1), so longer
# names show up truncated there: "emulationstation" becomes "emulationstatio".
COMM_LEN = 15
KNOWN_EMULATORS = {name[:COMM_LEN] for name in KNOWN_EMULATORS}
# Executable names of EmulationStation (Batocera/Knulli) and ES-DE, used to
# recognise the frontend process; matched as a prefix against comm and exe.
ES_NAMES = ("emulationstation", "es-de")
# Requests to the local EmulationStation API must never go through a proxy
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_es_process(pid_dir, comm):
    if comm in {name[:COMM_LEN] for name in ES_NAMES}:
        return True
    try:
        exe = os.path.basename(os.readlink(os.path.join(pid_dir, "exe")))
    except OSError:
        return False
    return exe.lower().startswith(ES_NAMES)


def find_es_pid():
    for comm_path in glob.glob("/proc/[0-9]*/comm"):
        if is_es_process(os.path.dirname(comm_path), (read_file(comm_path, "") or "").lower()):
            return int(comm_path.split("/")[2])
    return None


def read_meminfo():
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                parts = rest.split()
                if parts:
                    info[key] = int(parts[0]) * 1024
    except (OSError, ValueError):
        pass
    return info


def read_temps():
    temps = []
    for zone in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
        t = read_int(os.path.join(zone, "temp"))
        if t is None:
            continue
        c = t / 1000.0 if abs(t) > 300 else float(t)
        if -40 < c < 150:
            temps.append({"name": read_file(os.path.join(zone, "type"), os.path.basename(zone)),
                          "temp": round(c, 1)})
    return temps


class Telemetry:
    HISTORY = 150

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.history = collections.deque(maxlen=self.HISTORY)
        self.prev_cpu = None
        self.prev_net = None
        self.prev_t = None
        self.current = {"cpu": {}, "net": {}}
        self.last_access = 0.0
        self._static = None
        self._cache = {}

    # -- periodic sampling -------------------------------------------------

    def start(self):
        threading.Thread(target=self._loop, name="telemetry", daemon=True).start()

    def _loop(self):
        while True:
            try:
                self.sample()
            except Exception:  # pragma: no cover - must never stop the thread
                log.exception("telemetry sampling failed")
            # If nobody is watching the dashboard, sample less often (saves battery)
            idle = time.time() - self.last_access > 120
            time.sleep(self.cfg["sample_interval"] * (5 if idle else 1))

    @staticmethod
    def _cpu_times():
        res = {}
        try:
            with open("/proc/stat") as f:
                for line in f:
                    if not line.startswith("cpu"):
                        break
                    parts = line.split()
                    vals = [int(x) for x in parts[1:9]] + [0] * 8
                    res[parts[0]] = (vals[3] + vals[4], sum(vals[:8]))
        except (OSError, ValueError):
            pass
        return res

    @staticmethod
    def _net_dev():
        res = {}
        try:
            with open("/proc/net/dev") as f:
                for line in f.readlines()[2:]:
                    name, _, data = line.partition(":")
                    name = name.strip()
                    fields = data.split()
                    if name != "lo" and len(fields) >= 9:
                        res[name] = (int(fields[0]), int(fields[8]))
        except (OSError, ValueError):
            pass
        return res

    def sample(self):
        now = time.time()
        cpu = self._cpu_times()
        net = self._net_dev()
        usage, rates = {}, {}
        if self.prev_cpu:
            for key, (idle, total) in cpu.items():
                prev = self.prev_cpu.get(key)
                if prev and total - prev[1] > 0:
                    usage[key] = round(100.0 * (1 - (idle - prev[0]) / float(total - prev[1])), 1)
        if self.prev_net and self.prev_t and now > self.prev_t:
            dt = now - self.prev_t
            for key, (rx, tx) in net.items():
                prev = self.prev_net.get(key)
                if prev:
                    rates[key] = (max(0.0, (rx - prev[0]) / dt), max(0.0, (tx - prev[1]) / dt))
        self.prev_cpu, self.prev_net, self.prev_t = cpu, net, now

        mem = read_meminfo()
        mem_pct = None
        if mem.get("MemTotal"):
            avail = mem.get("MemAvailable", mem.get("MemFree", 0))
            mem_pct = round(100.0 * (mem["MemTotal"] - avail) / mem["MemTotal"], 1)
        temps = read_temps()
        temp_max = max((t["temp"] for t in temps), default=None)
        with self.lock:
            self.current = {"cpu": usage, "net": rates, "net_totals": net}
            if usage:
                self.history.append({
                    "t": int(now), "cpu": usage.get("cpu"), "mem": mem_pct, "temp": temp_max,
                    "rx": round(sum(r[0] for r in rates.values())),
                    "tx": round(sum(r[1] for r in rates.values())),
                })

    # -- information read at request time ----------------------------------

    def _cached(self, key, ttl, fn):
        hit = self._cache.get(key)
        now = time.time()
        if hit and now - hit[0] < ttl:
            return hit[1]
        value = fn()
        self._cache[key] = (now, value)
        return value

    def _static_info(self):
        if self._static is None:
            model = (read_file("/proc/device-tree/model") or read_file("/sys/firmware/devicetree/base/model")
                     or read_file("/sys/devices/virtual/dmi/id/product_name") or "")
            cpu_model = ""
            cpuinfo = read_file("/proc/cpuinfo", "")
            for key in ("model name", "Hardware", "Model", "cpu model"):
                m = re.search(r"^%s\s*:\s*(.+)$" % re.escape(key), cpuinfo, re.M)
                if m:
                    cpu_model = m.group(1).strip()
                    break
            os_release = read_file("/etc/os-release", "")
            m = re.search(r'^PRETTY_NAME="?([^"\n]*)"?', os_release, re.M)
            uname = os.uname()
            self._static = {
                "hostname": socket.gethostname(),
                "model": model,
                "os": m.group(1) if m else "Knulli",
                "version": read_file("/usr/share/batocera/batocera.version", ""),
                "board": read_file("/boot/boot/batocera.board", "") or read_file("/boot/batocera.board", ""),
                "kernel": "%s %s" % (uname.sysname, uname.release),
                "arch": uname.machine,
                "cpu_model": cpu_model,
                "cpu_count": os.cpu_count() or 1,
                "python": "%d.%d.%d" % tuple(__import__("sys").version_info[:3]),
                "webui_version": VERSION,
            }
        return self._static

    @staticmethod
    def _cpu_cores(usage):
        cores = []
        paths = glob.glob("/sys/devices/system/cpu/cpu[0-9]*")
        for path in sorted(paths, key=lambda p: int(re.sub(r"\D", "", os.path.basename(p)) or 0)):
            name = os.path.basename(path)
            freq_dir = os.path.join(path, "cpufreq")
            cur = read_int(os.path.join(freq_dir, "scaling_cur_freq"))
            cores.append({
                "id": name,
                "online": read_int(os.path.join(path, "online"), 1) == 1,
                "usage": usage.get(name),
                "freq": cur * 1000 if cur else None,
                "max": (read_int(os.path.join(freq_dir, "scaling_max_freq")) or 0) * 1000 or None,
                "min": (read_int(os.path.join(freq_dir, "scaling_min_freq")) or 0) * 1000 or None,
                "governor": read_file(os.path.join(freq_dir, "scaling_governor"), ""),
            })
        return cores

    @staticmethod
    def _gpus():
        out = []
        for path in sorted(glob.glob("/sys/class/devfreq/*")):
            name = os.path.basename(path)
            if "dmc" in name.lower():  # RAM controller, not a GPU
                continue
            out.append({
                "name": name,
                "freq": read_int(os.path.join(path, "cur_freq")),
                "max": read_int(os.path.join(path, "max_freq")),
                "governor": read_file(os.path.join(path, "governor"), ""),
            })
        return out

    @staticmethod
    def _power():
        batteries, external = [], None
        for path in sorted(glob.glob("/sys/class/power_supply/*")):
            kind = read_file(os.path.join(path, "type"), "")
            if kind == "Battery":
                volt = read_int(os.path.join(path, "voltage_now"))
                curr = read_int(os.path.join(path, "current_now"))
                temp = read_int(os.path.join(path, "temp"))
                batteries.append({
                    "name": os.path.basename(path),
                    "capacity": read_int(os.path.join(path, "capacity")),
                    "status": read_file(os.path.join(path, "status"), ""),
                    "health": read_file(os.path.join(path, "health"), ""),
                    "voltage": round(volt / 1e6, 3) if volt else None,
                    "current": round(curr / 1e6, 3) if curr is not None else None,
                    "power": round(abs(volt * curr) / 1e12, 2) if volt and curr else None,
                    "temp": round(temp / 10.0, 1) if temp is not None else None,
                })
            elif kind:
                if read_int(os.path.join(path, "online"), 0) == 1:
                    external = True
                elif external is None:
                    external = False
        return {"batteries": batteries, "external": external}

    def _storage(self):
        mounts = []
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        mounts.append((parts[1].replace("\\040", " "), parts[2], parts[0]))
        except OSError:
            pass

        def mount_of(path):
            best = ("", "", "")
            for mnt in mounts:
                if (path == mnt[0] or path.startswith(mnt[0].rstrip("/") + "/")) and len(mnt[0]) > len(best[0]):
                    best = mnt
            return best

        candidates = [("User data", self.cfg["userdata_dir"]), ("ROM", self.cfg["roms_dir"]), ("Boot", "/boot")]
        for mnt, fstype, _dev in mounts:
            if mnt.startswith(("/media/", "/mnt/", "/run/media/")) and fstype not in ("tmpfs", "overlay"):
                candidates.append((os.path.basename(mnt), mnt))
        seen, out = set(), []
        for label, path in candidates:
            try:
                st = os.stat(path)
                usage = shutil.disk_usage(path)
            except OSError:
                continue
            if st.st_dev in seen or usage.total == 0:
                continue
            seen.add(st.st_dev)
            mnt = mount_of(os.path.realpath(path))
            out.append({
                "label": label, "path": path, "mount": mnt[0], "fstype": mnt[1], "device": mnt[2],
                "total": usage.total, "used": usage.used, "free": usage.free,
                "percent": round(100.0 * usage.used / usage.total, 1),
            })
        return out

    def _addresses(self):
        addrs = collections.defaultdict(list)
        for line in run_cmd(["ip", "-o", "addr", "show"]).splitlines():
            m = re.match(r"^\d+:\s+(\S+?)(?:@\S+)?\s+(inet6?)\s+(\S+)", line)
            if m and not m.group(3).startswith("fe80"):
                addrs[m.group(1)].append(m.group(3))
        return dict(addrs)

    def _wifi(self):
        out = {}
        for line in (read_file("/proc/net/wireless", "") or "").splitlines()[2:]:
            name, _, data = line.partition(":")
            fields = data.split()
            if len(fields) >= 3:
                try:
                    out[name.strip()] = {"quality": float(fields[1].rstrip(".")),
                                         "level": float(fields[2].rstrip("."))}
                except ValueError:
                    pass
        for name, info in out.items():
            ssid = run_cmd(["iwgetid", "-r", name])
            if not ssid:
                m = re.search(r"SSID:\s*(.+)", run_cmd(["iw", "dev", name, "link"]))
                ssid = m.group(1).strip() if m else ""
            info["ssid"] = ssid
        return out

    def _primary_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))  # no packet is actually sent
            return s.getsockname()[0]
        except OSError:
            return None
        finally:
            s.close()

    def _network(self, rates, totals):
        addrs = self._cached("addrs", 10, self._addresses)
        wifi = self._cached("wifi", 15, self._wifi)
        ifaces = []
        for name in sorted(set(totals) | set(addrs)):
            base = "/sys/class/net/" + name
            state = read_file(base + "/operstate", "unknown")
            if name == "lo" or name.startswith(("dummy", "sit", "ip6tnl", "tunl", "ip_vti")):
                continue
            if state == "down" and not addrs.get(name):
                continue
            rx, tx = rates.get(name, (0, 0))
            ifaces.append({
                "name": name, "state": state, "mac": read_file(base + "/address", ""),
                "addresses": addrs.get(name, []), "rx_rate": round(rx), "tx_rate": round(tx),
                "rx_total": totals.get(name, (0, 0))[0], "tx_total": totals.get(name, (0, 0))[1],
                "wireless": wifi.get(name),
            })
        return {"interfaces": ifaces, "primary_ip": self._cached("ip", 10, self._primary_ip)}

    def _activity(self):
        es_running, es_source, emulator = False, None, None
        roms_dir = self.cfg["roms_dir"]
        for comm_path in glob.glob("/proc/[0-9]*/comm"):
            comm = (read_file(comm_path, "") or "").lower()
            if not es_running and is_es_process(os.path.dirname(comm_path), comm):
                es_running = True
                es_source = "process %s (pid %s)" % (comm, comm_path.split("/")[2])
            elif comm in KNOWN_EMULATORS and emulator is None:
                try:
                    with open(comm_path[:-4] + "cmdline", "rb") as f:
                        args = [a.decode("utf-8", "replace") for a in f.read().split(b"\x00") if a]
                except OSError:
                    args = []
                core, rom = "", ""
                for i, arg in enumerate(args):
                    if arg == "-L" and i + 1 < len(args):
                        core = os.path.basename(args[i + 1]).replace("_libretro.so", "")
                    if arg.startswith(roms_dir + "/"):
                        rom = arg
                emulator = {"name": comm, "core": core, "rom": rom,
                            "rom_name": os.path.basename(rom) if rom else ""}
        game = None
        # The EmulationStation API also confirms that ES is running, in case the
        # process was not recognised by name (any HTTP answer means it is alive).
        try:
            with _LOCAL_OPENER.open(self.cfg["es_api"] + "/runningGame", timeout=0.7) as r:
                es_running = True
                es_source = es_source or "api"
                if r.status == 200:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                    if isinstance(data, dict) and data.get("name"):
                        game = {"name": data.get("name"), "system": data.get("systemName") or data.get("system", ""),
                                "path": data.get("path", "")}
        except urllib.error.HTTPError:
            es_running = True
            es_source = es_source or "api"
        except Exception:
            pass
        return {"es_running": es_running, "es_source": es_source, "emulator": emulator, "game": game}

    def activity(self):
        return self._cached("activity", 4, self._activity)

    def snapshot(self):
        self.last_access = time.time()
        if not self.history:
            self.sample()
            time.sleep(0.25)
            self.sample()
        with self.lock:
            cur = dict(self.current)
            hist = list(self.history)
        mem = read_meminfo()
        total = mem.get("MemTotal", 0)
        avail = mem.get("MemAvailable", mem.get("MemFree", 0))
        swap_total = mem.get("SwapTotal", 0)
        temps = read_temps()
        try:
            load = list(os.getloadavg())
        except OSError:
            load = []
        uptime = read_file("/proc/uptime", "0").split()[0]
        info = dict(self._static_info())
        info.update({
            "uptime": float(uptime or 0), "load": [round(x, 2) for x in load],
            "processes": len(glob.glob("/proc/[0-9]*")), "time": int(time.time()),
        })
        return {
            "system": info,
            "cpu": {"usage": cur["cpu"].get("cpu"), "cores": self._cpu_cores(cur["cpu"])},
            "gpus": self._gpus(),
            "memory": {
                "total": total, "available": avail, "used": total - avail,
                "percent": round(100.0 * (total - avail) / total, 1) if total else None,
                "cached": mem.get("Cached", 0), "buffers": mem.get("Buffers", 0),
                "swap_total": swap_total, "swap_used": swap_total - mem.get("SwapFree", 0),
            },
            "temps": temps,
            "temp_max": max((t["temp"] for t in temps), default=None),
            "power": self._power(),
            "storage": self._cached("storage", 10, self._storage),
            "network": self._network(cur["net"], cur.get("net_totals", {})),
            "activity": self.activity(),
            "history": hist,
        }


# --------------------------------------------------------------------------
# ROM library
# --------------------------------------------------------------------------

# Subfolders that hold media rather than games
SKIP_DIRS = {
    "images", "videos", "media", "manuals", "downloaded_images", "downloaded_videos",
    "downloaded_media", "music", "thumbnails", "marquees", "snap", "snaps", "boxart", "wheel",
    "fanart", "screenshots", "titleshots", "bezels", "maps", "covers",
}
SKIP_FILES = {"gamelist.xml", "gamelist.xml.bak", "_info.txt", "systeminfo.txt", "readme.txt"}
# Extensions ignored when a system declares no extensions in es_systems.cfg
NON_ROM_EXTS = {".xml", ".txt", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".pdf",
                ".bak", ".part", ".tmp", ".srm", ".state", ".nfo", ".ini", ".cfg", ".db", ".md"}
MEDIA_TAGS = ("image", "thumbnail", "marquee", "video", "fanart", "titleshot", "manual",
              "boxart", "boxback", "wheel", "mix", "map", "cartridge", "bezel", "magazine")


def load_system_icons():
    """System folder -> icon URL, from static/icons/systems/map.json (see tools/build_icons.py)."""
    try:
        with open(os.path.join(ICONS_DIR, "map.json"), encoding="utf-8") as f:
            mapping = json.load(f)
    except (OSError, ValueError):
        return {}
    return {system: "/static/icons/systems/" + name for system, name in mapping.items()
            if os.path.isfile(os.path.join(ICONS_DIR, name))}


class Library:
    # After this many seconds a cached system is still served, but rescanned in the background
    TTL = 300

    def __init__(self, cfg):
        self.cfg = cfg
        self.icons = load_system_icons()
        self.roms_dir = os.path.normpath(os.path.abspath(cfg["roms_dir"]))
        self.lock = threading.RLock()
        self._cache = {}
        self._gen = {}           # bumped by invalidate(): a scan started earlier must not be stored
        self._gen_all = 0
        self._scan_locks = {}    # one scan per system at a time
        self._refreshing = set()
        self._defs = None
        self._defs_t = 0.0

    # -- system definitions (es_systems.cfg) -------------------------------

    def system_defs(self):
        if self._defs is not None and time.time() - self._defs_t < 300:
            return self._defs
        defs = {}
        files = []
        for pattern in self.cfg["es_systems_files"]:
            files.extend(sorted(glob.glob(pattern)))
        for path in files:
            try:
                root = ET.parse(path).getroot()
            except (OSError, ET.ParseError) as e:
                log.warning("cannot read %s: %s", path, e)
                continue
            for node in root.iter("system"):
                name = (node.findtext("name") or "").strip()
                sys_path = (node.findtext("path") or "").strip()
                folder = os.path.basename(os.path.normpath(sys_path)) if sys_path else name
                if not folder:
                    continue
                exts = set()
                for ext in (node.findtext("extension") or "").split():
                    exts.add((ext if ext.startswith(".") else "." + ext).lower())
                d = defs.setdefault(folder, {"name": folder, "fullname": folder, "extensions": set(),
                                             "manufacturer": "", "release": "", "hardware": ""})
                d["extensions"] |= exts
                for key in ("fullname", "manufacturer", "release", "hardware"):
                    value = (node.findtext(key) or "").strip()
                    if value:
                        d[key] = value
        self._defs, self._defs_t = defs, time.time()
        return defs

    def system_dirs(self):
        try:
            return sorted(e.name for e in os.scandir(self.roms_dir)
                          if not e.name.startswith(".") and e.is_dir())
        except OSError:
            return []

    def systems(self, with_counts=True):
        defs = self.system_defs()
        out = []
        for name in self.system_dirs():
            d = defs.get(name, {})
            info = {
                "name": name, "fullname": d.get("fullname", name),
                "manufacturer": d.get("manufacturer", ""), "release": d.get("release", ""),
                "hardware": d.get("hardware", ""), "extensions": sorted(d.get("extensions", ())),
                "known": name in defs,
                "icon": self.icons.get(name),
            }
            if with_counts:
                roms = self.get_system(name)
                info["count"] = len(roms)
                info["size"] = sum(r["size"] for r in roms)
                info["favorites"] = sum(1 for r in roms if r["favorite"])
            out.append(info)
        return out

    # -- scanning ----------------------------------------------------------

    def _signature(self, sysdir):
        sig = []
        for path in (sysdir, os.path.join(sysdir, "gamelist.xml")):
            try:
                sig.append(os.stat(path).st_mtime_ns)
            except OSError:
                sig.append(0)
        return tuple(sig)

    def invalidate(self, system=None):
        with self.lock:
            if system:
                self._cache.pop(system, None)
                self._gen[system] = self._gen.get(system, 0) + 1
            else:
                self._cache.clear()
                self._gen_all += 1

    def get_system(self, name, force=False):
        sysdir = os.path.join(self.roms_dir, name)
        requested = time.time()
        sig = self._signature(sysdir)
        with self.lock:
            hit = self._cache.get(name)
            if not force and hit and hit["sig"] == sig:
                if requested - hit["t"] >= self.TTL:
                    self._refresh_later(name)
                return hit["roms"]
            scan_lock = self._scan_locks.setdefault(name, threading.Lock())
        with scan_lock:
            # another request may have scanned it while we were waiting
            with self.lock:
                hit = self._cache.get(name)
                if hit and (hit["t"] >= requested or (not force and hit["sig"] == self._signature(sysdir))):
                    return hit["roms"]
                gen = (self._gen_all, self._gen.get(name, 0))
            now = time.time()
            sig = self._signature(sysdir)
            roms = self._scan(name, sysdir)
            with self.lock:
                if (self._gen_all, self._gen.get(name, 0)) == gen:
                    self._cache[name] = {"sig": sig, "t": now, "roms": roms}
            return roms

    def _refresh_later(self, name):
        """Rescan a system in the background (caller holds self.lock)."""
        if name in self._refreshing:
            return

        def run():
            try:
                self.get_system(name, force=True)
            except Exception:
                log.exception("background scan of %s failed", name)
            finally:
                with self.lock:
                    self._refreshing.discard(name)

        self._refreshing.add(name)
        threading.Thread(target=run, daemon=True).start()

    def warm_up(self):
        """Scan the whole library in the background, so the first visit finds it ready."""
        def run():
            t = time.time()
            try:
                self.all_roms()
            except Exception:
                log.exception("library warm-up failed")
            log.info("library scanned in %.1fs", time.time() - t)

        threading.Thread(target=run, daemon=True).start()

    def all_roms(self, force=False):
        roms = []
        for name in self.system_dirs():
            roms.extend(self.get_system(name, force))
        return roms

    def _gl_rel(self, sysdir, path):
        path = (path or "").strip()
        if not path:
            return None
        full = os.path.normpath(os.path.join(sysdir, path[2:] if path.startswith("./") else path))
        if not is_within(full, sysdir):
            return None
        return os.path.relpath(full, sysdir).replace(os.sep, "/")

    def _read_gamelist(self, sysdir):
        out = {}
        try:
            root = ET.parse(os.path.join(sysdir, "gamelist.xml")).getroot()
        except (OSError, ET.ParseError):
            return out
        for game in root.findall("game"):
            rel = self._gl_rel(sysdir, game.findtext("path"))
            if rel is None:
                continue
            meta, media = {}, {}
            for child in game:
                text = (child.text or "").strip()
                if not text:
                    continue
                if child.tag in MEDIA_TAGS:
                    media[child.tag] = text
                elif child.tag != "path":
                    meta[child.tag] = text
            meta["_media"] = media
            out[rel] = meta
        return out

    def _media_url(self, sysdir, path):
        if not path:
            return None
        if path.startswith("./"):
            path = path[2:]
        full = os.path.normpath(os.path.join(sysdir, path))
        if not is_within(full, self.roms_dir):
            return None
        return "/api/media?p=" + urllib.parse.quote(os.path.relpath(full, self.roms_dir).replace(os.sep, "/"))

    def _scan(self, system, sysdir):
        exts = self.system_defs().get(system, {}).get("extensions") or set()
        meta = self._read_gamelist(sysdir)
        # Media found by naming convention (images/<name>-image.png etc.) for unscraped ROMs
        loose_media = {}
        for sub in ("images", "videos"):
            try:
                for e in os.scandir(os.path.join(sysdir, sub)):
                    loose_media[e.name.lower()] = sub + "/" + e.name
            except OSError:
                pass
        roms = []

        def walk(directory, rel, depth):
            try:
                entries = list(os.scandir(directory))
            except OSError:
                return
            for entry in entries:
                name = entry.name
                if name.startswith("."):
                    continue
                rel_path = rel + "/" + name if rel else name
                ext = os.path.splitext(name)[1].lower()
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    continue
                if is_dir:
                    if exts and ext in exts:
                        roms.append(self._make(system, sysdir, rel_path, entry, True, meta, loose_media))
                    elif depth < 3 and name.lower() not in SKIP_DIRS:
                        walk(entry.path, rel_path, depth + 1)
                elif name.lower() not in SKIP_FILES:
                    if (ext in exts) if exts else (ext and ext not in NON_ROM_EXTS):
                        roms.append(self._make(system, sysdir, rel_path, entry, False, meta, loose_media))

        walk(sysdir, "", 0)
        return roms

    def _make(self, system, sysdir, rel, entry, is_dir, meta, loose_media):
        m = meta.get(rel, {})
        try:
            st = entry.stat()
        except OSError:
            st = None
        base = os.path.basename(rel)
        stem = os.path.splitext(base)[0]
        rating = None
        try:
            rating = round(max(0.0, min(1.0, float(m["rating"]))) * 5, 1)
        except (KeyError, ValueError):
            pass
        release = es_date(m.get("releasedate"))
        year = int(release[:4]) if release and 1950 <= int(release[:4]) <= 2100 else None
        players = [int(x) for x in re.findall(r"\d+", m.get("players", ""))]

        def to_int(key):
            try:
                return int(float(m.get(key, 0)))
            except ValueError:
                return 0

        media = dict(m.get("_media", {}))
        if not media:
            for tag, suffixes in (("image", ("-image", "-screenshot", "")), ("thumbnail", ("-thumb", "-boxart")),
                                  ("marquee", ("-marquee", "-wheel")), ("video", ("-video",))):
                for suffix in suffixes:
                    for ext in (".png", ".jpg", ".jpeg", ".mp4"):
                        hit = loose_media.get((stem + suffix + ext).lower())
                        if hit and tag not in media:
                            media[tag] = hit
        return {
            "id": system + "/" + rel,
            "system": system,
            "path": rel,
            "file": base,
            "isDir": is_dir,
            "size": dir_size(entry.path) if is_dir else (st.st_size if st else 0),
            "mtime": int(st.st_mtime) if st else 0,
            "name": m.get("name") or stem,
            "desc": m.get("desc", ""),
            "developer": m.get("developer", ""),
            "publisher": m.get("publisher", ""),
            "genre": m.get("genre", ""),
            "players": m.get("players", ""),
            "playersMax": max(players) if players else None,
            "rating": rating,
            "releasedate": release,
            "year": year,
            "region": m.get("region", ""),
            "lang": m.get("lang", ""),
            "favorite": m.get("favorite", "").lower() == "true",
            "hidden": m.get("hidden", "").lower() == "true",
            "kidgame": m.get("kidgame", "").lower() == "true",
            "playcount": to_int("playcount"),
            "lastplayed": es_date(m.get("lastplayed")),
            "gametime": to_int("gametime"),
            "inGamelist": bool(m),
            "image": self._media_url(sysdir, media.get("image") or media.get("mix")),
            "thumbnail": self._media_url(sysdir, media.get("thumbnail") or media.get("boxart")),
            "marquee": self._media_url(sysdir, media.get("marquee") or media.get("wheel")),
            "video": self._media_url(sysdir, media.get("video")),
            "manual": self._media_url(sysdir, media.get("manual")),
        }

    # -- safe path resolution ----------------------------------------------

    def resolve(self, rom_id):
        """id 'system/relative/path' -> (system, system dir, rel, absolute path)."""
        if not rom_id or "\x00" in rom_id:
            raise ValueError("invalid id")
        parts = rom_id.replace("\\", "/").split("/")
        if len(parts) < 2 or any(p in ("", ".", "..") for p in parts):
            raise ValueError("invalid id")
        sysdir = os.path.join(self.roms_dir, parts[0])
        full = os.path.normpath(os.path.join(sysdir, *parts[1:]))
        if not is_within(full, sysdir):
            raise ValueError("invalid id")
        return parts[0], sysdir, "/".join(parts[1:]), full

    def find(self, rom_id):
        """Return the ROM if the id matches a game present in the library."""
        system, _sysdir, _rel, _full = self.resolve(rom_id)
        for rom in self.get_system(system):
            if rom["id"] == rom_id:
                return rom
        return None

    def media_path(self, rel):
        if not rel or "\x00" in rel:
            return None
        parts = rel.replace("\\", "/").split("/")
        if any(p in ("", ".", "..") for p in parts):
            return None
        full = os.path.normpath(os.path.join(self.roms_dir, *parts))
        if not is_within(full, self.roms_dir) or os.path.splitext(full)[1].lower() not in MEDIA_EXTS:
            return None
        return full if os.path.isfile(full) else None

    # -- upload / deletion -------------------------------------------------

    def upload_target(self, system, filename, overwrite=False):
        if system not in self.system_dirs():
            raise ValueError("unknown system: %s" % system)
        name = (filename or "").strip()
        if (not name or name.startswith(".") or "/" in name or "\\" in name or "\x00" in name
                or len(name.encode("utf-8")) > 255):
            raise ValueError("invalid file name")
        sysdir = os.path.join(self.roms_dir, system)
        target = os.path.join(sysdir, name)
        if os.path.lexists(target) and not overwrite:
            raise FileExistsError("file %s already exists" % name)
        if os.path.isdir(target):
            raise FileExistsError("a folder named %s already exists" % name)
        return sysdir, target

    def delete(self, ids, delete_media=True):
        results, by_system = [], collections.defaultdict(set)
        for rom_id in ids:
            try:
                rom = self.find(rom_id)
                system, sysdir, rel, full = self.resolve(rom_id)
            except ValueError as e:
                results.append({"id": rom_id, "ok": False, "error": str(e)})
                continue
            if rom is None or not os.path.lexists(full):
                results.append({"id": rom_id, "ok": False, "error": "ROM not found"})
                continue
            try:
                if os.path.isdir(full) and not os.path.islink(full):
                    shutil.rmtree(full)
                else:
                    os.remove(full)
                by_system[system].add(rel)
                results.append({"id": rom_id, "ok": True})
                log.warning("ROM deleted: %s", full)
            except OSError as e:
                results.append({"id": rom_id, "ok": False, "error": e.strerror or str(e)})
        for system, rels in by_system.items():
            sysdir = os.path.join(self.roms_dir, system)
            try:
                media = self._remove_from_gamelist(sysdir, rels)
            except Exception:
                log.exception("gamelist update failed for %s", system)
                media = []
            if delete_media:
                for path in media:
                    try:
                        if os.path.isfile(path):
                            os.remove(path)
                    except OSError:
                        pass
            self.invalidate(system)
        return results

    def _remove_from_gamelist(self, sysdir, rels):
        path = os.path.join(sysdir, "gamelist.xml")
        if not os.path.isfile(path):
            return []
        with self.lock:
            tree = ET.parse(path)
            root = tree.getroot()
            removed, kept, changed = set(), set(), False
            for game in list(root):
                if game.tag not in ("game", "folder"):
                    continue
                media = {os.path.normpath(os.path.join(sysdir, (c.text or "").strip()))
                         for c in game if c.tag in MEDIA_TAGS and (c.text or "").strip()}
                if game.tag == "game" and self._gl_rel(sysdir, game.findtext("path")) in rels:
                    root.remove(game)
                    removed |= media
                    changed = True
                else:
                    kept |= media
            if changed:
                self._save_gamelist(tree, path)
        # Only delete media no longer referenced by other games
        return [m for m in removed - kept if is_within(m, sysdir)]

    @staticmethod
    def _save_gamelist(tree, path):
        """Atomically write gamelist.xml, saving a .bak copy first."""
        if os.path.isfile(path):
            shutil.copy2(path, path + ".bak")
        if hasattr(ET, "indent"):
            ET.indent(tree, space="\t")
        tmp = path + ".tmp"
        tree.write(tmp, encoding="utf-8", xml_declaration=True)
        os.replace(tmp, path)

    # -- favorites ---------------------------------------------------------

    def set_favorite(self, ids, favorite):
        results, by_system = [], collections.defaultdict(dict)
        for rom_id in ids:
            try:
                rom = self.find(rom_id)
                system, _sysdir, rel, _full = self.resolve(rom_id)
            except ValueError as e:
                results.append({"id": rom_id, "ok": False, "error": str(e)})
                continue
            if rom is None:
                results.append({"id": rom_id, "ok": False, "error": "ROM not found"})
                continue
            by_system[system][rel] = rom_id
        for system, rels in by_system.items():
            sysdir = os.path.join(self.roms_dir, system)
            try:
                self._favorite_in_gamelist(sysdir, set(rels), favorite)
                results.extend({"id": rom_id, "ok": True} for rom_id in rels.values())
            except (OSError, ET.ParseError) as e:
                log.exception("favorites update failed for %s", system)
                error = getattr(e, "strerror", None) or str(e)
                results.extend({"id": rom_id, "ok": False, "error": error} for rom_id in rels.values())
            self.invalidate(system)
        return results

    def _favorite_in_gamelist(self, sysdir, rels, favorite):
        path = os.path.join(sysdir, "gamelist.xml")
        with self.lock:
            if os.path.isfile(path):
                tree = ET.parse(path)
            else:
                tree = ET.ElementTree(ET.Element("gameList"))
            root = tree.getroot()
            found, changed = set(), False
            for game in root.findall("game"):
                rel = self._gl_rel(sysdir, game.findtext("path"))
                if rel not in rels:
                    continue
                found.add(rel)
                nodes = game.findall("favorite")
                if favorite:
                    if [(n.text or "").strip().lower() for n in nodes] != ["true"]:
                        for node in nodes:
                            game.remove(node)
                        ET.SubElement(game, "favorite").text = "true"
                        changed = True
                elif nodes:
                    # EmulationStation omits default values: "not a favorite" = no tag
                    for node in nodes:
                        game.remove(node)
                    changed = True
            if favorite:
                # ROMs not yet in gamelist.xml: create a minimal entry
                for rel in sorted(rels - found):
                    game = ET.SubElement(root, "game")
                    ET.SubElement(game, "path").text = "./" + rel
                    ET.SubElement(game, "name").text = os.path.splitext(os.path.basename(rel))[0]
                    ET.SubElement(game, "favorite").text = "true"
                    changed = True
            if changed:
                self._save_gamelist(tree, path)



# --------------------------------------------------------------------------
# EmulationStation collections
# --------------------------------------------------------------------------

# Characters EmulationStation accepts in a collection name (getValidNewCollectionName)
COLLECTION_NAME_RE = re.compile(r"^[A-Za-z0-9\-\[\]() ]{1,64}$")
# Names used by EmulationStation's automatic collections
RESERVED_COLLECTIONS = {"all", "recent", "favorites", "2players", "4players", "neverplayed",
                        "retroachievements", "arcade", "vertical", "lightgun", "wheel", "trackball",
                        "spinner", "collections"}
ENABLED_SETTING = "CollectionSystemsCustom"


class CollectionError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Collections:
    """EmulationStation collections stored in `collections_dir`:

    * custom-<name>.cfg: custom collection, one ROM path per line, written by ES
      relative to /userdata ("./roms/snes/game.zip"); "~/" and absolute paths are
      accepted too;
    * <name>.xcc: dynamic collection built from ES filters (contents not editable here).

    Which ones EmulationStation shows is the comma-separated `CollectionSystemsCustom`
    setting in es_settings.cfg. ES reads it only at startup, so new, renamed or
    re-enabled collections need an EmulationStation restart to appear.
    """

    def __init__(self, cfg, library):
        self.dir = cfg["collections_dir"]
        self.settings_path = cfg["es_settings_file"]
        self.root = os.path.normpath(cfg["userdata_dir"])
        self.home = os.path.join(self.root, "system")
        self.library = library
        self.lock = threading.RLock()

    # -- files ----------------------------------------------------------------

    def _custom_path(self, name):
        return os.path.join(self.dir, "custom-%s.cfg" % name)

    def _dynamic_path(self, name):
        return os.path.join(self.dir, "%s.xcc" % name)

    def _names(self):
        """{name: "custom" | "dynamic"} for the collection files on disk."""
        out = {}
        try:
            entries = sorted(os.listdir(self.dir))
        except OSError:
            return out
        for fn in entries:
            if not os.path.isfile(os.path.join(self.dir, fn)):
                continue
            if fn.startswith("custom-") and fn.endswith(".cfg") and fn != "custom-.cfg":
                out[fn[7:-4]] = "custom"
            elif fn.endswith(".xcc") and len(fn) > 4:
                out[fn[:-4]] = "dynamic"
        return out

    def _resolve_line(self, line):
        line = line.strip()
        # same rule as EmulationStation: empty lines, comments and lines starting with "0" are skipped
        if not line or line[0] in "#0":
            return None
        if line.startswith("./"):
            path = os.path.join(self.root, line[2:])
        elif line.startswith("~/"):
            path = os.path.join(self.home, line[2:])
        else:
            path = line
        return os.path.normpath(path)

    def _to_line(self, path):
        if is_within(path, self.root):
            return "./" + os.path.relpath(path, self.root).replace(os.sep, "/")
        return path

    def _rom_id(self, path):
        if path and is_within(path, self.library.roms_dir):
            return os.path.relpath(path, self.library.roms_dir).replace(os.sep, "/")
        return None

    def _read_lines(self, name):
        try:
            with open(self._custom_path(name), encoding="utf-8", errors="replace") as f:
                return f.read().splitlines()
        except FileNotFoundError:
            raise CollectionError(404, "collection not found: %s" % name)

    def _write_lines(self, name, lines):
        path = self._custom_path(name)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("".join(line + "\n" for line in lines))
        os.replace(tmp, path)

    # -- es_settings.cfg -----------------------------------------------------

    def _settings_tree(self):
        try:
            tree = ET.parse(self.settings_path)
        except FileNotFoundError:
            tree = ET.ElementTree(ET.Element("config"))
        except ET.ParseError as e:
            raise CollectionError(500, "cannot parse %s: %s" % (self.settings_path, e))
        return tree

    def enabled(self):
        try:
            tree = self._settings_tree()
        except CollectionError:
            return []
        for node in tree.getroot().iter("string"):
            if node.get("name") == ENABLED_SETTING:
                return [n for n in (node.get("value") or "").split(",") if n]
        return []

    def _set_enabled(self, names):
        tree = self._settings_tree()
        root = tree.getroot()
        node = next((n for n in root.iter("string") if n.get("name") == ENABLED_SETTING), None)
        if node is None:
            node = ET.SubElement(root, "string", {"name": ENABLED_SETTING})
        node.set("value", ",".join(names))
        os.makedirs(os.path.dirname(self.settings_path) or ".", exist_ok=True)
        if os.path.isfile(self.settings_path):
            shutil.copy2(self.settings_path, self.settings_path + ".bak")
        if hasattr(ET, "indent"):
            ET.indent(tree, space="\t")
        tmp = self.settings_path + ".tmp"
        tree.write(tmp, encoding="utf-8", xml_declaration=True)
        os.replace(tmp, self.settings_path)

    def _replace_enabled(self, old, new=None):
        names = [n for n in self.enabled() if n != old]
        if new and new not in names:
            names.append(new)
        self._set_enabled(names)

    def apply_enabled(self, changes):
        """Apply {name: enabled} to CollectionSystemsCustom."""
        with self.lock:
            names = [n for n in self.enabled() if changes.get(n, True)]
            names += [n for n, on in changes.items() if on and n not in names]
            self._set_enabled(names)

    # -- queries ---------------------------------------------------------------

    def list(self):
        known = {r["id"] for r in self.library.all_roms()}
        enabled = set(self.enabled())
        out = []
        with self.lock:
            for name, kind in self._names().items():
                info = {"name": name, "type": kind, "enabled": name in enabled, "ids": [], "missing": 0}
                if kind == "custom":
                    seen = set()
                    for line in self._read_lines(name):
                        path = self._resolve_line(line)
                        if path is None:
                            continue
                        rom_id = self._rom_id(path)
                        if rom_id in known:
                            if rom_id not in seen:
                                seen.add(rom_id)
                                info["ids"].append(rom_id)
                        else:
                            info["missing"] += 1
                out.append(info)
        out.sort(key=lambda c: c["name"].lower())
        return out

    def validate_name(self, name, current=None):
        name = (name or "").strip()
        if not COLLECTION_NAME_RE.match(name):
            raise CollectionError(400, "invalid name: use letters, digits, spaces and - [ ] ( ) (max 64)")
        lower = name.lower()
        if lower in RESERVED_COLLECTIONS:
            raise CollectionError(400, "%s is reserved by EmulationStation" % name)
        if lower in {s.lower() for s in self.library.system_dirs()}:
            raise CollectionError(400, "%s is the name of a system" % name)
        if any(n.lower() == lower and n != current for n in self._names()):
            raise CollectionError(409, "a collection named %s already exists" % name)
        return name

    def _require(self, name, kind=None):
        found = self._names().get(name)
        if found is None:
            raise CollectionError(404, "collection not found: %s" % name)
        if kind and found != kind:
            raise CollectionError(400, "%s is a dynamic collection: edit it in EmulationStation" % name)
        return found

    def _paths_for(self, ids):
        paths = {}
        for rom_id in ids:
            try:
                rom = self.library.find(rom_id)
            except ValueError:
                rom = None
            if rom is None:
                raise CollectionError(404, "ROM not found: %s" % rom_id)
            paths[rom_id] = os.path.normpath(self.library.resolve(rom_id)[3])
        return paths

    # -- changes ---------------------------------------------------------------

    def create(self, name, ids=(), enable=True):
        with self.lock:
            name = self.validate_name(name)
            paths = self._paths_for(ids)
            os.makedirs(self.dir, exist_ok=True)
            self._write_lines(name, [self._to_line(p) for p in paths.values()])
            if enable:
                self._replace_enabled(None, name)
            log.warning("collection created: %s", name)
            return name

    def rename(self, name, new_name):
        with self.lock:
            kind = self._require(name)
            new_name = self.validate_name(new_name, current=name)
            if new_name == name:
                return name
            if kind == "custom":
                os.replace(self._custom_path(name), self._custom_path(new_name))
            else:
                os.replace(self._dynamic_path(name), self._dynamic_path(new_name))
            if name in self.enabled():
                self._replace_enabled(name, new_name)
            return new_name

    def delete(self, name):
        with self.lock:
            kind = self._require(name)
            os.remove(self._custom_path(name) if kind == "custom" else self._dynamic_path(name))
            if name in self.enabled():
                self._replace_enabled(name)
            log.warning("collection deleted: %s", name)

    def set_enabled(self, name, enabled):
        """Returns False when the collection already was in that state."""
        with self.lock:
            self._require(name)
            if (name in self.enabled()) == enabled:
                return False
            self._replace_enabled(name, name if enabled else None)
            return True

    def add(self, name, ids):
        with self.lock:
            self._require(name, "custom")
            paths = self._paths_for(ids)
            lines = self._read_lines(name)
            present = {self._resolve_line(line) for line in lines}
            added = [p for p in paths.values() if p not in present]
            if added:
                self._write_lines(name, lines + [self._to_line(p) for p in added])
            return len(added)

    def remove(self, name, ids):
        with self.lock:
            self._require(name, "custom")
            targets = set()
            for rom_id in ids:
                try:
                    targets.add(os.path.normpath(self.library.resolve(rom_id)[3]))
                except ValueError:
                    raise CollectionError(400, "invalid id: %s" % rom_id)
            lines = self._read_lines(name)
            kept = [line for line in lines if self._resolve_line(line) not in targets]
            if len(kept) != len(lines):
                self._write_lines(name, kept)
            return len(lines) - len(kept)

    def prune(self, name):
        """Drop the entries pointing to ROMs that are no longer in the library."""
        with self.lock:
            self._require(name, "custom")
            known = {r["id"] for r in self.library.all_roms()}
            lines = self._read_lines(name)
            kept = [line for line in lines
                    if self._resolve_line(line) is None or self._rom_id(self._resolve_line(line)) in known]
            if len(kept) != len(lines):
                self._write_lines(name, kept)
            return len(lines) - len(kept)

    def forget(self, paths):
        """Remove deleted ROMs from every custom collection."""
        targets = {os.path.normpath(p) for p in paths}
        with self.lock:
            for name, kind in self._names().items():
                if kind != "custom":
                    continue
                try:
                    lines = self._read_lines(name)
                    kept = [line for line in lines if self._resolve_line(line) not in targets]
                    if len(kept) != len(lines):
                        self._write_lines(name, kept)
                except (OSError, CollectionError):
                    log.exception("cannot update collection %s", name)


def check_name(name):
    """A plain file name (no directories, not hidden), as accepted for uploads."""
    name = (name or "").strip()
    if (not name or name.startswith(".") or "/" in name or "\\" in name or "\x00" in name
            or len(name.encode("utf-8")) > 255):
        raise ValueError("invalid file name")
    return name


def safe_join(root, rel):
    """root + a relative path from a request, refusing anything outside root."""
    if not rel or "\x00" in rel:
        raise ValueError("invalid path")
    parts = rel.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise ValueError("invalid path")
    full = os.path.normpath(os.path.join(root, *parts))
    if not is_within(full, root):
        raise ValueError("invalid path")
    return full


def rom_index(roms):
    """{system: {name: rom}} and {name: rom} indexes by file name and by stem (lower case)."""
    by_system, everywhere = collections.defaultdict(dict), {}
    for rom in roms:
        for key in (rom["file"].lower(), os.path.splitext(rom["file"])[0].lower()):
            by_system[rom["system"]].setdefault(key, rom)
            everywhere.setdefault(key, rom)
    return by_system, everywhere


# --------------------------------------------------------------------------
# Saves and save states
# --------------------------------------------------------------------------

# Battery / memory-card saves written next to the save states
BATTERY_EXTS = {".srm", ".sav", ".eep", ".fla", ".sra", ".mpk", ".rtc", ".mcd", ".brm", ".nv", ".bkr",
                ".mcr", ".dsv", ".ldci", ".hi", ".nvram", ".psv", ".vmu", ".bin"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
# Used when no es_savestates.cfg is found: RetroArch, as configured by Batocera
DEFAULT_SAVESTATE_CONFIGS = [{
    "name": "libretro", "directory": "{{system}}",
    "file": "{{romfilename}}.state{{slot}}", "image": "{{romfilename}}.state{{slot}}.png",
    "autosave_file": "{{romfilename}}.state.auto", "autosave_image": "{{romfilename}}.state.auto.png",
}]


def _savestate_regex(pattern):
    """es_savestates.cfg file pattern -> regex, like EmulationStation's SaveStateConfig."""
    out = ""
    for token in re.split(r"(\{\{\w+\}\})", pattern):
        if token == "{{romfilename}}":
            out += "(?P<rom>.+)"
        elif token == "{{slot}}":
            out += "(?P<slot>[0-9]*)"
        elif token in ("{{slot0}}", "{{slot00}}", "{{slot2d}}"):
            out += "(?P<slot>[0-9]+)"
        else:
            out += re.escape(token)
    return re.compile("^" + out + "$")


def _savestate_fill(pattern, rom, slot):
    return (pattern.replace("{{romfilename}}", rom).replace("{{slot}}", "" if not slot else str(slot))
            .replace("{{slot0}}", str(slot)).replace("{{slot00}}", "%02d" % slot).replace("{{slot2d}}", "%02d" % slot))


class Saves:
    def __init__(self, cfg, library):
        self.cfg = cfg
        self.dir = os.path.normpath(cfg["saves_dir"])
        self.library = library

    def configs(self):
        found = DEFAULT_SAVESTATE_CONFIGS
        for path in self.cfg["es_savestates_files"]:
            try:
                root = ET.parse(path).getroot()
            except (OSError, ET.ParseError):
                continue
            out = [{k: node.get(k, "") for k in ("name", "directory", "file", "image", "autosave_file", "autosave_image")}
                   for node in root.iter("emulator")]
            out = [c for c in out if c["file"]]
            if out:
                found = out
                break
        return [dict(c, re_file=_savestate_regex(c["file"]),
                     re_auto=_savestate_regex(c["autosave_file"]) if c.get("autosave_file") else None) for c in found]

    def _directories(self, configs):
        """{relative dir: [(config, system or None)]} to scan."""
        try:
            subdirs = sorted(e.name for e in os.scandir(self.dir) if e.is_dir() and not e.name.startswith("."))
        except OSError:
            return {}
        systems = set(self.library.system_dirs())
        dirs = {d: [] for d in subdirs}
        for c in configs:
            d = c["directory"].replace("{{emulator}}", c["name"]).strip("/") or "{{system}}"
            if "{{core}}" in d:
                continue
            if "{{system}}" in d:
                for sub in subdirs:
                    dirs.setdefault(d.replace("{{system}}", sub), []).append((c, sub if sub in systems else None))
            else:
                dirs.setdefault(d, []).append((c, d if d in systems else None))
        return dirs

    def scan(self):
        by_system, everywhere = rom_index(self.library.all_roms())
        systems = set(self.library.system_dirs())
        groups = {}

        def group_for(system, key):
            lookup = by_system.get(system, {}) if system else everywhere
            rom = lookup.get(key.lower())
            gid = rom["id"] if rom else "%s/?%s" % (system or "", key)
            if gid not in groups:
                groups[gid] = {"id": gid, "romId": rom["id"] if rom else None, "system": rom["system"] if rom else system,
                               "name": rom["name"] if rom else key, "key": key, "files": []}
            return groups[gid]

        for rel, configs in self._directories(self.configs()).items():
            full = os.path.join(self.dir, *rel.split("/"))
            try:
                entries = {e.name: e for e in os.scandir(full) if e.is_file() and not e.name.startswith(".")}
            except OSError:
                continue
            system = next((sys_ for _c, sys_ in configs if sys_), rel if rel in systems else None)
            used = set()

            def record(name, kind, slot=None, image=None):
                st = entries[name].stat()
                used.add(name)
                if image:
                    used.add(image)
                return {"path": rel + "/" + name, "name": name, "kind": kind, "slot": slot, "size": st.st_size,
                        "mtime": int(st.st_mtime), "image": rel + "/" + image if image else None}

            # pass 1: save states (their screenshots are consumed with them)
            for name in sorted(entries):
                for c, _sys in configs:
                    m, kind = c["re_file"].match(name), "state"
                    if not m and c["re_auto"]:
                        m, kind = c["re_auto"].match(name), "auto"
                    if not m:
                        continue
                    rom = m.group("rom")
                    slot = int(m.groupdict().get("slot") or 0) if kind == "state" else None
                    image = name + ".png" if name + ".png" in entries else None
                    pattern = c["image"] if kind == "state" else c.get("autosave_image")
                    if not image and pattern:
                        candidate = _savestate_fill(pattern, rom, slot or 0)
                        image = candidate if candidate in entries else None
                    group_for(system, rom)["files"].append(record(name, kind, slot, image))
                    break
            # pass 2: battery saves and anything else left in the folder
            for name in sorted(entries):
                if name in used:
                    continue
                stem, ext = os.path.splitext(name)
                kind = "save" if ext.lower() in BATTERY_EXTS else "other"
                group_for(system, stem)["files"].append(record(name, kind))

        out = []
        for g in groups.values():
            g["files"].sort(key=lambda f: ({"save": 0, "auto": 1, "state": 2, "other": 3}[f["kind"]], f["slot"] or 0, f["name"]))
            g["size"] = sum(f["size"] for f in g["files"])
            g["mtime"] = max(f["mtime"] for f in g["files"])
            out.append(g)
        out.sort(key=lambda g: -g["mtime"])
        return out

    def path(self, rel):
        full = safe_join(self.dir, rel)
        if not os.path.isfile(full):
            raise FileNotFoundError(rel)
        return full

    def related(self, rels):
        """The files plus the screenshots of the save states among them."""
        images = {f["path"]: f["image"] for g in self.scan() for f in g["files"] if f["image"]}
        out = []
        for rel in rels:
            out.append(rel)
            if images.get(rel):
                out.append(images[rel])
        return list(dict.fromkeys(out))

    def delete(self, rels):
        results = []
        for rel in self.related(rels):
            try:
                os.remove(self.path(rel))
                results.append({"path": rel, "ok": True})
                log.warning("save deleted: %s", rel)
            except (ValueError, OSError) as e:
                results.append({"path": rel, "ok": False, "error": getattr(e, "strerror", None) or str(e)})
        return results

    def upload_dir(self, rel):
        """Existing folder (or a system folder, created on demand) for an uploaded save."""
        full = safe_join(self.dir, rel)
        if not os.path.isdir(full):
            if rel in self.library.system_dirs():
                os.makedirs(full, exist_ok=True)
            else:
                raise ValueError("unknown saves folder: %s" % rel)
        return full


# --------------------------------------------------------------------------
# BIOS check
# --------------------------------------------------------------------------

# readme.txt system names that do not end with the EmulationStation name of their system
README_ALIASES = {"msx2+": "msx2+", "msx laserdisc": "msx1", "msx-turbo": "msxturbor",
                  "nintendo 64dd": "n64dd", "apple 2 gs": "apple2gs"}


class Bios:
    """Checks the BIOS files listed by Batocera's `batocera-systems` script.

    The `systems` dictionary is read from the script with ast.literal_eval (no code is
    executed). Each entry is {"file": "bios/x.bin", "md5": "...", optional "zippedFile",
    "altmd5", "emulator", "core"}; an empty md5 means any content is accepted.

    Some Knulli images do not ship the script, only the readme.txt generated from it at
    build time (`batocera-systems --createReadme`): "<system full name>:" followed by
    "<md5> <file>" lines. It has no zippedFile information, so the md5 of a .zip entry
    is accepted when it matches the archive or any file inside it.
    """

    def __init__(self, cfg, library):
        self.cfg = cfg
        self.root = os.path.normpath(cfg["userdata_dir"])
        self.library = library
        self.lock = threading.Lock()
        self._db = (None, None)  # ((path, mtime), systems)
        self.source = None
        self._md5 = {}           # path -> (size, mtime_ns, md5)
        self._zip = {}           # path -> (size, mtime_ns, {lower-case entry name: md5})
        self.error = None

    def _sources(self):
        """Candidate BIOS lists, in order: (kind, path)."""
        out = [("script", self.cfg["bios_db"])]
        found = shutil.which("batocera-systems")
        if found:
            out.append(("script", found))
        out += [("readme", p) for p in self.cfg.get("bios_readme_files", [])]
        return out

    def db(self):
        tried = []
        for kind, path in self._sources():
            try:
                mtime = os.stat(path).st_mtime_ns
            except OSError:
                tried.append(path)
                continue
            if self._db[0] == (path, mtime):
                return self._db[1]
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
                systems = self._parse_script(text) if kind == "script" else self._parse_readme(text)
            except (OSError, SyntaxError, ValueError, StopIteration) as e:
                log.warning("cannot read the BIOS list %s: %s", path, e)
                tried.append(path)
                continue
            if not systems:
                tried.append(path)
                continue
            self.error, self.source = None, path
            self._db = ((path, mtime), systems)
            return systems
        self.error = "No BIOS list found. Looked for: %s" % ", ".join(dict.fromkeys(tried))
        self.source = None
        return {}

    @staticmethod
    def _parse_script(text):
        tree = ast.parse(text)
        node = next(n for n in tree.body if isinstance(n, ast.Assign)
                    and any(getattr(t, "id", None) == "systems" for t in n.targets))
        return ast.literal_eval(node.value)

    def _readme_folder(self, name):
        """System folder of a readme.txt full name ("Nintendo Gameboy Advance" -> "gba")."""
        alias = README_ALIASES.get(name.lower())
        if alias:
            return alias
        key = lambda v: re.sub(r"[^a-z0-9]", "", v.lower())
        wanted, best = key(name), None
        for folder, info in self.library.system_defs().items():
            for candidate in (key(info.get("fullname", "")), key(folder)):
                # the readme prefixes the manufacturer: keep the longest matching ending
                if candidate and wanted.endswith(candidate) and (best is None or len(candidate) > best[0]):
                    best = (len(candidate), folder)
        return best[1] if best else wanted

    def _parse_readme(self, text):
        systems, current = {}, None
        for line in text.splitlines():
            if not line.strip():
                continue
            m = re.match(r"^\s*([0-9a-fA-F]{32})?\s*(\S.*?)\s*$", line)
            if line.rstrip().endswith(":") and not re.match(r"^\s*[0-9a-fA-F]{32}\s", line):
                name = line.strip()[:-1]
                current = systems.setdefault(self._readme_folder(name), {"name": name, "biosFiles": []})
            elif current is not None and m:
                entry = {"md5": (m.group(1) or "").lower(), "file": m.group(2)}
                if entry["file"].lower().endswith(".zip"):
                    entry["zipAny"] = True
                current["biosFiles"].append(entry)
        return systems

    def _file_md5(self, full):
        st = os.stat(full)
        hit = self._md5.get(full)
        if hit and hit[:2] == (st.st_size, st.st_mtime_ns):
            return hit[2]
        digest = hashlib.md5()
        with open(full, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                digest.update(block)
        self._md5[full] = (st.st_size, st.st_mtime_ns, digest.hexdigest())
        return digest.hexdigest()

    def _zip_members(self, full, cache=True):
        """{lower-case member name: md5} of a zip ({} if it is not a readable zip)."""
        st = os.stat(full)
        hit = self._zip.get(full) if cache else None
        if hit and hit[:2] == (st.st_size, st.st_mtime_ns):
            return hit[2]
        members = {}
        try:
            with zipfile.ZipFile(full) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    digest = hashlib.md5()
                    with zf.open(info) as entry:
                        for block in iter(lambda: entry.read(1 << 20), b""):
                            digest.update(block)
                    members[info.filename.lower()] = digest.hexdigest()
        except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
            members = {}
        if cache:
            self._zip[full] = (st.st_size, st.st_mtime_ns, members)
        return members

    def _zip_md5(self, full, inner):
        """md5 of `inner` inside the zip (None if absent or unreadable)."""
        return self._zip_members(full).get(inner.lower())

    def _status(self, entry):
        try:
            full = safe_join(self.root, entry["file"])
        except ValueError:
            return "missing"
        if not os.path.isfile(full):
            return "missing"
        accepted = {m for m in (entry.get("md5"), entry.get("altmd5")) if m}
        try:
            if entry.get("zippedFile"):
                found = self._zip_md5(full, entry["zippedFile"])
                if found is None:
                    return "missing"
                return "ok" if not accepted or found in accepted else "mismatch"
            if not accepted or self._file_md5(full) in accepted:
                return "ok"
            if entry.get("zipAny") and accepted & set(self._zip_members(full).values()):
                return "ok"
            return "mismatch"
        except OSError:
            return "missing"

    def check(self):
        with self.lock:
            db = self.db()
            defs = self.library.system_defs()
            roms = {s["name"]: s["count"] for s in self.library.systems()}
            out = []
            for name, info in db.items():
                if defs and name not in defs:
                    continue  # system not available on this device
                files = []
                for entry in info.get("biosFiles", []):
                    who = "/".join(x for x in (entry.get("emulator") or info.get("emulator"),
                                               entry.get("core") or info.get("core")) if x)
                    files.append({"file": entry["file"], "zipped": entry.get("zippedFile"), "md5": entry.get("md5", ""),
                                  "status": self._status(entry), "for": who})
                out.append({"name": name, "fullname": defs.get(name, {}).get("fullname") or info.get("name", name),
                            "roms": roms.get(name, 0), "files": files,
                            "ok": sum(1 for f in files if f["status"] == "ok"), "total": len(files)})
            out.sort(key=lambda x: x["fullname"].lower())
            return {"systems": out, "error": self.error, "database": self.source}

    def targets(self, name, md5, upload_path=None):
        """Where an uploaded BIOS named `name` with this md5 belongs: (paths, known, matched).
        An uploaded zip matches when one of its files has an expected md5."""
        db = self.db()
        members = None
        candidates = {}
        for info in db.values():
            for entry in info.get("biosFiles", []):
                if os.path.basename(entry["file"]).lower() != name.lower():
                    continue
                accepted = {m for m in (entry.get("md5"), entry.get("altmd5")) if m}
                if entry.get("zippedFile") or entry.get("zipAny"):
                    if members is None:
                        members = self._zip_members(upload_path, cache=False) if upload_path else {}
                    if entry.get("zippedFile"):
                        inner = members.get(entry["zippedFile"].lower())
                        ok = inner is not None and (not accepted or inner in accepted)
                    else:
                        ok = not accepted or md5 in accepted or bool(accepted & set(members.values()))
                else:
                    ok = not accepted or md5 in accepted
                candidates[entry["file"]] = candidates.get(entry["file"], False) or ok
        if not candidates:
            return ["bios/" + name], False, False
        matched = [p for p, ok in candidates.items() if ok]
        return (matched or sorted(candidates)), True, bool(matched)


# --------------------------------------------------------------------------
# Screenshots
# --------------------------------------------------------------------------

# Date suffixes added by RetroArch ("Game-240115-203011") and by Batocera's tools
CAPTURE_DATE_RE = re.compile(r"[-_ ](\d{6}[-_]\d{6}|\d{4}[.\-_]\d{2}[.\-_]\d{2}[-_ ].*)$")


class Captures:
    def __init__(self, cfg, library):
        # ids are "<folder>/<name>"
        self.folders = {"screenshots": os.path.normpath(cfg["screenshots_dir"])}
        self.library = library

    def list(self):
        _by_system, everywhere = rom_index(self.library.all_roms())
        out = []
        for folder, root in self.folders.items():
            try:
                entries = [e for e in os.scandir(root) if e.is_file() and not e.name.startswith(".")]
            except OSError:
                continue
            for e in entries:
                stem, ext = os.path.splitext(e.name)
                ext = ext.lower()
                if ext not in IMAGE_EXTS:
                    continue
                rom = everywhere.get(stem.lower()) or everywhere.get(CAPTURE_DATE_RE.sub("", stem).lower())
                st = e.stat()
                out.append({"id": folder + "/" + e.name, "name": e.name, "size": st.st_size, "mtime": int(st.st_mtime),
                            "romId": rom["id"] if rom else None, "system": rom["system"] if rom else None,
                            "game": rom["name"] if rom else None})
        out.sort(key=lambda c: -c["mtime"])
        return out

    def path(self, cid):
        folder, _, name = (cid or "").partition("/")
        if folder not in self.folders:
            raise ValueError("invalid id")
        full = safe_join(self.folders[folder], check_name(name))
        if not os.path.isfile(full):
            raise FileNotFoundError(cid)
        return full

    def delete(self, ids):
        results = []
        for cid in ids:
            try:
                os.remove(self.path(cid))
                results.append({"id": cid, "ok": True})
            except (ValueError, OSError) as e:
                results.append({"id": cid, "ok": False, "error": getattr(e, "strerror", None) or str(e)})
        return results


# --------------------------------------------------------------------------
# Live screen and remote control
# --------------------------------------------------------------------------

IOC_READ = 2
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
KEY_MAX, ABS_MAX = 0x2FF, 0x3F
BTN_JOYSTICK, ABS_HAT0X, ABS_HAT3Y = 0x120, 0x10, 0x17
FBIOGET_VSCREENINFO, FBIOGET_FSCREENINFO = 0x4600, 0x4602
INPUT_EVENT = struct.Struct("@llHHi")  # struct input_event (timeval, type, code, value)


def evdev_ioc(nr, size):
    return (IOC_READ << 30) | (size << 16) | (ord("E") << 8) | nr


def test_bit(bits, n):
    return 0 <= n < len(bits) * 8 and bits[n // 8] >> (n % 8) & 1


def sdl_layout(keybits, absbits):
    """Joystick button, axis and hat indexes as SDL2 numbers them (they are the ids in
    es_input.cfg) -> evdev codes. Hats are (x code, y code) pairs."""
    buttons = [c for c in range(BTN_JOYSTICK, KEY_MAX) if test_bit(keybits, c)]
    buttons += [c for c in range(BTN_JOYSTICK) if test_bit(keybits, c)]
    axes = [c for c in range(ABS_MAX) if not ABS_HAT0X <= c <= ABS_HAT3Y and test_bit(absbits, c)]
    hats = [(c, c + 1) for c in range(ABS_HAT0X, ABS_HAT3Y + 1, 2)
            if test_bit(absbits, c) or test_bit(absbits, c + 1)]
    return buttons, axes, hats


class ScreenError(Exception):
    pass


class Screen:
    """What the device is showing: grim on Wayland, the framebuffer, or a custom command."""
    MIN_INTERVAL = 0.03  # viewers asking at the same time share one capture

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self._frame = None
        self._frame_t = 0.0
        self._grim_type = "jpeg"

    @staticmethod
    def wayland_env():
        dirs = [os.environ.get("XDG_RUNTIME_DIR"), "/run", "/var/run", "/tmp"] + sorted(glob.glob("/run/user/*"))
        for d in filter(None, dirs):
            for path in sorted(glob.glob(os.path.join(d, "wayland-*"))):
                try:
                    if stat.S_ISSOCK(os.stat(path).st_mode):
                        return dict(os.environ, XDG_RUNTIME_DIR=d, WAYLAND_DISPLAY=os.path.basename(path))
                except OSError:
                    pass
        return None

    def backend(self):
        mode = self.cfg.get("screen_capture") or "auto"
        if mode == "command" or (mode == "auto" and self.cfg.get("screen_command")):
            return "command" if self.cfg.get("screen_command") else None
        if mode in ("auto", "grim") and shutil.which("grim") and self.wayland_env():
            return "grim"
        if mode in ("auto", "fbdev") and os.path.exists(self.cfg["framebuffer"]):
            return "fbdev"
        return None

    def frame(self):
        """{"type": mime type or "raw", "data": bytes, "etag": str, "format": raw pixel layout}."""
        with self.lock:
            if self._frame and time.time() - self._frame_t < self.MIN_INTERVAL:
                return self._frame
            backend = self.backend()
            if backend is None:
                raise ScreenError("no way to capture the screen was found (no Wayland compositor with grim, "
                                  "no framebuffer and no screen_command)")
            frame = getattr(self, "_capture_" + backend)()
            frame["etag"] = '"%08x"' % zlib.crc32(frame["data"])
            self._frame, self._frame_t = frame, time.time()
            return frame

    def _run(self, args, env=None):
        try:
            res = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                                 shell=isinstance(args, str), timeout=5)
        except (OSError, subprocess.SubprocessError) as e:
            raise ScreenError(str(e))
        if res.returncode or not res.stdout:
            raise ScreenError(res.stderr.decode("utf-8", "replace").strip() or "exit status %d" % res.returncode)
        return res.stdout

    def _capture_grim(self):
        env = self.wayland_env()
        if self._grim_type == "jpeg":
            try:
                quality = str(self.cfg.get("screen_jpeg_quality") or 70)
                return {"type": "image/jpeg", "data": self._run(["grim", "-t", "jpeg", "-q", quality, "-"], env)}
            except ScreenError:
                self._grim_type = "png"  # grim built without JPEG support
        return {"type": "image/png", "data": self._run(["grim", "-t", "png", "-l", "1", "-"], env)}

    def _capture_command(self):
        data = self._run(self.cfg["screen_command"])
        for magic, mime in ((b"\x89PNG", "image/png"), (b"\xff\xd8", "image/jpeg"), (b"BM", "image/bmp")):
            if data.startswith(magic):
                return {"type": mime, "data": data}
        raise ScreenError("screen_command did not print a PNG, JPEG or BMP image")

    def _capture_fbdev(self):
        try:
            with open(self.cfg["framebuffer"], "rb", buffering=0) as f:
                var, fix = bytearray(160), bytearray(128)
                fcntl.ioctl(f, FBIOGET_VSCREENINFO, var)
                fcntl.ioctl(f, FBIOGET_FSCREENINFO, fix)
                v = struct.unpack_from("=17I", var)
                xres, yres, xoffset, yoffset, bpp = v[0], v[1], v[4], v[5], v[6]
                line = struct.unpack_from("@16sLIIIIHHHI", fix)[-1]
                if bpp not in (16, 24, 32) or not xres or not yres:
                    raise ScreenError("unsupported framebuffer format (%d bits per pixel)" % bpp)
                # the page being shown (double buffering pans between pages)
                f.seek(yoffset * line + xoffset * bpp // 8)
                data = f.read(yres * line)
        except OSError as e:
            raise ScreenError("cannot read %s: %s" % (self.cfg["framebuffer"], e.strerror or e))
        fmt = {"w": xres, "h": yres, "stride": line, "bpp": bpp,
               "r": v[8:10], "g": v[11:13], "b": v[14:16]}  # (offset, length) of each colour
        return {"type": "raw", "data": data, "format": fmt}


class PadError(Exception):
    pass


class Pad:
    """Presses the buttons of the device's own controller by writing input events to its
    evdev node: EmulationStation and the emulators see them as coming from the real pad,
    with the mapping configured in EmulationStation (es_input.cfg)."""
    BUTTONS = ("up", "down", "left", "right", "a", "b", "x", "y", "pageup", "pagedown",
               "l2", "r2", "l3", "r3", "select", "start", "hotkey")
    HAT_DIRS = {1: (1, -1), 2: (0, 1), 4: (1, 1), 8: (0, -1)}  # SDL hat bit -> (x/y, value)
    HOLD_TIMEOUT = 6  # a button the page stops refreshing (closed tab, lost network) is released

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self._dev = None
        self._held = {}
        self._watchdog = None

    def _es_configs(self):
        out = []
        for path in self.cfg["es_input_files"]:
            try:
                root = ET.parse(path).getroot()
            except (OSError, ET.ParseError):
                continue
            for node in root.iter("inputConfig"):
                if node.get("type") != "joystick":
                    continue
                inputs = {}
                for i in node.iter("input"):
                    try:
                        code = int(i.get("code")) if i.get("code") else None
                        inputs[i.get("name")] = (i.get("type"), int(i.get("id")), int(i.get("value")), code)
                    except (TypeError, ValueError):
                        pass
                out.append((node.get("deviceName") or "", inputs))
        return out

    @staticmethod
    def _devices():
        nodes = glob.glob("/dev/input/event*")
        nodes.sort(key=lambda p: int(re.sub(r"\D", "", p) or 0))
        return [(p, read_file("/sys/class/input/%s/device/name" % os.path.basename(p), "")) for p in nodes]

    def _find(self):
        configs = self._es_configs()
        devices = self._devices()
        if self.cfg.get("pad_device"):
            devices = [d for d in devices if d[0] == self.cfg["pad_device"]] or [(self.cfg["pad_device"], "")]
        for path, name in devices:
            for es_name, inputs in configs:
                if es_name == name or (self.cfg.get("pad_device") and len(devices) == 1):
                    return path, name, inputs
        if not configs:
            raise PadError("no controller is configured in EmulationStation (es_input.cfg)")
        raise PadError("none of the controllers configured in EmulationStation is connected")

    def _open(self):
        path, name, inputs = self._find()
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        try:
            keybits, absbits = bytearray(KEY_MAX // 8 + 1), bytearray(ABS_MAX // 8 + 1)
            fcntl.ioctl(fd, evdev_ioc(0x20 + EV_KEY, len(keybits)), keybits)
            fcntl.ioctl(fd, evdev_ioc(0x20 + EV_ABS, len(absbits)), absbits)
            buttons, axes, hats = sdl_layout(keybits, absbits)
            mapping = {}
            for button in self.BUTTONS:
                kind, idx, value, code = inputs.get(button, (None, -1, 0, None))
                # Recent EmulationStation versions also save the evdev code, which is exact; the SDL
                # index is only a fallback for older configurations (some drivers number differently)
                if kind == "button":
                    code = code if test_bit(keybits, code or -1) else buttons[idx] if 0 <= idx < len(buttons) else None
                    if code is not None:
                        mapping[button] = (EV_KEY, code, 1, 0)
                elif kind == "hat" and 0 <= idx < len(hats) and value in self.HAT_DIRS:
                    xy, direction = self.HAT_DIRS[value]
                    mapping[button] = (EV_ABS, hats[idx][xy], direction, 0)
                elif kind == "axis":
                    code = code if test_bit(absbits, code or -1) else axes[idx] if 0 <= idx < len(axes) else None
                    if code is not None:
                        info = bytearray(24)  # struct input_absinfo: value, min, max, fuzz, flat, resolution
                        fcntl.ioctl(fd, evdev_ioc(0x40 + code, len(info)), info)
                        rest, lo, hi = struct.unpack_from("=3i", info)
                        mapping[button] = (EV_ABS, code, hi if value > 0 else lo, rest)
        except OSError:
            os.close(fd)
            raise
        return {"fd": fd, "path": path, "name": name, "map": mapping}

    def _device(self):
        if self._dev is None:
            try:
                self._dev = self._open()
            except OSError as e:
                raise PadError("cannot open the controller: %s" % (e.strerror or e))
        return self._dev

    def _close(self):
        if self._dev:
            try:
                os.close(self._dev["fd"])
            except OSError:
                pass
            self._dev = None

    def info(self):
        with self.lock:
            self._close()  # pick up a controller plugged in or reconfigured since last time
            dev = self._device()
            return {"device": dev["name"], "path": dev["path"], "buttons": sorted(dev["map"])}

    def _write(self, events):
        data = b"".join(INPUT_EVENT.pack(0, 0, t, c, v) for t, c, v in events + [(EV_SYN, 0, 0)])
        try:
            os.write(self._device()["fd"], data)
        except OSError:
            self._close()  # the controller went away: retry once with a fresh lookup
            try:
                os.write(self._device()["fd"], data)
            except OSError as e:
                raise PadError("cannot write to the controller: %s" % (e.strerror or e))

    def set(self, button, down):
        with self.lock:
            entry = self._device()["map"].get(button)
            if entry is None:
                raise PadError("the button '%s' is not mapped in EmulationStation" % button)
            etype, code, on, off = entry
            if down and button in self._held:
                self._held[button] = time.time()  # still held: just a keep-alive
                return
            self._write([(etype, code, on if down else off)])
            if down:
                self._held[button] = time.time()
                if not self._watchdog:
                    self._watchdog = threading.Thread(target=self._watch, daemon=True)
                    self._watchdog.start()
            else:
                self._held.pop(button, None)

    def release_all(self):
        with self.lock:
            held, self._held = list(self._held), {}
            if held and self._dev:
                try:
                    self._write([(e[0], e[1], e[3]) for e in (self._dev["map"][b] for b in held)])
                except PadError:
                    pass

    def _watch(self):
        while True:
            time.sleep(1)
            with self.lock:
                stale = [b for b, t in self._held.items() if time.time() - t > self.HOLD_TIMEOUT]
                if not self._held or stale == list(self._held):
                    self._watchdog = None
            for button in stale:
                try:
                    self.set(button, False)
                except PadError:
                    with self.lock:
                        self._held.pop(button, None)
            if self._watchdog is None:
                return


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.telemetry = Telemetry(cfg)
        self.library = Library(cfg)
        self.collections = Collections(cfg, self.library)
        self.saves = Saves(cfg, self.library)
        self.bios = Bios(cfg, self.library)
        self.captures = Captures(cfg, self.library)
        self.screen = Screen(cfg)
        self.pad = Pad(cfg)
        # EmulationStation reads CollectionSystemsCustom only at startup: remember the
        # changes the running instance (pid) has not seen yet
        self._restart_pid = None
        self._pending = {}
        self._pending_lock = threading.Lock()

    def mark_restart_needed(self, changes):
        """`changes` = {collection: enabled} written to es_settings.cfg."""
        pid = find_es_pid()
        if pid is None:
            return  # ES is not running: it will read the new settings when it starts
        with self._pending_lock:
            self._restart_pid = pid
            self._pending.update(changes)

    def restart_needed(self):
        with self._pending_lock:
            if self._restart_pid is None:
                return False
            pid = find_es_pid()
            if pid == self._restart_pid:
                return True
            # ES restarted or stopped. When it quits, ES saves its in-memory settings if
            # they were modified in its menus, which can drop our changes: check and re-apply.
            enabled = set(self.collections.enabled())
            existing = self.collections._names()
            lost = {n: on for n, on in self._pending.items() if (n in enabled) != on and (n in existing or not on)}
            if lost:
                log.warning("EmulationStation overwrote the collection settings, re-applying: %s", lost)
                self.collections.apply_enabled(lost)
            if lost and pid is not None:
                self._restart_pid, self._pending = pid, lost
                return True
            self._restart_pid, self._pending = None, {}
            return False

    def es_reload(self, force=False):
        if not (force or self.cfg.get("es_reload")):
            return

        def worker():
            try:
                _LOCAL_OPENER.open(self.cfg["es_api"] + "/reloadgames", timeout=15).read()
            except Exception as e:
                log.info("EmulationStation reload failed: %s", e)

        threading.Thread(target=worker, daemon=True).start()


def versioned_assets(html):
    """Append ?v=<mtime> to the page's CSS/JS links, so that browsers fetch
    the new files as soon as they are updated on the device."""
    def repl(m):
        try:
            stamp = int(os.path.getmtime(os.path.join(STATIC_DIR, m.group(2).decode())))
        except OSError:
            return m.group(0)
        return m.group(1) + m.group(2) + b"?v=%d" % stamp + m.group(3)
    return re.sub(rb'((?:href|src)="/static/)([\w.-]+\.(?:css|js))(")', repl, html)


class HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Handler(BaseHTTPRequestHandler):
    server_version = "KnulliWebUI/" + VERSION
    timeout = 300
    app = None  # type: App

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def _auth_ok(self):
        user, pwd = self.app.cfg.get("username") or "", self.app.cfg.get("password") or ""
        if not user and not pwd:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            u, _, p = base64.b64decode(header[6:]).decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        return hmac.compare_digest(u.encode(), user.encode()) & hmac.compare_digest(p.encode(), pwd.encode())

    def _dispatch(self, method):
        url = urllib.parse.urlsplit(self.path)
        self.query = urllib.parse.parse_qs(url.query)
        try:
            if not self._auth_ok():
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Knulli WebUI", charset="UTF-8"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            route = ROUTES.get((method, url.path))
            if route is None:
                if method == "GET" and (url.path == "/" or url.path.startswith("/static/")):
                    return self.serve_static(url.path)
                raise HttpError(404, "not found")
            fn, mutating = route
            if mutating and self.headers.get(CSRF_HEADER) != CSRF_VALUE:
                raise HttpError(403, "request not allowed")
            fn(self)
        except HttpError as e:
            self.send_json({"error": e.message}, e.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            log.exception("error handling %s %s", method, self.path)
            try:
                self.send_json({"error": "internal error: %s" % e}, 500)
            except OSError:
                pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def param(self, name, default=None):
        values = self.query.get(name)
        return values[0] if values else default

    def read_json(self, limit=4 << 20):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > limit:
            raise HttpError(400, "invalid request body")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise HttpError(400, "invalid JSON")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        gz = len(body) > 4096 and "gzip" in self.headers.get("Accept-Encoding", "")
        if gz:
            body = gzip.compress(body, 5)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, path):
        rel = "index.html" if path == "/" else path[len("/static/"):]
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not is_within(full, STATIC_DIR) or not os.path.isfile(full):
            raise HttpError(404, "not found")
        with open(full, "rb") as f:
            body = f.read()
        if rel == "index.html":
            body = versioned_assets(body)
        self.send_response(200)
        self.send_header("Content-Type", mime_for(full))
        self.send_header("Content-Length", str(len(body)))
        # Icons never change between releases; the app files must be revalidated
        self.send_header("Cache-Control", "max-age=604800" if rel.startswith("icons/") else "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, name, attachment=True, cache=None):
        size = os.path.getsize(path)
        start, end, status = 0, size - 1, 200
        m = re.match(r"^bytes=(\d*)-(\d*)$", self.headers.get("Range", "").strip())
        if m and size > 0 and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", mime_for(path))
        self.send_header("Content-Length", str(max(0, end - start + 1)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", content_disposition(name, attachment))
        if cache:
            self.send_header("Cache-Control", cache)
        if status == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        remaining = end - start + 1
        with open(path, "rb") as f:
            f.seek(start)
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def send_zip(self, entries, zip_name):
        """Streamed zip (uncompressed: ROMs are already compressed and the CPU is modest)."""
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", content_disposition(zip_name))
        self.end_headers()
        self.close_connection = True
        with zipfile.ZipFile(self.wfile, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
            for path, arcname in entries:
                if os.path.isdir(path):
                    for root, _dirs, files in os.walk(path):
                        for fn in sorted(files):
                            fp = os.path.join(root, fn)
                            zf.write(fp, arcname + "/" + os.path.relpath(fp, path).replace(os.sep, "/"))
                else:
                    zf.write(path, arcname)

    # -- endpoints ---------------------------------------------------------

    def api_config(self):
        cfg = self.app.cfg
        self.send_json({"version": VERSION, "allowUpload": bool(cfg["allow_upload"]),
                        "allowDelete": bool(cfg["allow_delete"]), "maxUploadMb": cfg["max_upload_mb"],
                        "allowRemoteControl": bool(cfg["allow_remote_control"]),
                        "hostname": socket.gethostname()})

    def api_telemetry(self):
        self.send_json(self.app.telemetry.snapshot())

    def api_roms(self):
        lib = self.app.library
        force = self.param("refresh") == "1"
        if force:
            lib.invalidate()
        roms = [{k: v for k, v in r.items() if k != "desc"} for r in lib.all_roms()]
        self.send_json({"roms": roms, "systems": lib.systems()})

    def api_systems(self):
        self.send_json({"systems": self.app.library.systems()})

    def api_rom(self):
        try:
            rom = self.app.library.find(self.param("id", ""))
        except ValueError as e:
            raise HttpError(400, str(e))
        if rom is None:
            raise HttpError(404, "ROM not found")
        self.send_json(rom)

    def api_media(self):
        path = self.app.library.media_path(self.param("p", ""))
        if not path:
            raise HttpError(404, "media not found")
        self.send_file(path, os.path.basename(path), attachment=False, cache="max-age=86400")

    def _download(self, ids):
        lib = self.app.library
        roms = []
        for rom_id in ids:
            try:
                rom = lib.find(rom_id)
            except ValueError as e:
                raise HttpError(400, str(e))
            if rom is None:
                raise HttpError(404, "ROM not found: %s" % rom_id)
            roms.append(rom)
        if not roms:
            raise HttpError(400, "no ROM specified")
        if len(roms) == 1 and not roms[0]["isDir"]:
            _s, _d, _r, full = lib.resolve(roms[0]["id"])
            return self.send_file(full, roms[0]["file"])
        multi_system = len({r["system"] for r in roms}) > 1
        entries = []
        for rom in roms:
            arc = (rom["system"] + "/" + rom["path"]) if multi_system else rom["path"]
            entries.append((lib.resolve(rom["id"])[3], arc))
        name = roms[0]["file"] + ".zip" if len(roms) == 1 else "knulli-roms-%s.zip" % time.strftime("%Y%m%d-%H%M%S")
        self.send_zip(entries, name)

    def api_download_get(self):
        self._download(self.query.get("id", []))

    def form_ids(self):
        """`ids` field (a JSON array) of a form POST, used by the multi-file downloads."""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 4 << 20:
            raise HttpError(400, "invalid request")
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        try:
            ids = json.loads(form.get("ids", ["[]"])[0])
        except ValueError:
            raise HttpError(400, "invalid list")
        if not isinstance(ids, list):
            raise HttpError(400, "invalid list")
        return [str(i) for i in ids]

    def api_download_post(self):
        """Multi-download via form POST (`ids` field holding a JSON array)."""
        self._download(self.form_ids())

    def upload_length(self):
        cfg = self.app.cfg
        if not cfg["allow_upload"]:
            raise HttpError(403, "uploads are disabled")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise HttpError(411, "Content-Length required")
        if length < 0:
            raise HttpError(400, "invalid length")
        if cfg["max_upload_mb"] and length > cfg["max_upload_mb"] * 1024 * 1024:
            raise HttpError(413, "file too large (max %d MB)" % cfg["max_upload_mb"])
        return length

    def receive_file(self, directory, length, digest=None):
        """Stream the request body into a hidden temporary file in `directory` (on the
        same filesystem as the target, so it can be renamed). Returns its path; the
        caller moves it into place or deletes it. `digest` (a hashlib object) is updated."""
        free = shutil.disk_usage(directory).free
        if length > free - 32 * 1024 * 1024:
            raise HttpError(507, "not enough space: %d MB free" % (free // (1024 * 1024)))
        tmp = os.path.join(directory, ".upload-%s.part" % uuid.uuid4().hex)
        try:
            remaining = length
            with open(tmp, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(1 << 20, remaining))
                    if not chunk:
                        raise HttpError(400, "upload interrupted")
                    f.write(chunk)
                    if digest is not None:
                        digest.update(chunk)
                    remaining -= len(chunk)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        return tmp

    def api_upload(self):
        length = self.upload_length()
        system = self.param("system", "")
        try:
            sysdir, target = self.app.library.upload_target(system, self.param("name", ""),
                                                            self.param("overwrite") == "1")
        except ValueError as e:
            raise HttpError(400, str(e))
        except FileExistsError as e:
            raise HttpError(409, str(e))
        tmp = self.receive_file(sysdir, length)
        try:
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        log.warning("ROM uploaded: %s (%d bytes)", target, length)
        self.app.library.invalidate(system)
        self.app.es_reload()
        self.send_json({"ok": True, "id": system + "/" + os.path.basename(target), "size": length})

    def api_delete(self):
        if not self.app.cfg["allow_delete"]:
            raise HttpError(403, "deletion is disabled")
        data = self.read_json()
        ids = data.get("ids") if isinstance(data, dict) else None
        if not isinstance(ids, list) or not ids:
            raise HttpError(400, "no ROM specified")
        roms_dir = self.app.library.roms_dir
        results = self.app.library.delete([str(i) for i in ids], bool(data.get("deleteMedia", True)))
        deleted = [os.path.join(roms_dir, *r["id"].split("/")) for r in results if r["ok"]]
        if deleted:
            self.app.collections.forget(deleted)
            self.app.es_reload()
        self.send_json({"results": results, "deleted": sum(1 for r in results if r["ok"])})

    def api_favorite(self):
        data = self.read_json()
        ids = data.get("ids") if isinstance(data, dict) else None
        if not isinstance(ids, list) or not ids:
            raise HttpError(400, "no ROM specified")
        favorite = bool(data.get("favorite", True))
        results = self.app.library.set_favorite([str(i) for i in ids], favorite)
        if any(r["ok"] for r in results):
            self.app.es_reload()
        self.send_json({"results": results, "favorite": favorite,
                        "updated": sum(1 for r in results if r["ok"])})

    def _collections_state(self, **extra):
        state = {"collections": self.app.collections.list(), "restartNeeded": self.app.restart_needed()}
        state.update(extra)
        return state

    def api_collections(self):
        self.send_json(self._collections_state())

    def api_collections_post(self):
        data = self.read_json()
        if not isinstance(data, dict):
            raise HttpError(400, "invalid request")
        col = self.app.collections
        action = data.get("action")
        name = str(data.get("name") or "")
        ids = data.get("ids") or []
        if not isinstance(ids, list):
            raise HttpError(400, "invalid ROM list")
        ids = [str(i) for i in ids]
        extra, changes = {}, {}  # changes = {collection: enabled} the running ES must reload
        was_enabled = name in col.enabled()
        try:
            if action == "create":
                enable = bool(data.get("enable", True))
                name = col.create(name, ids, enable)
                changes = {name: True} if enable else {}
                extra = {"name": name, "added": len(ids)}
            elif action == "rename":
                new_name = col.rename(name, str(data.get("newName") or ""))
                if was_enabled and new_name != name:
                    changes = {name: False, new_name: True}
                extra = {"name": new_name}
            elif action == "delete":
                col.delete(name)
            elif action == "enable":
                enabled = bool(data.get("enabled", True))
                if col.set_enabled(name, enabled):
                    changes = {name: enabled}
            elif action in ("add", "remove"):
                if not ids:
                    raise HttpError(400, "no ROM specified")
                count = (col.add if action == "add" else col.remove)(name, ids)
                extra = {"name": name, "added" if action == "add" else "removed": count}
            elif action == "prune":
                extra = {"name": name, "removed": col.prune(name)}
            else:
                raise HttpError(400, "unknown action")
        except CollectionError as e:
            raise HttpError(e.status, str(e))
        if changes:
            self.app.mark_restart_needed(changes)
        # ES re-reads the collection files on reload; enabling needs a restart (see Collections)
        self.app.es_reload()
        self.send_json(self._collections_state(**extra))

    def api_es_restart(self):
        activity = self.app.telemetry._activity()
        if activity["game"] or activity["emulator"]:
            raise HttpError(409, "a game is running: quit it before restarting EmulationStation")
        if not activity["es_running"]:
            raise HttpError(409, "EmulationStation is not running")
        # Not /restart: in Batocera's ES that reboots the whole device. /quit closes ES
        # normally and emulationstation-standalone starts it again right away.
        try:
            _LOCAL_OPENER.open(self.app.cfg["es_api"] + "/quit", timeout=5).read()
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise HttpError(502, "EmulationStation refused the request")
        except (ConnectionResetError, http.client.RemoteDisconnected, http.client.IncompleteRead):
            pass  # ES closed the connection while quitting
        except Exception as e:
            raise HttpError(502, "EmulationStation did not answer: %s" % e)
        self.send_json({"ok": True})

    # -- live screen and remote control --------------------------------------

    def api_screen(self):
        screen, out = self.app.screen, {"capture": None, "pad": None, "padError": None}
        out["capture"] = screen.backend()
        if self.app.cfg.get("allow_remote_control"):
            try:
                out["pad"] = self.app.pad.info()
            except PadError as e:
                out["padError"] = str(e)
        else:
            out["padError"] = "remote control is disabled in the configuration"
        self.send_json(out)

    def api_screen_frame(self):
        try:
            frame = self.app.screen.frame()
        except ScreenError as e:
            raise HttpError(503, str(e))
        if self.headers.get("If-None-Match") == frame["etag"]:
            self.send_response(304)
            self.send_header("ETag", frame["etag"])
            self.end_headers()
            return
        body = frame["data"]
        self.send_response(200)
        if frame["type"] == "raw":
            # raw pixels compress very well and the browser inflates them for free
            body = gzip.compress(body, 1)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("X-Frame-Format", json.dumps(frame["format"], separators=(",", ":")))
        else:
            self.send_header("Content-Type", frame["type"])
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("ETag", frame["etag"])
        self.end_headers()
        self.wfile.write(body)

    def api_pad(self):
        if not self.app.cfg.get("allow_remote_control"):
            raise HttpError(403, "remote control is disabled in the configuration")
        data = self.read_json()
        try:
            if data.get("releaseAll"):
                self.app.pad.release_all()
            else:
                button = data.get("button")
                if button not in Pad.BUTTONS:
                    raise HttpError(400, "unknown button")
                self.app.pad.set(button, bool(data.get("down")))
        except PadError as e:
            raise HttpError(503, str(e))
        self.send_json({"ok": True})

    # -- saves ---------------------------------------------------------------

    def api_saves(self):
        groups = self.app.saves.scan()
        self.send_json({"groups": groups, "dir": self.app.saves.dir,
                        "size": sum(g["size"] for g in groups)})

    def api_saves_file(self):
        try:
            full = self.app.saves.path(self.param("p", ""))
        except (ValueError, FileNotFoundError):
            raise HttpError(404, "file not found")
        inline = self.param("inline") == "1" and os.path.splitext(full)[1].lower() in IMAGE_EXTS
        self.send_file(full, os.path.basename(full), attachment=not inline)

    def _saves_zip(self, rels):
        saves = self.app.saves
        if not rels:
            raise HttpError(400, "no file specified")
        entries = []
        for rel in saves.related(rels):
            try:
                entries.append((saves.path(rel), rel))
            except (ValueError, FileNotFoundError):
                raise HttpError(404, "file not found: %s" % rel)
        if len(entries) == 1:
            return self.send_file(entries[0][0], os.path.basename(entries[0][0]))
        self.send_zip(entries, "knulli-saves-%s.zip" % time.strftime("%Y%m%d-%H%M%S"))

    def api_saves_download(self):
        if self.param("all") == "1":
            root = self.app.saves.dir
            if not os.path.isdir(root):
                raise HttpError(404, "no saves folder")
            entries = [(os.path.join(root, n), n) for n in sorted(os.listdir(root)) if not n.startswith(".")]
            return self.send_zip(entries, "knulli-saves-backup-%s.zip" % time.strftime("%Y%m%d-%H%M%S"))
        self._saves_zip(self.query.get("p", []))

    def api_saves_download_post(self):
        self._saves_zip(self.form_ids())

    def api_saves_delete(self):
        if not self.app.cfg["allow_delete"]:
            raise HttpError(403, "deletion is disabled")
        data = self.read_json()
        paths = data.get("paths") if isinstance(data, dict) else None
        if not isinstance(paths, list) or not paths:
            raise HttpError(400, "no file specified")
        results = self.app.saves.delete([str(p) for p in paths])
        self.send_json({"results": results, "deleted": sum(1 for r in results if r["ok"])})

    def api_saves_upload(self):
        length = self.upload_length()
        try:
            directory = self.app.saves.upload_dir(self.param("dir", ""))
            name = check_name(self.param("name", ""))
        except ValueError as e:
            raise HttpError(400, str(e))
        target = os.path.join(directory, name)
        if os.path.exists(target) and self.param("overwrite") != "1":
            raise HttpError(409, "the file %s already exists" % name)
        tmp = self.receive_file(directory, length)
        try:
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        log.warning("save uploaded: %s", target)
        self.send_json({"ok": True, "path": os.path.relpath(target, self.app.saves.dir).replace(os.sep, "/")})

    # -- BIOS ----------------------------------------------------------------

    def api_bios(self):
        self.send_json(self.app.bios.check())

    def api_bios_upload(self):
        length = self.upload_length()
        try:
            name = check_name(self.param("name", ""))
        except ValueError as e:
            raise HttpError(400, str(e))
        bios = self.app.bios
        bios_dir = os.path.join(bios.root, "bios")
        os.makedirs(bios_dir, exist_ok=True)
        digest = hashlib.md5()
        tmp = self.receive_file(bios_dir, length, digest)
        try:
            paths, known, matched = bios.targets(name, digest.hexdigest(), tmp)
            for i, rel in enumerate(paths):
                try:
                    full = safe_join(bios.root, rel)
                except ValueError:
                    raise HttpError(400, "invalid BIOS path: %s" % rel)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                if i == len(paths) - 1:
                    os.replace(tmp, full)
                else:
                    shutil.copy2(tmp, full)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        log.warning("BIOS uploaded: %s -> %s", name, ", ".join(paths))
        self.send_json({"ok": True, "placed": paths, "known": known, "matched": matched, "md5": digest.hexdigest()})

    # -- screenshots ---------------------------------------------------------

    def api_captures(self):
        self.send_json({"captures": self.app.captures.list()})

    def api_captures_file(self):
        try:
            full = self.app.captures.path(self.param("id", ""))
        except (ValueError, FileNotFoundError):
            raise HttpError(404, "file not found")
        self.send_file(full, os.path.basename(full), attachment=self.param("download") == "1",
                       cache="max-age=3600")

    def _captures_zip(self, ids):
        if not ids:
            raise HttpError(400, "no file specified")
        entries = []
        for cid in ids:
            try:
                entries.append((self.app.captures.path(cid), cid))
            except (ValueError, FileNotFoundError):
                raise HttpError(404, "file not found: %s" % cid)
        if len(entries) == 1:
            return self.send_file(entries[0][0], os.path.basename(entries[0][0]))
        self.send_zip(entries, "knulli-screenshots-%s.zip" % time.strftime("%Y%m%d-%H%M%S"))

    def api_captures_download(self):
        self._captures_zip(self.query.get("id", []))

    def api_captures_download_post(self):
        self._captures_zip(self.form_ids())

    def api_captures_delete(self):
        if not self.app.cfg["allow_delete"]:
            raise HttpError(403, "deletion is disabled")
        data = self.read_json()
        ids = data.get("ids") if isinstance(data, dict) else None
        if not isinstance(ids, list) or not ids:
            raise HttpError(400, "no file specified")
        results = self.app.captures.delete([str(i) for i in ids])
        self.send_json({"results": results, "deleted": sum(1 for r in results if r["ok"])})

    def api_es_reload(self):
        self.app.library.invalidate()
        self.app.es_reload(force=True)
        self.send_json({"ok": True})


ROUTES = {
    ("GET", "/api/config"): (Handler.api_config, False),
    ("GET", "/api/telemetry"): (Handler.api_telemetry, False),
    ("GET", "/api/roms"): (Handler.api_roms, False),
    ("GET", "/api/systems"): (Handler.api_systems, False),
    ("GET", "/api/rom"): (Handler.api_rom, False),
    ("GET", "/api/media"): (Handler.api_media, False),
    ("GET", "/api/download"): (Handler.api_download_get, False),
    # A form download cannot send custom headers; it is read-only anyway
    ("POST", "/api/download"): (Handler.api_download_post, False),
    ("POST", "/api/upload"): (Handler.api_upload, True),
    ("POST", "/api/delete"): (Handler.api_delete, True),
    ("POST", "/api/favorite"): (Handler.api_favorite, True),
    ("POST", "/api/es-reload"): (Handler.api_es_reload, True),
    ("GET", "/api/collections"): (Handler.api_collections, False),
    ("POST", "/api/collections"): (Handler.api_collections_post, True),
    ("POST", "/api/es-restart"): (Handler.api_es_restart, True),
    ("GET", "/api/saves"): (Handler.api_saves, False),
    ("GET", "/api/saves/file"): (Handler.api_saves_file, False),
    ("GET", "/api/saves/download"): (Handler.api_saves_download, False),
    ("POST", "/api/saves/download"): (Handler.api_saves_download_post, False),
    ("POST", "/api/saves/delete"): (Handler.api_saves_delete, True),
    ("POST", "/api/saves/upload"): (Handler.api_saves_upload, True),
    ("GET", "/api/bios"): (Handler.api_bios, False),
    ("POST", "/api/bios/upload"): (Handler.api_bios_upload, True),
    ("GET", "/api/captures"): (Handler.api_captures, False),
    ("GET", "/api/captures/file"): (Handler.api_captures_file, False),
    ("GET", "/api/captures/download"): (Handler.api_captures_download, False),
    ("POST", "/api/captures/download"): (Handler.api_captures_download_post, False),
    ("POST", "/api/captures/delete"): (Handler.api_captures_delete, True),
    ("GET", "/api/screen"): (Handler.api_screen, False),
    ("GET", "/api/screen/frame"): (Handler.api_screen_frame, False),
    ("POST", "/api/pad"): (Handler.api_pad, True),
}


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------

def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
    return cfg


def make_server(cfg):
    app = App(cfg)
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer((cfg["host"], int(cfg["port"])), handler)
    server.daemon_threads = True
    server.app = app
    return server


def main():
    ap = argparse.ArgumentParser(description="Knulli WebUI")
    ap.add_argument("--config", default=os.environ.get("KNULLI_WEBUI_CONFIG", os.path.join(APP_DIR, "config.json")))
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--roms-dir")
    ap.add_argument("--es-systems", action="append", help="es_systems.cfg file (repeatable)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.host:
        cfg["host"] = args.host
    if args.port:
        cfg["port"] = args.port
    if args.roms_dir:
        cfg["roms_dir"] = args.roms_dir
    if args.es_systems:
        cfg["es_systems_files"] = args.es_systems

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    server = make_server(cfg)
    server.app.telemetry.start()
    server.app.library.warm_up()

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.warning("Knulli WebUI %s listening on http://%s:%d (ROMs: %s)",
                VERSION, cfg["host"], cfg["port"], cfg["roms_dir"])
    server.serve_forever()
    server.server_close()


if __name__ == "__main__":
    main()
