"""Настройки (JSON в ~/.config/sysmon-widget) и автозапуск через XDG autostart."""
import json
import os
import sys

APP_NAME = "SysMonWidget"
APP_ID = "io.github.ms0skrin.SysMonWidget"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
CFG_DIR = os.path.join(CONFIG_HOME, "sysmon-widget")
CFG_PATH = os.path.join(CFG_DIR, "config.json")
AUTOSTART = os.path.join(CONFIG_HOME, "autostart", "sysmon-widget.desktop")
ICON_PATH = os.path.join(ROOT, "assets", "app.ico")

DEFAULTS = {
    "topmost": False, "alpha": 0.9, "locked": False,
    "columns": 2, "tiles": ["cpu", "cpu_temp", "gpu", "gpu_temp", "ram", "net"],
    "apps": [],              # закреплённые приложения: [{"name": str, "path": "<id>.desktop" | путь}]
    "launcher_alpha": 0.92,
    "launcher_icon": 48,     # размер значков в панели приложений, px
    "launcher_cols": 0,      # колонок в панели (0 — авто)
    "theme": "graphite",
    "graph_style": "",       # "" — как в теме
    "sensor_meta": {},       # "lhm:<id>" -> {"title", "type"} для плиток датчиков
    "alert_cpu": 90, "alert_gpu": 90,  # порог уведомления о перегреве, °C (0 — выкл.)
    "weather_city": "",      # город для плитки «Погода»
    "profiles": {},          # раскладки: имя -> {"panels": [...], "menu_panel": id}
}


def load_cfg(valid_tiles):
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    cfg["tiles"] = [t for t in cfg["tiles"] if t in valid_tiles or t.startswith("lhm:")]
    return cfg


def save_cfg(cfg):
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        tmp = CFG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CFG_PATH)
    except Exception:
        pass


def widget_cmd(*args):
    """Команда запуска виджета."""
    return [sys.executable, os.path.join(ROOT, "widget.py"), *args]


def _quote(arg):
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"' if " " in arg else arg


def desktop_entry(*args, hidden=False):
    lines = ["[Desktop Entry]", "Type=Application", "Name=SysMon Widget",
             "Comment=Монитор системы и панель приложений на рабочем столе",
             "Exec=" + " ".join(_quote(a) for a in widget_cmd(*args)),
             "Icon=sysmon-widget", "Terminal=false", "Categories=System;Monitor;",
             f"StartupWMClass={APP_ID}"]
    if hidden:
        lines.append("NoDisplay=true")
    return "\n".join(lines) + "\n"


def autostart_enabled():
    return os.path.exists(AUTOSTART)


def set_autostart(on):
    if on:
        os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
        with open(AUTOSTART, "w", encoding="utf-8") as f:
            f.write(desktop_entry(hidden=True))
    else:
        try:
            os.remove(AUTOSTART)
        except FileNotFoundError:
            pass
