"""Иконки файлов/программ → PIL.Image (на заданном фоне)."""
import ctypes
import os
from ctypes import wintypes

from PIL import Image, ImageDraw, ImageFont

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
shell32 = ctypes.windll.shell32

HANDLE = ctypes.c_void_p
user32.GetDC.restype = HANDLE
user32.GetDC.argtypes = [HANDLE]
user32.ReleaseDC.argtypes = [HANDLE, HANDLE]
user32.DrawIconEx.argtypes = [HANDLE, ctypes.c_int, ctypes.c_int, HANDLE, ctypes.c_int,
                              ctypes.c_int, wintypes.UINT, HANDLE, wintypes.UINT]
user32.DestroyIcon.argtypes = [HANDLE]
user32.FillRect.argtypes = [HANDLE, ctypes.POINTER(wintypes.RECT), HANDLE]
user32.PrivateExtractIconsW.restype = wintypes.UINT
user32.PrivateExtractIconsW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.POINTER(HANDLE), ctypes.POINTER(wintypes.UINT),
                                        wintypes.UINT, wintypes.UINT]
gdi32.CreateCompatibleDC.restype = HANDLE
gdi32.CreateCompatibleDC.argtypes = [HANDLE]
gdi32.CreateDIBSection.restype = HANDLE
gdi32.CreateDIBSection.argtypes = [HANDLE, ctypes.c_void_p, wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), HANDLE, wintypes.DWORD]
gdi32.SelectObject.restype = HANDLE
gdi32.SelectObject.argtypes = [HANDLE, HANDLE]
gdi32.DeleteObject.argtypes = [HANDLE]
gdi32.DeleteDC.argtypes = [HANDLE]
gdi32.CreateSolidBrush.restype = HANDLE
gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [("hIcon", HANDLE), ("iIcon", ctypes.c_int), ("dwAttributes", wintypes.DWORD),
                ("szDisplayName", wintypes.WCHAR * 260), ("szTypeName", wintypes.WCHAR * 80)]


shell32.SHGetFileInfoW.restype = ctypes.c_size_t
shell32.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(SHFILEINFOW),
                                   wintypes.UINT, wintypes.UINT]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def _icon_source(path):
    """Для .lnk — откуда брать иконку (IconLocation или цель ярлыка)."""
    if path.lower().endswith(".lnk"):
        try:
            import win32com.client
            sc = win32com.client.Dispatch("WScript.Shell").CreateShortcut(path)
            src, _, idx = (sc.IconLocation or "").rpartition(",")
            src = os.path.expandvars(src)
            if src and os.path.exists(src):
                return src, int(idx or 0)
            target = os.path.expandvars(sc.TargetPath or "")
            if target and os.path.exists(target):
                return target, 0
        except Exception:
            pass
    return path, 0


def _extract_hicon(path, size):
    src, idx = _icon_source(path)
    if os.path.splitext(src)[1].lower() in (".exe", ".dll", ".ico", ".icl", ".cpl"):
        h, iid = HANDLE(), wintypes.UINT()
        n = user32.PrivateExtractIconsW(src, idx, size, size, ctypes.byref(h), ctypes.byref(iid), 1, 0)
        if n and n != 0xFFFFFFFF and h.value:
            return h.value
    info = SHFILEINFOW()
    if shell32.SHGetFileInfoW(path, 0, ctypes.byref(info), ctypes.sizeof(info), 0x100) and info.hIcon:
        return info.hIcon  # SHGFI_ICON, 32px
    return None


def _render(hicon, size, bg_hex):
    r, g, b = (int(bg_hex[i:i + 2], 16) for i in (1, 3, 5))
    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), size, -size, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = ctypes.c_void_p()
    bmp = gdi32.CreateDIBSection(dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(dc, bmp)
    brush = gdi32.CreateSolidBrush(r | (g << 8) | (b << 16))
    user32.FillRect(dc, ctypes.byref(wintypes.RECT(0, 0, size, size)), brush)
    gdi32.DeleteObject(brush)
    user32.DrawIconEx(dc, 0, 0, hicon, size, size, 0, None, 3)  # DI_NORMAL
    raw = ctypes.string_at(bits, size * size * 4)
    img = Image.frombuffer("RGB", (size, size), raw, "raw", "BGRX", 0, 1).copy()
    gdi32.SelectObject(dc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(dc)
    user32.ReleaseDC(None, screen)
    return img


def letter_icon(name, size, bg_hex, color="#4fa3ff"):
    img = Image.new("RGB", (size, size), bg_hex)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), size // 5, fill=color)
    try:
        font = ImageFont.truetype("segoeuib.ttf", int(size * 0.55))
    except OSError:
        font = ImageFont.load_default()
    d.text((size / 2, size / 2), (name or "?")[0].upper(), fill="white", font=font, anchor="mm")
    return img


def file_icon(path, size, bg_hex, name=""):
    hicon = None
    try:
        if os.path.exists(path):
            hicon = _extract_hicon(path, size)
        if hicon:
            img = _render(hicon, size, bg_hex)
            return img
    except Exception:
        pass
    finally:
        if hicon:
            user32.DestroyIcon(hicon)
    return letter_icon(name or os.path.basename(path), size, bg_hex)
