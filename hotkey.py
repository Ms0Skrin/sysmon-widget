"""Глобальная горячая клавиша через RegisterHotKey в отдельном потоке."""
import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WM_HOTKEY = 0x0312
WM_APP = 0x8000
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 1, 2, 4, 8, 0x4000
MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}

_SPECIAL = {
    0x20: "Space", 0x09: "Tab", 0x0D: "Enter", 0x08: "Backspace", 0x2D: "Insert",
    0x2E: "Delete", 0x24: "Home", 0x23: "End", 0x21: "PgUp", 0x22: "PgDn",
    0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓", 0xC0: "`", 0x13: "Pause",
}


def vk_name(vk):
    if vk in _SPECIAL:
        return _SPECIAL[vk]
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x60 <= vk <= 0x69:
        return f"Num{vk - 0x60}"
    buf = ctypes.create_unicode_buffer(64)
    sc = user32.MapVirtualKeyW(vk, 0)
    if sc and user32.GetKeyNameTextW(sc << 16, buf, 64):
        return buf.value
    return f"VK{vk:02X}"


def hotkey_label(mods, vk):
    if not vk:
        return "не задана"
    parts = [n for m, n in ((MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"),
                            (MOD_WIN, "Win")) if mods & m]
    return "+".join(parts + [vk_name(vk)])


def current_mods():
    """Какие модификаторы зажаты прямо сейчас."""
    down = lambda vk: bool(user32.GetAsyncKeyState(vk) & 0x8000)
    mods = 0
    if down(0x11):
        mods |= MOD_CONTROL
    if down(0x12):
        mods |= MOD_ALT
    if down(0x10):
        mods |= MOD_SHIFT
    if down(0x5B) or down(0x5C):
        mods |= MOD_WIN
    return mods


class HotkeyThread(threading.Thread):
    """Держит одну горячую клавишу; callback вызывается из этого потока."""

    def __init__(self, callback):
        super().__init__(daemon=True)
        self.callback = callback
        self.tid = None
        self._ready = threading.Event()
        self._done = threading.Event()
        self._req = None
        self._ok = False

    def run(self):
        msg = wintypes.MSG()
        self.tid = kernel32.GetCurrentThreadId()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # создаём очередь сообщений
        self._ready.set()
        registered = False
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.callback()
            elif msg.message == WM_APP:
                if registered:
                    user32.UnregisterHotKey(None, 1)
                    registered = False
                if self._req:
                    mods, vk = self._req
                    registered = bool(user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk))
                self._ok = registered
                self._done.set()

    def set(self, mods, vk):
        """Перерегистрировать. vk=0 — снять. Возвращает True, если клавиша свободна."""
        self._ready.wait(2)
        self._req = (mods, vk) if vk else None
        self._done.clear()
        user32.PostThreadMessageW(self.tid, WM_APP, 0, 0)
        self._done.wait(2)
        return self._ok if vk else True
