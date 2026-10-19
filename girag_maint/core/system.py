"""Utilidades de sistema: administrador, versión de Windows, rutas de datos."""
import ctypes
import json
import os
import platform
import struct
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


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def protected_roots() -> list:
    """Rutas que NINGUNA limpieza debe tocar: el entorno temporal del .exe (PyInstaller extrae a
    %TEMP%\\_MEIxxxx y corre desde ahí), la carpeta del ejecutable y los datos de la app."""
    roots = []
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        roots.append(mei)
    if is_frozen():
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    try:
        roots.append(str(data_dir()))
    except OSError:
        pass
    return [os.path.normcase(os.path.abspath(r)) for r in roots]


def is_protected(path: str, roots=None) -> bool:
    p = os.path.normcase(os.path.abspath(path))
    return any(p == r or p.startswith(r + os.sep) for r in (protected_roots() if roots is None else roots))


def runtime_ok():
    """(True, '') si el entorno del .exe sigue intacto; si alguien borró %TEMP%\\_MEI..., (False, mensaje)."""
    if not is_frozen():
        return True, ""
    mei = getattr(sys, "_MEIPASS", None)
    if mei and not os.path.isfile(os.path.join(mei, "base_library.zip")):
        return False, ("Los archivos temporales de la aplicación (" + mei + ") fueron eliminados mientras "
                       "estaba abierta, por lo que no puede cargar sus componentes.\n\n"
                       "Cierra la aplicación y ábrela de nuevo.")
    return True, ""


def is_32bit_on_64bit() -> bool:
    """Python de 32 bits sobre Windows de 64: el sistema de archivos/registro quedan redirigidos."""
    return IS_WINDOWS and struct.calcsize("P") == 4 and "PROCESSOR_ARCHITEW6432" in os.environ


_CONFIG_CACHE = {}


def load_json_config(name: str, default=None):
    """Lee config/<name>. Orden: C:\\ProgramData\\GiragMaintenance\\config\\<name> (personalizable, sobrevive
    entre ejecuciones) y luego la copia incluida en la app. Queda en memoria: si alguien borra el archivo
    con la app abierta, se sigue usando la copia ya leída."""
    if name in _CONFIG_CACHE:
        return _CONFIG_CACHE[name]
    for path in (data_dir() / "config" / name, resource_path("config/" + name)):
        try:
            with open(path, encoding="utf-8") as f:
                _CONFIG_CACHE[name] = json.load(f)
            return _CONFIG_CACHE[name]
        except (OSError, ValueError):
            continue
    if default is not None:
        return default
    raise FileNotFoundError(f"No se encontró la configuración {name}")


def system_drive() -> str:
    return os.environ.get("SystemDrive", "C:") + "\\"


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
