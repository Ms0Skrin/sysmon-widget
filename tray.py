"""Значок в трее через Shell_NotifyIcon (свой, чтобы показывать собственное красивое меню)."""
import os
import threading

import win32api
import win32con
import win32gui

from paths import APP_DIR, LOCAL_DIR

WM_TRAY = win32con.WM_USER + 20


def icon_path():
    """Иконка программы: из установки или сгенерированная."""
    bundled = os.path.join(APP_DIR, "assets", "app.ico")
    if os.path.exists(bundled):
        return bundled
    path = os.path.join(LOCAL_DIR, "app.ico")
    if not os.path.exists(path):
        make_icon(path)
    return path


def make_icon(path):
    from PIL import Image, ImageDraw
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 8, 248, 248), 52, fill=(22, 24, 29, 255))
    for i, (hgt, col) in enumerate([(88, "#4fa3ff"), (150, "#b07cff"), (118, "#ffb347"), (182, "#3ddc97")]):
        x = 40 + i * 46
        d.rounded_rectangle((x, 214 - hgt, x + 32, 214), 8, fill=col)
    img.save(path, sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])


class TrayIcon(threading.Thread):
    def __init__(self, tooltip, on_left, on_right):
        super().__init__(daemon=True)
        self.tooltip, self.on_left, self.on_right = tooltip, on_left, on_right
        self.hwnd = None
        self._ready = threading.Event()

    def run(self):
        hinst = win32api.GetModuleHandle(None)
        self._taskbar_created = win32gui.RegisterWindowMessage("TaskbarCreated")
        wc = win32gui.WNDCLASS()
        wc.hInstance = hinst
        wc.lpszClassName = "SysMonWidgetTray"
        wc.lpfnWndProc = {
            WM_TRAY: self._notify,
            self._taskbar_created: self._recreate,
            win32con.WM_DESTROY: self._destroy,
        }
        try:
            atom = win32gui.RegisterClass(wc)
        except win32gui.error:
            atom = "SysMonWidgetTray"
        self.hwnd = win32gui.CreateWindow(atom, "SysMonWidgetTray", 0, 0, 0, 0, 0, 0, 0, hinst, None)
        self._add()
        self._ready.set()
        win32gui.PumpMessages()

    def _add(self):
        ico = icon_path()
        hicon = self.hicon = win32gui.LoadImage(None, ico, win32con.IMAGE_ICON, 0, 0,
                                   win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
        nid = (self.hwnd, 0, win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
               WM_TRAY, hicon, self.tooltip)
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid)
        except win32gui.error:
            pass  # панель задач ещё не готова — добавим по TaskbarCreated

    def notify(self, title, text):
        """Всплывающее уведомление Windows от значка в трее."""
        if not self.hwnd:
            return
        nid = (self.hwnd, 0, win32gui.NIF_INFO, WM_TRAY, getattr(self, "hicon", 0), self.tooltip,
               text[:255], 10, title[:63], win32gui.NIIF_WARNING)
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, nid)
        except win32gui.error:
            pass

    def _recreate(self, *_):
        self._add()
        return 0

    def _notify(self, hwnd, _msg, _wp, lparam):
        if lparam == win32con.WM_LBUTTONUP:
            self.on_left()
        elif lparam in (win32con.WM_RBUTTONUP, win32con.WM_CONTEXTMENU):
            # разрешение на передний план: так Windows позволит нашему меню получить фокус
            try:
                win32gui.SetForegroundWindow(hwnd)
            except win32gui.error:
                pass
            self.on_right(*win32api.GetCursorPos())
        return 0

    def _destroy(self, hwnd, *_):
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (hwnd, 0))
        except win32gui.error:
            pass
        win32gui.PostQuitMessage(0)
        return 0

    def stop(self):
        if self.hwnd:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
