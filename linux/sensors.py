"""Датчики Linux без прав администратора: hwmon (/sys/class/hwmon), частоты ядер, nvidia-smi.

Показания отдаются в том же виде, что и от LibreHardwareMonitor в Windows-версии
({"id", "hw", "hwtype", "group", "name", "type", "value"}), поэтому выбор главной температуры,
карточки «Процессор»/«Видеокарта» и плитки отдельных датчиков работают общим кодом (sensors.py).
"""
import atexit
import glob
import os
import re
import subprocess
import threading
import time

import psutil

import sensors as base

CPU_CHIPS = {"coretemp", "k10temp", "zenpower", "cpu_thermal", "via_cputemp"}
STORAGE_CHIPS = {"nvme", "drivetemp"}
GPU_CHIPS = {"amdgpu": "GpuAmd", "radeon": "GpuAmd", "i915": "GpuIntel", "xe": "GpuIntel",
             "nouveau": "GpuNvidia"}
# вид файла hwmon -> (тип датчика, делитель, подпись по умолчанию)
KINDS = {
    "temp": ("Temperature", 1000, "Температура"),
    "fan": ("Fan", 1, "Вентилятор"),
    "in": ("Voltage", 1000, "Напряжение"),
    "power": ("Power", 1_000_000, "Мощность"),
    "curr": ("Current", 1000, "Ток"),
}
RESCAN = 60  # сек — заново искать устройства (подключили диск и т.п.)


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def cpu_name():
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    name = line.split(":", 1)[1]
                    name = re.sub(r"\((R|TM|tm|r)\)", "", name)
                    name = re.sub(r"\s+@.*$|\bCPU\b|\b\d+-Core Processor\b|\bProcessor\b", "", name)
                    return " ".join(name.split())
    except OSError:
        pass
    return "Процессор"


def _cpu_label(label, kind, n):
    """Подписи как у LibreHardwareMonitor — по ним выбирается главная температура ЦП."""
    low = (label or "").lower()
    m = re.match(r"package id (\d+)", low)
    if m:
        return "CPU Package" if m.group(1) == "0" else f"CPU Package #{int(m.group(1)) + 1}"
    m = re.match(r"core (\d+)", low)
    if m:
        return f"Core #{int(m.group(1)) + 1}"
    if low == "tctl":
        return "Core (Tctl/Tdie)"
    m = re.match(r"tccd(\d+)", low)
    if m:
        return f"CCD{m.group(1)}"
    return label or f"{KINDS[kind][2]} {n}"


