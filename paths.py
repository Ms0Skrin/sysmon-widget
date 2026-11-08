"""Пути программы: из исходников (python widget.py) или установленная версия (SysMonWidget.exe)."""
import os
import sys

FROZEN = getattr(sys, "frozen", False)
# папка с exe (установка) или с исходниками
APP_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(os.path.abspath(__file__))

# сторонние программы, уложенные установщиком
THIRD_PARTY = os.path.join(APP_DIR, "third_party")
# библиотеки LibreHardwareMonitor
LHM_DIR = os.path.join(APP_DIR, "lhm") if FROZEN else os.path.join(SRC_DIR, "vendor", "lhm")

DATA_DIR = os.path.join(os.environ.get("APPDATA", "."), "sysmon-widget")
LOCAL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "sysmon-widget")


def pythonw():
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return pyw if os.path.exists(pyw) else sys.executable


def widget_cmd(*args):
    """Команда запуска виджета."""
    if FROZEN:
        return [os.path.join(APP_DIR, "SysMonWidget.exe"), *args]
    return [pythonw(), os.path.join(SRC_DIR, "widget.py"), *args]


def sensors_cmd(*args):
    """Команда запуска процесса датчиков."""
    if FROZEN:
        return [os.path.join(APP_DIR, "SysMonSensors.exe"), *args]
    return [pythonw(), os.path.join(SRC_DIR, "sensor_host.py"), *args]


def quote(cmd):
    return " ".join(f'"{c}"' if " " in c or not c else c for c in cmd)
