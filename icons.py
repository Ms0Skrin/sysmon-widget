"""Иконки программ, ярлыков и приложений Магазина → PIL.Image (на заданном фоне)."""
import ctypes
import os
from ctypes import POINTER, byref, c_int, c_size_t, c_void_p, wintypes

from PIL import Image, ImageDraw, ImageFont

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
shell32 = ctypes.windll.shell32
ole32 = ctypes.windll.ole32
ole32.CoInitialize(None)

HANDLE = c_void_p
user32.GetDC.restype = HANDLE
user32.GetDC.argtypes = [HANDLE]
user32.ReleaseDC.argtypes = [HANDLE, HANDLE]
user32.DrawIconEx.argtypes = [HANDLE, c_int, c_int, HANDLE, c_int, c_int, wintypes.UINT, HANDLE,
                              wintypes.UINT]
user32.DestroyIcon.argtypes = [HANDLE]
user32.FillRect.argtypes = [HANDLE, POINTER(wintypes.RECT), HANDLE]
user32.PrivateExtractIconsW.restype = wintypes.UINT
user32.PrivateExtractIconsW.argtypes = [wintypes.LPCWSTR, c_int, c_int, c_int, POINTER(HANDLE),
                                        POINTER(wintypes.UINT), wintypes.UINT, wintypes.UINT]
gdi32.CreateCompatibleDC.restype = HANDLE
gdi32.CreateCompatibleDC.argtypes = [HANDLE]
gdi32.CreateDIBSection.restype = HANDLE
gdi32.CreateDIBSection.argtypes = [HANDLE, c_void_p, wintypes.UINT, POINTER(c_void_p), HANDLE,
                                   wintypes.DWORD]
gdi32.SelectObject.restype = HANDLE
gdi32.SelectObject.argtypes = [HANDLE, HANDLE]
gdi32.DeleteObject.argtypes = [HANDLE]
gdi32.DeleteDC.argtypes = [HANDLE]
gdi32.CreateSolidBrush.restype = HANDLE
gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]
ole32.CoTaskMemFree.argtypes = [c_void_p]


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [("hIcon", HANDLE), ("iIcon", c_int), ("dwAttributes", wintypes.DWORD),
                ("szDisplayName", wintypes.WCHAR * 260), ("szTypeName", wintypes.WCHAR * 80)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


_SHGetFileInfo_str = ctypes.WINFUNCTYPE(c_size_t, wintypes.LPCWSTR, wintypes.DWORD, POINTER(SHFILEINFOW),
                                        wintypes.UINT, wintypes.UINT)(("SHGetFileInfoW", shell32))
_SHGetFileInfo_pidl = ctypes.WINFUNCTYPE(c_size_t, c_void_p, wintypes.DWORD, POINTER(SHFILEINFOW),
                                         wintypes.UINT, wintypes.UINT)(("SHGetFileInfoW", shell32))
shell32.SHParseDisplayName.argtypes = [wintypes.LPCWSTR, c_void_p, POINTER(c_void_p), wintypes.ULONG,
                                       POINTER(wintypes.ULONG)]
shell32.SHGetImageList.argtypes = [c_int, POINTER(GUID), POINTER(c_void_p)]

IID_IImageList = GUID(0x46EB5926, 0x582E, 0x4017,
                      (ctypes.c_ubyte * 8)(0x9F, 0xDF, 0xE8, 0x99, 0x8D, 0xAA, 0x09, 0x50))
SHGFI_SYSICONINDEX, SHGFI_PIDL = 0x4000, 0x8
SHIL_EXTRALARGE = 2  # 48x48


def _sys_image_icon(path):
    """Иконка из системного списка (48px). Работает для .lnk, папок, shell:AppsFolder\\…"""
    info = SHFILEINFOW()
    pidl = c_void_p()
    try:
        if path.lower().startswith("shell:"):
            if shell32.SHParseDisplayName(path, None, byref(pidl), 0, None) != 0 or not pidl.value:
                return None
            ok = _SHGetFileInfo_pidl(pidl, 0, byref(info), ctypes.sizeof(info),
                                     SHGFI_SYSICONINDEX | SHGFI_PIDL)
        else:
            ok = _SHGetFileInfo_str(path, 0, byref(info), ctypes.sizeof(info), SHGFI_SYSICONINDEX)
        if not ok:
            return None
        il = c_void_p()
        if shell32.SHGetImageList(SHIL_EXTRALARGE, byref(IID_IImageList), byref(il)) != 0:
            return None
        vtbl = ctypes.cast(ctypes.cast(il, POINTER(c_void_p))[0], POINTER(c_void_p))
        get_icon = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, c_int, wintypes.UINT,
                                      POINTER(c_void_p))(vtbl[10])
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vtbl[2])
        hicon = c_void_p()
        hr = get_icon(il, info.iIcon, 1, byref(hicon))  # ILD_TRANSPARENT
        release(il)
        return hicon.value if hr == 0 and hicon.value else None
    finally:
        if pidl.value:
            ole32.CoTaskMemFree(pidl)


def _lnk_target_exe(path):
    try:
        import win32com.client
        sc = win32com.client.Dispatch("WScript.Shell").CreateShortcut(path)
        src, _, idx = (sc.IconLocation or "").rpartition(",")
        src = os.path.expandvars(src)
        if src and os.path.exists(src):
            return src, int(idx or 0)
        target = os.path.expandvars(sc.TargetPath or "")
        if target.lower().endswith(".exe") and os.path.exists(target):
            return target, 0
    except Exception:
        pass
    return None, 0


def _extract_hicon(path, size):
    src, idx = (path, 0)
    if path.lower().endswith(".lnk"):
        src, idx = _lnk_target_exe(path)
    if src and os.path.splitext(src)[1].lower() in (".exe", ".dll", ".ico", ".icl", ".cpl"):
        h, iid = HANDLE(), wintypes.UINT()
        n = user32.PrivateExtractIconsW(src, idx, size, size, byref(h), byref(iid), 1, 0)
        if n and n != 0xFFFFFFFF and h.value:
            return h.value
    return _sys_image_icon(path)


def _render(hicon, size, bg_hex):
    r, g, b = (int(bg_hex[i:i + 2], 16) for i in (1, 3, 5))
    screen = user32.GetDC(None)
    dc = gdi32.CreateCompatibleDC(screen)
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), size, -size, 1, 32, 0, 0, 0, 0, 0, 0)
    bits = c_void_p()
    bmp = gdi32.CreateDIBSection(dc, byref(bmi), 0, byref(bits), None, 0)
    old = gdi32.SelectObject(dc, bmp)
    brush = gdi32.CreateSolidBrush(r | (g << 8) | (b << 16))
    user32.FillRect(dc, byref(wintypes.RECT(0, 0, size, size)), brush)
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


def file_icon(path, size, bg_hex, name="", accent="#4fa3ff"):
    hicon = None
    try:
        if path.lower().startswith("shell:") or os.path.exists(path):
            hicon = _extract_hicon(path, size)
        if hicon:
            return _render(hicon, size, bg_hex)
    except Exception:
        pass
    finally:
        if hicon:
            user32.DestroyIcon(hicon)
    return letter_icon(name or os.path.basename(path), size, bg_hex, accent)
