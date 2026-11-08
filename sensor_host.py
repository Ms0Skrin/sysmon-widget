"""Процесс датчиков (запускается с правами администратора).

Загружает встроенный LibreHardwareMonitorLib (vendor/lhm) и раз в ~1.5 с пишет все
показания в %APPDATA%\\sysmon-widget\\sensors.json. Сам завершается, когда виджет закрыт.

Ключи командной строки (их передаёт виджет через UAC):
  --setup        установить задачу «запуск без UAC» + драйвер PawnIO, затем работать
  --install-task / --remove-task   только создать / удалить задачу и выйти
  --install-pawnio                 только поставить драйвер PawnIO и выйти
"""
import ctypes
import json
import os
import subprocess
import sys
import time
import traceback

from paths import APP_DIR, DATA_DIR, LHM_DIR, THIRD_PARTY, quote, sensors_cmd
OUT_PATH = os.path.join(DATA_DIR, "sensors.json")
LOG_PATH = os.path.join(DATA_DIR, "sensor_host.log")
STOP_PATH = os.path.join(DATA_DIR, "sensors.stop")  # виджет создаёт файл, чтобы остановить датчики

TASK_PATH = "\\SysMonWidget\\"
TASK_NAME = "Sensors"
WIDGET_MUTEX = "Local\\SysMonWidget"
HOST_MUTEX = "Local\\SysMonWidget-Sensors"
INTERVAL = 1.5
GRACE = 30          # сек без виджета до выхода
NO_WINDOW = 0x08000000

k32 = ctypes.windll.kernel32
k32.OpenMutexW.restype = ctypes.c_void_p
k32.CreateMutexW.restype = ctypes.c_void_p


def log(msg):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def mutex_exists(name):
    h = k32.OpenMutexW(0x00100000, False, name)  # SYNCHRONIZE
    if h:
        k32.CloseHandle(ctypes.c_void_p(h))
        return True
    return False


def ps(script):
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, creationflags=NO_WINDOW, timeout=300)


# ---------- установка (нужны права администратора) ----------
def install_task():
    """Задача Планировщика без триггеров: виджет запускает её сам, без окна UAC."""
    cmd = sensors_cmd()
    exe = cmd[0].replace("'", "''")
    args = quote(cmd[1:]).replace("'", "''")
    arg_part = f" -Argument '{args}'" if args else ""
    r = ps(f"""
$a = New-ScheduledTaskAction -Execute '{exe}'{arg_part} -WorkingDirectory '{APP_DIR}'
$p = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\\$env:USERNAME" -LogonType Interactive -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskPath '{TASK_PATH}' -TaskName '{TASK_NAME}' -Action $a -Principal $p -Settings $s -Force | Out-Null
""")
    log(f"install_task rc={r.returncode} {r.stderr.strip()}")
    return r.returncode == 0


def remove_task():
    r = ps(f"Unregister-ScheduledTask -TaskPath '{TASK_PATH}' -TaskName '{TASK_NAME}' -Confirm:$false")
    log(f"remove_task rc={r.returncode} {r.stderr.strip()}")


def pawnio_installed():
    r = subprocess.run(["sc", "query", "PawnIO"], capture_output=True, creationflags=NO_WINDOW)
    return r.returncode == 0


def install_pawnio():
    if pawnio_installed():
        return True
    bundled = os.path.join(THIRD_PARTY, "pawnio", "PawnIO_setup.exe")
    if os.path.exists(bundled):
        r = subprocess.run([bundled, "-install", "-silent"], capture_output=True, creationflags=NO_WINDOW,
                           timeout=600)
        log(f"install_pawnio (bundled) rc={r.returncode}")
        if pawnio_installed():
            return True
    try:
        r = subprocess.run(["winget", "install", "--id", "namazso.PawnIO", "-e", "--silent",
                            "--accept-package-agreements", "--accept-source-agreements"],
                           capture_output=True, text=True, creationflags=NO_WINDOW, timeout=600)
        log(f"install_pawnio rc={r.returncode} {r.stdout[-300:]}")
    except Exception as ex:
        log(f"install_pawnio failed: {ex}")
    return pawnio_installed()


# ---------- чтение датчиков ----------
def open_computer():
    from pythonnet import load
    load("netfx")
    import clr  # noqa: F401  (появляется после load)
    sys.path.append(LHM_DIR)
    clr.AddReference(os.path.join(LHM_DIR, "LibreHardwareMonitorLib.dll"))
    from LibreHardwareMonitor.Hardware import Computer
    c = Computer()
    for flag in ("IsCpuEnabled", "IsGpuEnabled", "IsMotherboardEnabled", "IsMemoryEnabled",
                 "IsStorageEnabled", "IsControllerEnabled", "IsBatteryEnabled", "IsPsuEnabled"):
        try:
            setattr(c, flag, True)
        except Exception:
            pass
    c.Open()
    return c


def collect(computer):
    out = []

    def walk(hw, parent_name=None):
        hw.Update()
        name = str(hw.Name).strip()
        for s in hw.Sensors:
            if s.Value is None:
                continue
            out.append({
                "id": str(s.Identifier),
                "hw": name,
                "hwtype": str(hw.HardwareType),
                "group": parent_name or name,
                "name": str(s.Name),
                "type": str(s.SensorType),
                "value": round(float(s.Value), 2),
            })
        for sub in hw.SubHardware:
            walk(sub, parent_name or name)

    for hw in computer.Hardware:
        try:
            walk(hw)
        except Exception:
            pass
    return out


def write(sensors):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = OUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "pid": os.getpid(), "pawnio": PAWNIO, "sensors": sensors},
                  f, ensure_ascii=False)
    os.replace(tmp, OUT_PATH)


PAWNIO = False


def serve():
    global PAWNIO
    if mutex_exists(HOST_MUTEX):
        return
    try:
        os.remove(STOP_PATH)
    except OSError:
        pass
    global _HOST_MUTEX_HANDLE
    _HOST_MUTEX_HANDLE = k32.CreateMutexW(None, True, HOST_MUTEX)  # держим до выхода
    PAWNIO = pawnio_installed()
    computer = open_computer()
    log(f"host started pid={os.getpid()} pawnio={PAWNIO}")
    last_seen = time.time()
    try:
        while True:
            if os.path.exists(STOP_PATH):
                log("stop requested")
                break
            if mutex_exists(WIDGET_MUTEX):
                last_seen = time.time()
            elif time.time() - last_seen > GRACE:
                log("widget gone, exiting")
                break
            try:
                write(collect(computer))
            except Exception:
                log(traceback.format_exc())
            time.sleep(INTERVAL)
    finally:
        computer.Close()
        try:
            os.remove(OUT_PATH)
        except OSError:
            pass


def main():
    args = set(sys.argv[1:])
    try:
        if "--install-task" in args:
            install_task()
            return
        if "--remove-task" in args:
            remove_task()
            return
        if "--install-pawnio" in args:
            install_pawnio()
            return
        if "--setup" in args:
            install_pawnio()
            install_task()
        serve()
    except Exception:
        log(traceback.format_exc())


if __name__ == "__main__":
    main()
