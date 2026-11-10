"""Наблюдение за рабочим столом: «Показать рабочий стол» (Win+D) и полноэкранные приложения.

По Win+D Windows не сворачивает окна, а поднимает окно рабочего стола (Progman) поверх всех —
панели оказываются под ним. Мы это замечаем по порядку окон и возвращаем панели наверх.
"""
import ctypes
import os
from ctypes import wintypes

import desktop

u = ctypes.WinDLL("user32")  # свой экземпляр: типы функций не мешают остальным модулям
HWND = wintypes.HWND
u.GetTopWindow.restype = u.GetWindow.restype = u.GetForegroundWindow.restype = u.GetAncestor.restype = HWND
u.GetParent.restype = HWND
u.GetParent.argtypes = [HWND]
u.GetTopWindow.argtypes = [HWND]
u.GetWindow.argtypes = [HWND, wintypes.UINT]
u.GetAncestor.argtypes = [HWND, wintypes.UINT]
u.SetWindowPos.argtypes = [HWND, HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
u.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
u.GetClassNameW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
u.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
u.GetWindowLongW.argtypes = [HWND, ctypes.c_int]
u.MonitorFromWindow.restype = ctypes.c_void_p
u.MonitorFromWindow.argtypes = [HWND, wintypes.DWORD]

SWP_QUIET = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
                 "XamlExplorerHostIslandWindow", "TopLevelWindowForOverflowXamlIsland"}
PID = os.getpid()


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]


def class_name(hwnd):
    buf = ctypes.create_unicode_buffer(128)
    u.GetClassNameW(hwnd, buf, 128)
    return buf.value


def pid_of(hwnd):
    pid = wintypes.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def top_hwnd(tk_window):
    """Настоящее окно верхнего уровня для окна tkinter."""
    return u.GetParent(tk_window.winfo_id())


def desktop_window():
    """Окно верхнего уровня, в котором живут значки рабочего стола (Progman или WorkerW)."""
    dv = desktop._defview()
    return u.GetAncestor(dv, 2) if dv else None  # GA_ROOT


def is_above(a, b):
    """Стоит ли окно a выше окна b в порядке окон."""
    h = u.GetTopWindow(None)
    for _ in range(3000):
        if not h or h == b:
            return False
        if h == a:
            return True
        h = u.GetWindow(h, 2)  # GW_HWNDNEXT
    return False


def raise_quietly(hwnd):
    """Поднять окно над всеми обычными, не активируя. Фоновой программе HWND_TOP не даёт встать выше
    активного окна, поэтому идём через «поверх всех» и сразу обратно."""
    u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, SWP_QUIET)  # HWND_TOPMOST
    u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, SWP_QUIET)  # HWND_NOTOPMOST


def put_below(hwnd, other):
    u.SetWindowPos(hwnd, other, 0, 0, 0, 0, SWP_QUIET)


def foreground_app():
    """Активное окно чужой программы (не оболочки Windows и не наше) или None."""
    fg = u.GetForegroundWindow()
    if not fg or pid_of(fg) == PID or class_name(fg) in SHELL_CLASSES:
        return None
    return fg


def monitor_of(hwnd):
    return u.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST


def fullscreen_monitor():
    """Монитор, который целиком занят активным полноэкранным приложением (игра, видео), или None."""
    fg = foreground_app()
    if not fg:
        return None
    if u.GetWindowLongW(fg, -16) & 0x00C00000 == 0x00C00000:  # WS_CAPTION: обычное развёрнутое окно
        return None
    mon = monitor_of(fg)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    r = wintypes.RECT()
    if not mon or not u.GetMonitorInfoW(mon, ctypes.byref(mi)) or not u.GetWindowRect(fg, ctypes.byref(r)):
        return None
    m = mi.rcMonitor
    full = r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom
    return mon if full else None