class NvidiaSmi(threading.Thread):
    """Одна долгоживущая nvidia-smi в режиме опроса — дешевле, чем запускать её каждую секунду."""
    FIELDS = ("index,name,utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw,"
              "fan.speed,clocks.gr,clocks.mem")
    BASIC = "index,name,utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw"

    def __init__(self, interval_ms):
        super().__init__(daemon=True)
        self.interval_ms = interval_ms
        self.gpus = {}       # index -> (время, {поле: значение})
        self.proc = None
        self.lock = threading.Lock()
        atexit.register(self.stop)

    def stop(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass

    def run(self):
        fields = self.FIELDS
        while True:
            got = False
            try:
                self.proc = p = subprocess.Popen(
                    ["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits",
                     "-lms", str(self.interval_ms)],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            except OSError:
                return  # nvidia-smi нет — видеокарта не NVIDIA
            keys = fields.split(",")
            for line in p.stdout:
                parts = [x.strip() for x in line.split(",")]
                if len(parts) != len(keys):
                    continue
                got = True
                row = dict(zip(keys, parts))
                with self.lock:
                    self.gpus[row["index"]] = (time.time(), row)
            p.wait()
            if self.proc is None:
                return  # остановили при выходе
            if not got and fields != self.BASIC:
                fields = self.BASIC  # старый драйвер не знает части полей
                continue
            time.sleep(5)

    def sensors(self):
        out = []
        now = time.time()
        with self.lock:
            gpus = dict(self.gpus)
        for idx, (ts, row) in sorted(gpus.items()):
            if now - ts > base.STALE_AFTER:
                continue
            hw = row.get("name") or "NVIDIA"
            for field, name, stype in (
                    ("utilization.gpu", "GPU Core", "Load"),
                    ("temperature.gpu", "GPU Core", "Temperature"),
                    ("memory.used", "GPU Memory Used", "SmallData"),
                    ("memory.total", "GPU Memory Total", "SmallData"),
                    ("power.draw", "GPU Package", "Power"),
                    ("fan.speed", "GPU Fan", "Control"),
                    ("clocks.gr", "GPU Core", "Clock"),
                    ("clocks.mem", "GPU Memory", "Clock")):
                try:
                    v = float(row[field])
                except (KeyError, ValueError):
                    continue
                out.append({"id": f"/nvidia/{idx}/{field}", "hw": hw, "hwtype": "GpuNvidia", "group": hw,
                            "name": name, "type": stype, "value": round(v, 2)})
        return out


class HwReader:
    def __init__(self):
        self.cpu = cpu_name()
        self.items = []      # (путь к файлу, делитель, заготовка датчика)
        self.extras = []     # AMD: (каталог устройства, имя видеокарты, ключ)
        self.scanned = 0.0
        self.nvidia = NvidiaSmi(int(base.SENSOR_INTERVAL * 1000))
        self.nvidia.start()

    def scan(self):
        self.items, self.extras = [], []
        seen = {}
        for hw in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            chip = _read(os.path.join(hw, "name")) or os.path.basename(hw)
            dev = os.path.realpath(os.path.join(hw, "device"))
            if chip in CPU_CHIPS:
                hwtype, name, key = "Cpu", self.cpu, os.path.basename(dev)
            elif chip in STORAGE_CHIPS:
                hwtype = "Storage"
                name = (_read(os.path.join(dev, "model")) or chip).strip()
                key = (_read(os.path.join(dev, "serial")) or name).strip().replace("/", "_")
            elif chip in GPU_CHIPS:
                hwtype, key = GPU_CHIPS[chip], os.path.basename(dev)
                name = {"GpuAmd": "AMD Radeon", "GpuIntel": "Intel Graphics", "GpuNvidia": "NVIDIA"}[hwtype]
                if hwtype == "GpuAmd":
                    self.extras.append((dev, name, key))
            else:
                hwtype, name, key = "SuperIO", chip, os.path.basename(dev)
            # два одинаковых устройства (два сокета, два диска одной модели) не должны слиться
            n = seen[(chip, key)] = seen.get((chip, key), 0) + 1
            if n > 1:
                key = f"{key}-{n}"
            for path in sorted(glob.glob(os.path.join(hw, "*_input")) +
                               glob.glob(os.path.join(hw, "power*_average"))):
                m = re.match(r"(temp|fan|in|power|curr)(\d+)_(input|average)$", os.path.basename(path))
                if not m:
                    continue
                kind, num = m.group(1), int(m.group(2))
                stype, div, _ = KINDS[kind]
                label = _read(os.path.join(hw, f"{kind}{num}_label"))
                if hwtype == "Cpu":
                    label = _cpu_label(label, kind, num)
                elif hwtype == "GpuAmd":
                    label = {"edge": "GPU Core", "junction": "GPU Hot Spot", "mem": "GPU Memory",
                             "PPT": "GPU Package", "fan1": "GPU Fan"}.get(label or "", label)
                    if kind == "power" and not label:
                        label = "GPU Package"
                    if kind == "fan" and not label:
                        label = "GPU Fan"
                label = label or f"{KINDS[kind][2]} {num}"
                self.items.append((path, div, num, {
                    "id": f"/{chip}/{key}/{kind}/{num}", "hw": name, "hwtype": hwtype, "group": name,
                    "name": label, "type": stype}))
        self.items.sort(key=lambda it: (it[3]["hw"], it[3]["type"], it[2]))
        self.scanned = time.time()

    def read(self):
        if time.time() - self.scanned > RESCAN:
            self.scan()
        out = []
        for path, div, _num, proto in self.items:
            raw = _read(path)
            try:
                v = float(raw) / div
            except (TypeError, ValueError):
                continue  # датчик временно не отвечает
            out.append({**proto, "value": round(v, 2)})
        for dev, name, key in self.extras:  # AMD: нагрузка и видеопамять лежат рядом с устройством
            for fname, sname, stype, div in (("gpu_busy_percent", "GPU Core", "Load", 1),
                                             ("mem_info_vram_used", "GPU Memory Used", "SmallData", 1 << 20),
                                             ("mem_info_vram_total", "GPU Memory Total", "SmallData", 1 << 20)):
                try:
                    v = float(_read(os.path.join(dev, fname))) / div
                except (TypeError, ValueError):
                    continue
                out.append({"id": f"/amdgpu/{key}/{fname}", "hw": name, "hwtype": "GpuAmd", "group": name,
                            "name": sname, "type": stype, "value": round(v, 2)})
        try:
            for i, fr in enumerate(psutil.cpu_freq(percpu=True) or []):
                if fr.current:
                    out.append({"id": f"/cpu/clock/{i}", "hw": self.cpu, "hwtype": "Cpu", "group": self.cpu,
                                "name": f"Core #{i + 1}", "type": "Clock", "value": round(fr.current)})
        except Exception:
            pass
        out += self.nvidia.sensors()
        return out


class Sensors(base.Sensors):
    """Тот же фоновый поток, что и в Windows, но показания берём прямо из системы."""

    def __init__(self):
        super().__init__()
        self.has_nvidia = False  # nvidia-smi уже учтена в общем списке датчиков
        self.hw = HwReader()

    def read_lhm(self):
        try:
            return self.hw.read(), True
        except Exception:
            return [], False

    def stop(self):
        self.hw.nvidia.stop()


def gpu_details(sensors, fallback):
    card = base.gpu_details(sensors, fallback)
    if card:  # nvidia-smi отдаёт вентилятор в процентах, а не в оборотах
        fan = next((s["value"] for s in sensors if s["hwtype"] == "GpuNvidia" and s["name"] == "GPU Fan"), None)
        if fan is not None and not any(r[0] == "Вентилятор" for r in card["rows"]):
            card["rows"].append(("Вентилятор", f"{fan:.0f} %", None))
    return card


SKIP_FS = {"squashfs", "tmpfs", "devtmpfs", "overlay", "iso9660", "ramfs", "efivarfs", "fuse.portal"}
SKIP_MOUNTS = ("/boot", "/efi", "/snap", "/run", "/var/lib/docker", "/var/lib/containers", "/nix/store")


def disk_details(sensors):
    """Диски: занято/всего по каждому разделу и температуры накопителей."""
    gb = 1024 ** 3
    vols = {}
    for part in psutil.disk_partitions(all=False):
        mp = part.mountpoint
        if part.fstype in SKIP_FS or any(mp == s or mp.startswith(s + "/") for s in SKIP_MOUNTS):
            continue
        if part.device not in vols or len(mp) < len(vols[part.device]):
            vols[part.device] = mp  # подтома btrfs — один диск: берём самую короткую точку
    rows, ring, ring_text = [], 0, "—"
    for mp in sorted(vols.values(), key=lambda m: (m != "/", m)):
        try:
            use = psutil.disk_usage(mp)
        except OSError:
            continue
        if not use.total:
            continue
        title = "Система (/)" if mp == "/" else os.path.basename(mp.rstrip("/")) or mp
        rows.append((title, f"{use.used / gb:.0f} / {use.total / gb:.0f} ГБ",
                     "hot" if use.percent >= 92 else None))
        if mp == "/":
            ring, ring_text = use.percent / 100, f"{use.percent:.0f}%"
    nvol = len(rows)
    seen = set()
    for s in sensors:
        if s["hwtype"] == "Storage" and s["type"] == "Temperature" and s["hw"] not in seen and s["value"] > 0:
            seen.add(s["hw"])
            rows.append((s["hw"], f"{s['value']:.0f} °C", "temp"))
    if not rows:
        return None
    return {"device": f"томов: {nvol}", "ring": ring, "ring_text": ring_text, "ring_label": "диск /",
            "rows": rows}
