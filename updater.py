"""Проверка и установка обновлений через git (папка виджета — клон репозитория)."""
import os
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = 0x08000000


def _git(*args, timeout=30):
    return subprocess.run(["git", *args], cwd=APP_DIR, capture_output=True, text=True,
                          timeout=timeout, creationflags=NO_WINDOW)


def check():
    """→ (кол-во новых коммитов, описание или текст ошибки). count=None — ошибка."""
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
    except Exception as ex:  # таймаут и т.п.
        return None, str(ex)


def apply():
    """git pull --ff-only → (ok, сообщение)."""
    r = _git("pull", "--ff-only", timeout=120)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def restart():
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    subprocess.Popen([pyw if os.path.exists(pyw) else exe, os.path.join(APP_DIR, "widget.py"), "--restart"],
                     cwd=APP_DIR, creationflags=NO_WINDOW)


def version():
    try:
        r = _git("describe", "--tags", "--always", "--dirty", timeout=5)
        return r.stdout.strip() or "dev"
    except Exception:
        return "dev"
