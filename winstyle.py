"""Стиль Windows: системные цвета, эффекты прозрачности, прозрачность панели задач."""
import ctypes
import winreg
from ctypes import wintypes

user32 = ctypes.windll.user32
PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

def _reg_get(path, name, default=None):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return default


def _broadcast(area="ImmersiveColorSet"):
    res = wintypes.DWORD()
    user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, area, 0x0002, 1000, ctypes.byref(res))


def system_dark():
    return _reg_get(PERSONALIZE, "AppsUseLightTheme", 1) == 0


def accent_color():
    """Акцентный цвет Windows → '#rrggbb'."""
    v = _reg_get(r"Software\Microsoft\Windows\DWM", "AccentColor")
    if v is None:
        return "#0078d4"
    r, g, b = v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF
    return f"#{r:02x}{g:02x}{b:02x}"


def transparency_enabled():
    return _reg_get(PERSONALIZE, "EnableTransparency", 1) == 1


def set_transparency(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "EnableTransparency", 0, winreg.REG_DWORD, 1 if on else 0)
    _broadcast()
