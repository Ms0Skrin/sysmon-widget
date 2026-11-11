"""Медленные датчики в фоновом потоке: nvidia-smi + файл от процесса датчиков (LHM)."""
import json
import os
import subprocess
import sys
import threading
import time

import psutil

if sys.platform == "win32":
    from sensor_host import OUT_PATH
else:  # Linux: датчики читает linux/sensors.py, файла от процесса датчиков нет
    OUT_PATH = ""

SENSOR_INTERVAL = 1.5
STALE_AFTER = 10      # сек — данные LHM считаем устаревшими
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

UNITS = {
    "Temperature": "°C", "Load": "%", "Clock": "МГц", "Fan": "об/мин", "Power": "Вт",
    "Voltage": "В", "Current": "А", "Control": "%", "Level": "%", "Data": "ГБ",
    "SmallData": "МБ", "Throughput": "Б/с", "Frequency": "Гц", "Energy": "мВт·ч",
    "Factor": "", "Flow": "л/ч", "Noise": "дБА", "Humidity": "%", "TimeSpan": "с",
}
PERCENT_TYPES = {"Load", "Control", "Level", "Humidity"}
TYPE_NAMES = {
    "Temperature": "Температура", "Load": "Нагрузка", "Clock": "Частота", "Fan": "Вентилятор",
    "Power": "Мощность", "Voltage": "Напряжение", "Current": "Ток", "Control": "Управление",
    "Level": "Уровень", "Data": "Данные", "SmallData": "Данные", "Throughput": "Скорость",
    "Frequency": "Частота", "Energy": "Энергия", "Factor": "Коэффициент", "Flow": "Поток",
    "Noise": "Шум", "Humidity": "Влажность", "TimeSpan": "Время",
}


def fmt_rate(b):
    units = ("Б/с", "КБ/с", "МБ/с", "ГБ/с")
    i = 0
    while b >= 1024 and i < len(units) - 1:
        b /= 1024
        i += 1
    return f"{b:.0f} {units[i]}" if i == 0 else f"{b:.1f} {units[i]}"


def sensor_max(stype):
    return 100 if stype in PERCENT_TYPES or stype == "Temperature" else None


def fmt_value(stype, v):
    unit = UNITS.get(stype, "")
    if stype == "Throughput":
        return fmt_rate(v)
    if stype in ("Voltage", "Current", "Factor"):
        return f"{v:.2f} {unit}".strip()
    if stype in ("Data", "SmallData", "Power") and v < 100:
        return f"{v:.1f} {unit}".strip()
    return f"{v:.0f} {unit}".strip()


def _best(sensors, pred, prio):
    """Значение датчика с наивысшим приоритетом по подстрокам имени (prio — по убыванию)."""
    best = None
    for s in sensors:
        if not pred(s):
            continue
        name = s["name"].lower()
        for i, p in enumerate(prio):
            if p in name:
                if best is None or i < best[0]:
                    best = (i, s["value"])
                break
    return best[1] if best else None


def pick_cpu_temp(sensors):
    """Главная температура ЦП для Intel и AMD."""
    return _best(
        sensors,
        lambda s: s["hwtype"] == "Cpu" and s["type"] == "Temperature" and "distance" not in s["name"].lower(),
        # Intel: CPU Package / Core Max; AMD Zen: Core (Tctl/Tdie), Tctl, Tdie, CCD; старые AMD: Package / Core
        ("cpu package", "core (tctl/tdie)", "tctl", "tdie", "package", "core max", "ccd", "core", "cpu"),
    )


GPU_ORDER = ("GpuNvidia", "GpuAmd", "GpuIntel")  # дискретная важнее встроенной


def pick_gpu(sensors):
    """Нагрузка/температура/память/мощность главной видеокарты — NVIDIA, AMD или Intel."""
    gpus = {}
    for s in sensors:
        if s["hwtype"] in GPU_ORDER:
            gpus.setdefault((s["hwtype"], s["hw"]), []).append(s)
    if not gpus:
        return {}

    def rank(key):
        items = gpus[key]
        total = next((x["value"] for x in items if x["name"] == "GPU Memory Total"), 0)
        return (GPU_ORDER.index(key[0]), -total)

    items = gpus[sorted(gpus, key=rank)[0]]
    out = {}
    v = _best(items, lambda s: s["type"] == "Load", ("gpu core", "d3d 3d", "gpu"))
    if v is not None:
        out["gpu"] = v
    v = _best(items, lambda s: s["type"] == "Temperature", ("gpu core", "gpu hot spot", "gpu"))
    if v is not None:
        out["gpu_temp"] = v
    used = _best(items, lambda s: s["type"] == "SmallData",
                 ("gpu memory used", "d3d dedicated memory used"))
    total = _best(items, lambda s: s["type"] == "SmallData", ("gpu memory total",))
    if used is not None and total:
        out["vram_used"], out["vram_total"] = used, total
    v = _best(items, lambda s: s["type"] == "Power", ("gpu package", "gpu power", "gpu core", "gpu"))
    if v is not None:
        out["gpu_pwr"] = v
    out["gpu_name"] = items[0]["hw"]
    return out


