"""Интеграция NabyScape (https://github.com/BillyNabil/nabyscape, MIT, автор BillyNabil).

Виджет скачивает официальную portable-сборку с GitHub Releases (проверяя SHA-256),
запускает/останавливает её и открывает её настройки. Сам NabyScape не модифицируется.
Управление — через его именованные объекты: mutex Local\\LumaIsland.Desktop,
события Local\\LumaIsland.OpenSettings и Local\\LumaIsland.Exit.
"""
import ctypes
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile

REPO = "BillyNabil/nabyscape"
HOME = "https://github.com/" + REPO
BASE = os.path.join(os.environ.get("LOCALAPPDATA", "."), "sysmon-widget", "nabyscape")
APP_DIR = os.path.join(BASE, "app")
VERSION_FILE = os.path.join(BASE, "version.txt")
UA = {"User-Agent": "sysmon-widget", "Accept": "application/vnd.github+json"}
NO_WINDOW = 0x08000000

k32 = ctypes.windll.kernel32
k32.OpenMutexW.restype = ctypes.c_void_p
k32.OpenEventW.restype = ctypes.c_void_p
k32.SetEvent.argtypes = [ctypes.c_void_p]
k32.CloseHandle.argtypes = [ctypes.c_void_p]


def _arch():
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"


def exe_path():
    if not os.path.isdir(APP_DIR):
        return None
    for root, _dirs, files in os.walk(APP_DIR):
        for f in files:
            if f.lower() == "nabyscape.exe":
                return os.path.join(root, f)
    return None


def installed():
    return exe_path() is not None


def installed_version():
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def running():
    h = k32.OpenMutexW(0x00100000, False, "Local\\LumaIsland.Desktop")
    if h:
        k32.CloseHandle(h)
        return True
    return False


def _signal(name):
    h = k32.OpenEventW(0x0002, False, name)  # EVENT_MODIFY_STATE
    if not h:
        return False
    k32.SetEvent(h)
    k32.CloseHandle(h)
    return True


def start(show_settings=False):
    exe = exe_path()
    if not exe:
        return False
    if running():
        return open_settings() if show_settings else True
    args = [exe, "--settings" if show_settings else "--background"]
    subprocess.Popen(args, cwd=os.path.dirname(exe), creationflags=0x00000008)  # DETACHED_PROCESS
    return True


def open_settings():
    if running():
        return _signal("Local\\LumaIsland.OpenSettings")
    return start(show_settings=True)


def stop(wait=5.0):
    if not running():
        return True
    _signal("Local\\LumaIsland.Exit")
    end = time.time() + wait
    while time.time() < end:
        if not running():
            return True
        time.sleep(0.2)
    return not running()


def restart():
    stop()
    return start()


def latest_release():
    """→ {"tag", "zip_url", "zip_name", "size", "sums_url"}"""
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest", headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        rel = json.load(r)
    want = f"NabyScape-win-{_arch()}-Portable.zip"
    zip_a = next((a for a in rel["assets"] if a["name"] == want), None)
    sums = next((a for a in rel["assets"] if a["name"] == "SHA256SUMS"), None)
    if not zip_a:
        raise RuntimeError(f"В релизе {rel['tag_name']} нет {want}")
    return {"tag": rel["tag_name"], "zip_url": zip_a["browser_download_url"], "zip_name": want,
            "size": zip_a["size"], "sums_url": sums["browser_download_url"] if sums else None}


def update_available():
    """→ (есть ли новее, тег последней версии)."""
    rel = latest_release()
    return rel["tag"] != installed_version(), rel["tag"]


def install(progress=None):
    """Скачать и распаковать последнюю версию. progress(доля 0..1, текст)."""
    say = progress or (lambda *_: None)
    say(0, "Получаю информацию о релизе…")
    rel = latest_release()
    expected = None
    if rel["sums_url"]:
        with urllib.request.urlopen(urllib.request.Request(rel["sums_url"], headers=UA), timeout=20) as r:
            for line in r.read().decode("utf-8", "replace").splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[-1].lstrip("*") == rel["zip_name"]:
                    expected = parts[0].lower()
    os.makedirs(BASE, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", dir=BASE)
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(urllib.request.Request(rel["zip_url"], headers=UA), timeout=60) as r:
            total = int(r.headers.get("Content-Length") or rel["size"] or 0)
            done = 0
            while chunk := r.read(1 << 16):
                tmp.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if total:
                    say(done / total * 0.9, f"Скачиваю {rel['tag']}: {done >> 20} / {total >> 20} МБ")
        tmp.close()
        if expected and h.hexdigest() != expected:
            raise RuntimeError("Контрольная сумма не совпала — файл повреждён, установка отменена.")
        say(0.92, "Распаковываю…")
        was_running = running()
        stop()
        staging = APP_DIR + ".new"
        shutil.rmtree(staging, ignore_errors=True)
        with zipfile.ZipFile(tmp.name) as z:
            z.extractall(staging)
        shutil.rmtree(APP_DIR, ignore_errors=True)
        os.replace(staging, APP_DIR)
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(rel["tag"])
        say(1, f"Установлено {rel['tag']}")
        if was_running:
            start()
        return rel["tag"]
    finally:
        try:
            tmp.close()
            os.remove(tmp.name)
        except OSError:
            pass


def uninstall():
    stop()
    shutil.rmtree(BASE, ignore_errors=True)
