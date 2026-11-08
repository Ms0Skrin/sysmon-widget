"""Обновления.

* Установленная версия (SysMonWidget.exe): смотрим последний релиз на GitHub, скачиваем
  SysMonWidget-Setup.exe и запускаем его в тихом режиме (он сам перезапустит виджет).
* Запуск из исходников (git clone): git fetch / git pull --ff-only.
"""
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request

from paths import APP_DIR, FROZEN, widget_cmd

REPO = "Ms0Skrin/sysmon-widget"
SETUP_ASSET = "SysMonWidget-Setup.exe"
NO_WINDOW = 0x08000000
UA = {"User-Agent": "sysmon-widget", "Accept": "application/vnd.github+json"}
_latest = {}


def _git(*args, timeout=30):
    return subprocess.run(["git", *args], cwd=APP_DIR, capture_output=True, text=True,
                          timeout=timeout, creationflags=NO_WINDOW)


def version():
    if FROZEN:
        try:
            with open(os.path.join(APP_DIR, "version.txt"), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return "?"
    try:
        return _git("describe", "--tags", "--always", "--dirty", timeout=5).stdout.strip() or "dev"
    except Exception:
        return "dev"


def _vtuple(tag):
    out = []
    for part in tag.lstrip("v").split("-")[0].split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def _check_release():
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/latest", headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            rel = json.load(r)
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            return None, ("Релизы на GitHub не найдены (или репозиторий приватный — тогда "
                          "обновления доступны только владельцу).")
        return None, f"GitHub ответил ошибкой {ex.code}."
    except Exception as ex:
        return None, f"Не удалось связаться с GitHub: {ex}"
    asset = next((a for a in rel.get("assets", []) if a["name"] == SETUP_ASSET), None)
    if not asset or _vtuple(rel["tag_name"]) <= _vtuple(version()):
        return 0, ""
    _latest.update(url=asset["browser_download_url"], tag=rel["tag_name"])
    notes = (rel.get("body") or "").strip()
    return 1, f"{rel['tag_name']}\n\n{notes[:600]}"


def check():
    """→ (кол-во обновлений, описание) или (None, текст ошибки)."""
    if FROZEN:
        return _check_release()
    try:
        if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
            return None, "Папка виджета не является git-репозиторием."
        r = _git("fetch", "--quiet", timeout=60)
        if r.returncode != 0:
            return None, "Не удалось связаться с GitHub:\n" + (r.stderr.strip() or r.stdout.strip())
        r = _git("rev-list", "--count", "HEAD..@{u}")
        if r.returncode != 0:
            return None, "У ветки нет upstream:\n" + r.stderr.strip()
        n = int(r.stdout.strip() or 0)
        log = _git("log", "--oneline", "-n", "10", "HEAD..@{u}").stdout.strip() if n else ""
        return n, log
    except FileNotFoundError:
        return None, "git не найден в PATH."
    except Exception as ex:
        return None, str(ex)


def apply():
    """→ (ok, сообщение). В установленной версии запускает установщик и виджет должен закрыться."""
    if FROZEN:
        if not _latest:
            return False, "Сначала проверьте обновления."
        try:
            path = os.path.join(tempfile.gettempdir(), SETUP_ASSET)
            urllib.request.urlretrieve(_latest["url"], path)
            # установщик попросит права администратора, обновит файлы и сам запустит виджет
            subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NOCANCEL", "/RESTARTAPP"])
            return True, ""
        except Exception as ex:
            return False, str(ex)
    r = _git("pull", "--ff-only", timeout=120)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def restart():
    if FROZEN:
        return  # перезапуск делает установщик
    subprocess.Popen(widget_cmd("--restart"), cwd=APP_DIR, creationflags=NO_WINDOW)
