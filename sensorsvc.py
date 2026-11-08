"""Управление процессом датчиков из виджета (виджет работает БЕЗ прав администратора)."""
import ctypes
import os
import subprocess

from sensor_host import (APP_DIR, HOST_MUTEX, NO_WINDOW, STOP_PATH, TASK_NAME, TASK_PATH,
                         mutex_exists, pawnio_installed, pythonw)

HOST_SCRIPT = os.path.join(APP_DIR, "sensor_host.py")
shell32 = ctypes.windll.shell32
shell32.ShellExecuteW.restype = ctypes.c_void_p


def host_running():
    return mutex_exists(HOST_MUTEX)


def task_exists():
    r = subprocess.run(["schtasks", "/query", "/tn", TASK_PATH + TASK_NAME],
                       capture_output=True, creationflags=NO_WINDOW)
    return r.returncode == 0


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_elevated(*args):
    """Запуск sensor_host.py с правами администратора (окно UAC). → True, если согласились."""
    params = " ".join([f'"{HOST_SCRIPT}"', *args])
    r = shell32.ShellExecuteW(None, "runas", pythonw(), params, APP_DIR, 0)
    return (r or 0) > 32


def start_host(allow_uac=True):
    """Запустить датчики: через задачу (без UAC) или с запросом UAC."""
    try:
        os.remove(STOP_PATH)
    except OSError:
        pass
    if host_running():
        return True
    if task_exists():
        r = subprocess.run(["schtasks", "/run", "/tn", TASK_PATH + TASK_NAME],
                           capture_output=True, creationflags=NO_WINDOW)
        if r.returncode == 0:
            return True
    if is_admin():
        subprocess.Popen([pythonw(), HOST_SCRIPT], cwd=APP_DIR, creationflags=NO_WINDOW)
        return True
    return run_elevated() if allow_uac else False


def stop_host():
    try:
        os.makedirs(os.path.dirname(STOP_PATH), exist_ok=True)
        open(STOP_PATH, "w").close()
    except OSError:
        pass


def setup_all():
    """Одним UAC: драйвер PawnIO + задача без UAC + запуск датчиков."""
    return run_elevated("--setup")


def set_task(enabled):
    return run_elevated("--install-task" if enabled else "--remove-task")


def install_pawnio():
    return run_elevated("--install-pawnio")


__all__ = ["stop_host", "host_running", "task_exists", "start_host", "setup_all", "set_task",
           "install_pawnio", "pawnio_installed"]
