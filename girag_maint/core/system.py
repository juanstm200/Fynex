"""Utilidades de sistema: administrador, versión de Windows, rutas de datos."""
import ctypes
import os
import platform
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def is_admin() -> bool:
    if not IS_WINDOWS:
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """Relanza el programa con UAC. Devuelve True si el usuario aceptó."""
    if not IS_WINDOWS:
        return False
    exe = sys.executable
    if getattr(sys, "frozen", False):
        args = sys.argv[1:]
    else:
        pyw = exe.lower().replace("python.exe", "pythonw.exe")
        if os.path.exists(pyw):
            exe = pyw
        args = sys.argv
    params = " ".join(f'"{a}"' for a in args)
    try:
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1) > 32
    except Exception:
        return False


def enable_dpi_awareness() -> None:
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def windows_version() -> str:
    if not IS_WINDOWS:
        return platform.platform()
    try:
        build = sys.getwindowsversion().build
        return f"{'Windows 11' if build >= 22000 else 'Windows 10'} (build {build})"
    except Exception:
        return platform.platform()


def resource_path(rel: str) -> Path:
    """Ruta a un recurso, funciona en desarrollo y empaquetado con PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / rel


def data_dir() -> Path:
    if IS_WINDOWS:
        root = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "GiragMaintenance"
    else:
        root = Path.home() / ".girag_maintenance"
    root.mkdir(parents=True, exist_ok=True)
    return root


def logs_dir() -> Path:
    p = data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backups_dir() -> Path:
    p = data_dir() / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def system_drive() -> str:
    return os.environ.get("SystemDrive", "C:") + "\\"


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
