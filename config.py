"""Настройки (JSON в %APPDATA%) и автозапуск через реестр."""
import json
import os
import winreg

APP_NAME = "SysMonWidget"
CFG_DIR = os.path.join(os.environ.get("APPDATA", "."), "sysmon-widget")
CFG_PATH = os.path.join(CFG_DIR, "config.json")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

DEFAULTS = {
    "x": None, "y": None, "topmost": False, "alpha": 0.9, "locked": False,
    "columns": 2, "tiles": ["cpu", "cpu_temp", "gpu", "gpu_temp", "ram", "net"],
    # горячая клавиша меню приложений: MOD_ALT=1, MOD_CONTROL=2, MOD_SHIFT=4, MOD_WIN=8
    "hotkey": {"mods": 3, "vk": 0x20},  # Ctrl+Alt+Space
    "apps": [],  # [{"name": str, "path": str}]; path может быть shell:AppsFolder\<AppID>
    "launcher_alpha": 0.92,
    "launcher_icon": 40,     # размер значков в панели приложений, px
    "launcher_cols": 0,      # колонок в панели (0 — авто)
    "theme": "graphite",
    "graph_style": "",       # "" — как в теме
    "lhm_enabled": True,     # встроенные датчики LibreHardwareMonitor
    "setup_asked": False,    # спрашивали ли про права при первом запуске
    "sensor_meta": {},       # "lhm:<id>" -> {"title", "type"} для плиток датчиков
    "naby_enabled": False,   # интеграция NabyScape
    "taskbar_mode": "",      # "" как в Windows | clear | blur | tint
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


def autostart_command():
    from paths import quote, widget_cmd
    return quote(widget_cmd())


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass
