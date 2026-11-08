# -*- mode: python ; coding: utf-8 -*-
# Сборка: два exe (виджет и процесс датчиков) в одной папке dist/SysMonWidget.
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(ROOT, "assets", "app.ico")

w = Analysis([os.path.join(ROOT, "widget.py")], pathex=[ROOT],
             hiddenimports=["win32timezone"], excludes=["pythonnet", "clr_loader", "pystray"])

datas, binaries, hidden = [], [], []
for pkg in ("pythonnet", "clr_loader"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h
s = Analysis([os.path.join(ROOT, "sensor_host.py")], pathex=[ROOT], datas=datas, binaries=binaries,
             hiddenimports=hidden + ["clr"], excludes=["tkinter", "PIL", "pystray"])

w_exe = EXE(PYZ(w.pure), w.scripts, [], exclude_binaries=True, name="SysMonWidget", icon=ICON,
            console=False, uac_admin=False)
s_exe = EXE(PYZ(s.pure), s.scripts, [], exclude_binaries=True, name="SysMonSensors", icon=ICON,
            console=False, uac_admin=False)

COLLECT(w_exe, w.binaries, w.datas, s_exe, s.binaries, s.datas, name="SysMonWidget")
