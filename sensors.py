"""Медленные датчики в фоновом потоке: nvidia-smi + файл от процесса датчиков (LHM)."""
import json
import os
import subprocess
import threading
import time

from sensor_host import OUT_PATH

SENSOR_INTERVAL = 1.5
STALE_AFTER = 10      # сек — данные LHM считаем устаревшими
NO_WINDOW = 0x08000000

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


def pick_cpu_temp(sensors):
    """Главная температура ЦП: Package / Tctl / Core Max."""
    best = None
    prio = ("cpu package", "core (tctl/tdie)", "tctl", "core max", "cpu")
    for s in sensors:
        if s["type"] != "Temperature" or s["hwtype"] != "Cpu":
            continue
        name = s["name"].lower()
        for p_i, p in enumerate(prio):
            if p in name and "distance" not in name:
                if best is None or p_i < best[0]:
                    best = (p_i, s["value"])
                break
    return best[1] if best else None


class Sensors(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.data = {}
        self.lhm = []          # список датчиков LHM
        self.lhm_ok = False
        self.has_nvidia = True

    def get(self):
        with self.lock:
            return dict(self.data)

    def get_lhm(self):
        with self.lock:
            return list(self.lhm), self.lhm_ok

    def run(self):
        while True:
            d = self.read_nvidia()
            lhm, ok = self.read_lhm()
            if ok:
                t = pick_cpu_temp(lhm)
                if t is not None:
                    d["cpu_temp"] = t
            with self.lock:
                self.data, self.lhm, self.lhm_ok = d, lhm, ok
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
