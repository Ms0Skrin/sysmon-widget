"""Медленные датчики (nvidia-smi, LibreHardwareMonitor) в фоновом потоке."""
import subprocess
import threading
import time

SENSOR_INTERVAL = 2.0  # опрос, сек
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW — чтобы nvidia-smi не мигал консолью


class Sensors(threading.Thread):
    """Опрашивает медленные источники, чтобы не тормозить интерфейс."""

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.data = {}
        self.has_nvidia = True
        self._wmi = None
        self._wmi_retry = 0.0

    def get(self):
        with self.lock:
            return dict(self.data)

    def run(self):
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except ImportError:
            pass
        while True:
            d = {}
            d.update(self.read_nvidia())
            d.update(self.read_lhm())
            with self.lock:
                self.data = d
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

    def read_lhm(self):
        """Температура ЦП из LibreHardwareMonitor (WMI)."""
        now = time.monotonic()
        if self._wmi is None:
            if now < self._wmi_retry:
                return {}
            try:
                import win32com.client
                self._wmi = win32com.client.GetObject("winmgmts:root\\LibreHardwareMonitor")
            except Exception:
                self._wmi_retry = now + 15  # LHM не запущен — проверим позже
                return {}
        try:
            rows = self._wmi.ExecQuery(
                "SELECT Name, Value, Identifier FROM Sensor WHERE SensorType='Temperature'")
            pkg, cores = None, []
            for r in rows:
                ident = (r.Identifier or "").lower()
                if "cpu" not in ident:
                    continue
                name = (r.Name or "").lower()
                if "package" in name:
                    pkg = max(pkg or 0, r.Value)
                elif name.startswith("cpu core") or name.startswith("core"):
                    cores.append(r.Value)
            t = pkg if pkg is not None else (max(cores) if cores else None)
            return {"cpu_temp": t} if t is not None else {}
        except Exception:
            self._wmi = None
            self._wmi_retry = now + 15
            return {}
