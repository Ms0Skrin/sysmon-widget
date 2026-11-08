"""Прозрачная панель задач через TranslucentTB (https://github.com/TranslucentTB/TranslucentTB, GPL-3.0).

Новая панель задач Windows 11 игнорирует SetWindowCompositionAttribute, поэтому используем
официальную portable-сборку TranslucentTB: виджет скачивает её с GitHub Releases, пишет
settings.json рядом с exe (TranslucentTB следит за файлом и применяет изменения на лету)
и запускает/закрывает её. Значок TranslucentTB в трее скрыт — управление из виджета.
"""
import json
import os
import platform
import shutil
import subprocess
import time
import urllib.request
import zipfile

REPO = "TranslucentTB/TranslucentTB"
HOME = "https://github.com/" + REPO
BASE = os.path.join(os.environ.get("LOCALAPPDATA", "."), "sysmon-widget", "translucenttb")
UA = {"User-Agent": "sysmon-widget", "Accept": "application/vnd.github+json"}
NO_WINDOW = 0x08000000

MODES = [("", "Как в Windows"), ("clear", "Прозрачная"), ("blur", "Размытие"),
         ("acrylic", "Акрил"), ("tint", "Цвет темы виджета")]


def _from_bundle():
    """TranslucentTB пишет settings.json рядом с exe, а Program Files нам не писать —
    поэтому копируем комплектную версию в профиль пользователя."""
    from paths import THIRD_PARTY
    src = os.path.join(THIRD_PARTY, "translucenttb")
    if os.path.isdir(src):
        shutil.copytree(src, os.path.join(BASE, "app"), dirs_exist_ok=True)
        return True
    return False


def exe_path():
    for root, _d, files in os.walk(BASE) if os.path.isdir(BASE) else []:
        for f in files:
            if f.lower() == "translucenttb.exe":
                return os.path.join(root, f)
    return None


def running():
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq TranslucentTB.exe", "/NH"],
                       capture_output=True, text=True, creationflags=NO_WINDOW)
    return "translucenttb.exe" in r.stdout.lower()


def install():
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest", headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        rel = json.load(r)
    name = f"TranslucentTB-portable-{arch}.zip"
    asset = next(a for a in rel["assets"] if a["name"] == name)
    os.makedirs(BASE, exist_ok=True)
    zpath = os.path.join(BASE, name)
    with urllib.request.urlopen(urllib.request.Request(asset["browser_download_url"], headers=UA),
                                timeout=60) as r, open(zpath, "wb") as f:
        shutil.copyfileobj(r, f)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(os.path.join(BASE, "app"))
    os.remove(zpath)
    return rel["tag_name"]


def _settings(mode, tint_hex):
    accent, color = {
        "clear": ("clear", "#00000000"),
        "blur": ("blur", "#00000000"),
        "acrylic": ("acrylic", "#00000000"),
        "tint": ("clear", tint_hex + "B0"),
    }[mode]
    off = {"enabled": False, "accent": "normal", "color": "#00000000", "show_peek": True, "show_line": False}
    return {
        "desktop_appearance": {"accent": accent, "color": color, "show_peek": True, "show_line": False},
        "visible_window_appearance": dict(off),
        "maximized_window_appearance": dict(off),
        "start_opened_appearance": dict(off),
        "search_opened_appearance": dict(off),
        "task_view_opened_appearance": dict(off),
        "battery_saver_appearance": dict(off),
        "hide_tray": True,
        "disable_saving": False,
        "verbosity": "warn",
        "use_xaml_context_menu": False,
    }


def apply(mode, tint_hex="#000000"):
    """mode из MODES; "" — вернуть стандартную панель (закрыть TranslucentTB)."""
    if not mode:
        stop()
        return
    exe = exe_path() or (_from_bundle() and exe_path()) or (install() and exe_path())
    if not exe:
        raise RuntimeError("Не удалось установить TranslucentTB")
    cfg_path = os.path.join(os.path.dirname(exe), "settings.json")
    tmp = cfg_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_settings(mode, tint_hex), f, indent=2)
    os.replace(tmp, cfg_path)
    if not running():
        subprocess.Popen([exe], cwd=os.path.dirname(exe), creationflags=0x00000008)


def stop():
    if not running():
        return
    subprocess.run(["taskkill", "/IM", "TranslucentTB.exe"], capture_output=True, creationflags=NO_WINDOW)
    for _ in range(20):
        if not running():
            return
        time.sleep(0.2)
    subprocess.run(["taskkill", "/F", "/IM", "TranslucentTB.exe"], capture_output=True,
                   creationflags=NO_WINDOW)
