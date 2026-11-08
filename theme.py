"""Цвета и мелкие утилиты оформления."""
import ctypes

BG = "#16181d"
PANEL = "#1f232b"
HOVER = "#2a2f3a"
FG = "#e8eaed"
MUTED = "#8b919c"
ACCENT = "#4fa3ff"
WARN = "#ffb347"
HOT = "#ff5c5c"


def blend(hex_color, bg_hex, a):
    c = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg_hex[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(b[i] + (c[i] - b[i]) * a):02x}" for i in range(3))


def round_corners(tk_window):
    """Скруглённые углы Windows 11 для окна без рамки."""
    try:
        hwnd = ctypes.windll.user32.GetParent(tk_window.winfo_id())
        pref = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
    except Exception:
        pass


def dark_titlebar(tk_window):
    """Тёмный заголовок у обычного окна (настройки)."""
    try:
        tk_window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(tk_window.winfo_id())
        on = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4)
    except Exception:
        pass
