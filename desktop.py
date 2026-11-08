"""Показ/скрытие иконок рабочего стола (как «Вид → Отображать значки рабочего стола»)."""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowExW.restype = wintypes.HWND
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.IsWindowVisible.argtypes = [wintypes.HWND]

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
WM_COMMAND = 0x0111
TOGGLE_DESKTOP_ICONS = 0x7402


def _defview():
    progman = user32.FindWindowW("Progman", None)
    dv = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
    if dv:
        return dv
    found = []

    def cb(hwnd, _lp):  # после смены обоев DefView живёт в одном из WorkerW
        d = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        if d:
            found.append(d)
            return False
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0] if found else None


def icons_visible():
    dv = _defview()
    if not dv:
        return True
    lv = user32.FindWindowExW(dv, None, "SysListView32", None)
    return bool(lv and user32.IsWindowVisible(lv))


def set_icons_visible(visible):
    dv = _defview()
    if dv and icons_visible() != visible:
        user32.SendMessageW(dv, WM_COMMAND, TOGGLE_DESKTOP_ICONS, 0)