class Sensors(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.data = {}
        self.lhm = []          # список датчиков LHM
        self.lhm_ok = False
        self.has_nvidia = True
        self.want_procs = False  # считать ли топ процессов (только когда есть такая плитка)
        self.procs = None

    def get_procs(self):
        with self.lock:
            return self.procs

    def read_procs(self):
        """Топ программ по ЦП и памяти (процессы с одним именем складываются) → строки карточки."""
        ncpu = psutil.cpu_count() or 1
        cpu, mem = {}, {}
        count = 0
        for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
            i = p.info
            if not i["pid"] or not i["name"] or i["name"] == "System Idle Process":
                continue
            count += 1
            name = i["name"][:-4] if i["name"].lower().endswith(".exe") else i["name"]
            cpu[name] = cpu.get(name, 0.0) + (i["cpu_percent"] or 0.0) / ncpu
            mem[name] = mem.get(name, 0) + getattr(i["memory_info"], "rss", 0)
        top_cpu = sorted(cpu.items(), key=lambda kv: -kv[1])[:4]
        top_mem = sorted(mem.items(), key=lambda kv: -kv[1])[:4]
        rows = []
        for (cn, cv), (mn, mv) in zip(top_cpu, top_mem):  # левая колонка — ЦП, правая — память
            rows.append((cn, f"{cv:.0f}% ЦП", None))
            rows.append((mn, f"{mv / 1024 ** 3:.1f} ГБ", None))
        return {"count": count, "rows": rows}

    def get(self):
        with self.lock:
            return dict(self.data)

    def get_lhm(self):
        with self.lock:
            return list(self.lhm), self.lhm_ok

    def run(self):
        while True:
            lhm, ok = self.read_lhm()
            d = {}
            if ok:  # LibreHardwareMonitor: Intel / AMD / NVIDIA одинаково
                t = pick_cpu_temp(lhm)
                if t is not None:
                    d["cpu_temp"] = t
                d.update(pick_gpu(lhm))
            # запасной путь без прав администратора: nvidia-smi (только NVIDIA)
            if not all(k in d for k in ("gpu", "gpu_temp", "vram_used", "gpu_pwr")):
                for k, v in self.read_nvidia().items():
                    d.setdefault(k, v)
            procs = None
            if self.want_procs:
                try:
                    procs = self.read_procs()
                except Exception:
                    procs = None
            with self.lock:
                self.data, self.lhm, self.lhm_ok, self.procs = d, lhm, ok, procs
            time.sleep(SENSOR_INTERVAL)

    def read_nvidia(self):
        if not self.has_nvidia:
            return {}
        try:
            out = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total,power.draw",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW,
            ).stdout.splitlines()[0]
        except FileNotFoundError:
            self.has_nvidia = False
            return {}
        except Exception:
            return {}
        vals = []
        for part in out.split(","):
            try:
                vals.append(float(part))
            except ValueError:
                vals.append(None)
        keys = ("gpu", "gpu_temp", "vram_used", "vram_total", "gpu_pwr")
        return {k: v for k, v in zip(keys, vals) if v is not None}

    @staticmethod
    def read_lhm():
        try:
            if time.time() - os.path.getmtime(OUT_PATH) > STALE_AFTER:
                return [], False
            with open(OUT_PATH, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("sensors", []), True
        except Exception:
            return [], False


def _vals(sensors, hwtypes, stype):
    return [s for s in sensors if s["hwtype"] in hwtypes and s["type"] == stype]


def cpu_details(sensors, per_core, load):
    """Подробности о процессоре (Intel / AMD) для большой плитки."""
    cpus = [s for s in sensors if s["hwtype"] == "Cpu"]
    name = cpus[0]["hw"] if cpus else ""
    rows = []
    t = pick_cpu_temp(sensors)
    if t is not None:
        rows.append(("Температура", f"{t:.0f} °C", "temp"))
    temps = [s["value"] for s in _vals(sensors, ("Cpu",), "Temperature")
             if "distance" not in s["name"].lower() and s["value"] > 0]
    if temps:
        rows.append(("Горячее ядро", f"{max(temps):.0f} °C", "temp"))
    clocks = [s["value"] for s in _vals(sensors, ("Cpu",), "Clock")
              if "core" in s["name"].lower() and s["value"] > 0]
    if clocks:
        rows.append(("Частота", f"{sum(clocks) / len(clocks) / 1000:.2f} ГГц", None))
        rows.append(("Макс. частота", f"{max(clocks) / 1000:.2f} ГГц", None))
    pw = _best(sensors, lambda s: s["hwtype"] == "Cpu" and s["type"] == "Power", ("package", "cpu"))
    if pw is not None:
        rows.append(("Мощность", f"{pw:.0f} Вт", None))
    rows.append(("Потоки", f"{len(per_core)}", None))
    rows.append(("Макс. поток", f"{max(per_core):.0f}%" if per_core else "—", None))
    return {"device": name, "ring": load / 100, "ring_text": f"{load:.0f}%", "rows": rows, "bars": per_core}


def disk_details(sensors):
    """Диски: занято/всего по каждому тому и температуры накопителей (если есть датчики LHM)."""
    gb = 1024 ** 3
    sysdrive = os.environ.get("SystemDrive", "C:").upper()
    rows, ring, ring_text = [], 0, "—"
    for part in psutil.disk_partitions():
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            use = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        letter = part.device.rstrip("\\")
        rows.append((f"Диск {letter}", f"{use.used / gb:.0f} / {use.total / gb:.0f} ГБ",
                     "hot" if use.percent >= 92 else None))
        if letter.upper() == sysdrive:
            ring, ring_text = use.percent / 100, f"{use.percent:.0f}%"
    seen = set()
    for s in _vals(sensors, ("Storage",), "Temperature"):
        if s["hw"] not in seen and s["value"] > 0:
            seen.add(s["hw"])
            rows.append((s["hw"], f"{s['value']:.0f} °C", "temp"))
    if not rows:
        return None
    return {"device": f"томов: {len(rows) - len(seen)}", "ring": ring, "ring_text": ring_text,
            "ring_label": f"диск {sysdrive}", "rows": rows}


def gpu_details(sensors, fallback):
    """Подробности о главной видеокарте (NVIDIA / AMD / Intel) для большой плитки."""
    gpus = {}
    for s in sensors:
        if s["hwtype"] in GPU_ORDER:
            gpus.setdefault((s["hwtype"], s["hw"]), []).append(s)
    rows = []
    if gpus:
        key = sorted(gpus, key=lambda k: (GPU_ORDER.index(k[0]),
                                          -next((x["value"] for x in gpus[k] if x["name"] == "GPU Memory Total"), 0)))[0]
        items, name = gpus[key], key[1]
        b = lambda stype, prio: _best(items, lambda s: s["type"] == stype, prio)
        load = b("Load", ("gpu core", "d3d 3d", "gpu")) or 0
        for label, stype, prio, fmt, kind in (
                ("Температура", "Temperature", ("gpu core",), "{:.0f} °C", "temp"),
                ("Hot Spot", "Temperature", ("hot spot",), "{:.0f} °C", "temp"),
                ("Частота ядра", "Clock", ("gpu core",), "{:.0f} МГц", None),
                ("Частота памяти", "Clock", ("gpu memory",), "{:.0f} МГц", None),
                ("Мощность", "Power", ("gpu package", "gpu power", "gpu"), "{:.0f} Вт", None),
                ("Вентилятор", "Fan", ("fan",), "{:.0f} об/мин", None)):
            v = b(stype, prio)
            if v is not None:
                rows.append((label, fmt.format(v), kind))
        used = b("SmallData", ("gpu memory used", "d3d dedicated memory used"))
        total = b("SmallData", ("gpu memory total",))
        if used is not None and total:
            rows.insert(2, ("Видеопамять", f"{used / 1024:.1f} / {total / 1024:.0f} ГБ", None))
    else:  # без LHM — то, что дал nvidia-smi
        name = fallback.get("gpu_name", "")
        load = fallback.get("gpu", 0)
        if "gpu_temp" in fallback:
            rows.append(("Температура", f"{fallback['gpu_temp']:.0f} °C", "temp"))
        if fallback.get("vram_total"):
            rows.append(("Видеопамять", f"{fallback.get('vram_used', 0) / 1024:.1f} / "
                                        f"{fallback['vram_total'] / 1024:.0f} ГБ", None))
        if "gpu_pwr" in fallback:
            rows.append(("Мощность", f"{fallback['gpu_pwr']:.0f} Вт", None))
    if not gpus and not fallback:
        return None
    return {"device": name, "ring": load / 100, "ring_text": f"{load:.0f}%", "rows": rows}
