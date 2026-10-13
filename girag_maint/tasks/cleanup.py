"""1. Limpieza de caché de aplicaciones y temporales de Windows."""
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..core import system
from ..core.context import TaskContext, TaskResult
from ..core.system import human_size
from .base import Task

REPARSE_POINT = 0x400


@dataclass
class Target:
    category: str
    label: str
    path: Path


def _profiles():
    users = Path(system.system_drive()) / "Users"
    skip = {"All Users", "Default", "Default User", "Public"}
    if not users.is_dir():
        return []
    return [p for p in users.iterdir()
            if p.is_dir() and p.name not in skip and (p / "AppData").is_dir()]


def build_targets():
    """Lista de rutas a limpiar. Para añadir más cachés, agregar líneas aquí."""
    win = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    pdata = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    out = []

    def add(cat, label, path):
        out.append(Target(cat, label, Path(path)))

    def add_glob(cat, label, base, pattern):
        base = Path(base)
        if base.is_dir():
            for p in base.glob(pattern):
                out.append(Target(cat, label, p))

    add("Windows", "Temporales de Windows", win / "Temp")
    add("Windows", "Descargas de Windows Update", win / "SoftwareDistribution" / "Download")
    add("Windows", "Volcados de memoria (Minidump)", win / "Minidump")
    add("Windows", "Informes de errores (WER)", pdata / "Microsoft" / "Windows" / "WER" / "ReportArchive")
    add("Windows", "Informes de errores (WER)", pdata / "Microsoft" / "Windows" / "WER" / "ReportQueue")

    for prof in _profiles():
        L = prof / "AppData" / "Local"
        R = prof / "AppData" / "Roaming"
        u = prof.name
        add("Usuario", f"Temporales de {u}", L / "Temp")
        add("Usuario", f"Caché de Internet/Windows de {u}", L / "Microsoft" / "Windows" / "INetCache")
        add("Usuario", f"Volcados de aplicaciones de {u}", L / "CrashDumps")
        add("Usuario", f"Caché de sombreadores de {u}", L / "D3DSCache")
        add_glob("Usuario", f"Miniaturas de {u}", L / "Microsoft" / "Windows" / "Explorer", "thumbcache_*.db")
        add("Aplicaciones", f"Caché de Office de {u}", L / "Microsoft" / "Office" / "16.0" / "OfficeFileCache")
        for sub in ("Cache", "Code Cache", "GPUCache", "blob_storage", "tmp"):
            add("Aplicaciones", f"Caché de Teams (clásico) de {u}", R / "Microsoft" / "Teams" / sub)
        for browser, base in (("Chrome", L / "Google" / "Chrome" / "User Data"),
                              ("Edge", L / "Microsoft" / "Edge" / "User Data")):
            for pat in ("*/Cache/Cache_Data", "*/Code Cache", "*/GPUCache",
                        "*/Service Worker/CacheStorage", "ShaderCache", "GrShaderCache"):
                add_glob("Navegadores", f"Caché de {browser} de {u}", base, pat)
        add_glob("Navegadores", f"Caché de Firefox de {u}", L / "Mozilla" / "Firefox" / "Profiles", "*/cache2")
    return [t for t in out if t.path.exists()]


def _is_reparse(entry) -> bool:
    try:
        if entry.is_symlink():
            return True
        return bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & REPARSE_POINT)
    except OSError:
        return False


def collect(root: Path):
    """Devuelve (archivos[(ruta, tamaño)], carpetas, enlaces). No sigue enlaces/junctions."""
    files, dirs, links = [], [], []
    if root.is_file():
        try:
            return [(str(root), root.stat().st_size)], [], []
        except OSError:
            return [], [], []
    stack = [str(root)]
    while stack:
        cur = stack.pop()
        try:
            it = os.scandir(cur)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if _is_reparse(e):
                        links.append(e.path)
                    elif e.is_dir(follow_symlinks=False):
                        dirs.append(e.path)
                        stack.append(e.path)
                    else:
                        files.append((e.path, e.stat(follow_symlinks=False).st_size))
                except OSError:
                    continue
    return files, dirs, links


def delete_file(path: str) -> bool:
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return True
    except PermissionError:
        try:
            os.chmod(path, stat.S_IWRITE)
            os.unlink(path)
            return True
        except OSError:
            return False
    except OSError:
        return False


def _service_running(name: str) -> bool:
    try:
        r = subprocess.run(["sc", "query", name], capture_output=True, text=True,
                           creationflags=system.CREATE_NO_WINDOW)
        return "RUNNING" in r.stdout
    except Exception:
        return False


def _service(action: str, name: str):
    try:
        subprocess.run(["net", action, name], capture_output=True, timeout=90,
                       creationflags=system.CREATE_NO_WINDOW)
    except Exception:
        pass


class CleanupTask(Task):
    id = "cleanup"
    title = "Limpiar caché y temporales"
    description = ("Temporales de Windows, Windows Update, miniaturas, errores y caché de "
                   "Chrome, Edge, Firefox, Teams y Office.")
    icon = "L"
    button = "Limpiar ahora"

    def run(self, ctx: TaskContext) -> TaskResult:
        ctx.status("Analizando carpetas…")
        ctx.progress(None)
        targets = build_targets()
        plan, total_files, total_bytes = [], 0, 0
        for t in targets:
            ctx.check_cancel()
            files, dirs, links = collect(t.path)
            if not (files or dirs or links):
                continue
            size = sum(s for _, s in files)
            plan.append((t, files, dirs, links))
            total_files += len(files)
            total_bytes += size
            ctx.log(f"{t.label}: {len(files)} archivos ({human_size(size)})", "dim")

        if total_files == 0 and not any(p[3] for p in plan):
            return TaskResult(True, "No había archivos que limpiar.")

        by_cat = {}
        for t, files, _, _ in plan:
            by_cat[t.category] = by_cat.get(t.category, 0) + sum(s for _, s in files)
        resumen = "\n".join(f"  • {c}: {human_size(b)}" for c, b in by_cat.items())
        ctx.log(f"Total detectado: {total_files} archivos, {human_size(total_bytes)}", "warn")

        if ctx.dry_run:
            return TaskResult(True, f"Simulación: se liberarían {human_size(total_bytes)} "
                                    f"({total_files} archivos).", resumen)
        if not ctx.confirm("Confirmar limpieza",
                           f"Se eliminarán {total_files} archivos ({human_size(total_bytes)}).\n"
                           "Cierra navegadores y Teams antes de continuar para liberar más espacio.",
                           resumen):
            return TaskResult(False, "Limpieza cancelada por el usuario.")

        stopped = []
        if any("SoftwareDistribution" in str(t.path) for t, *_ in plan):
            for svc in ("wuauserv", "bits"):
                if _service_running(svc):
                    ctx.log(f"Deteniendo servicio {svc}…", "dim")
                    _service("stop", svc)
                    stopped.append(svc)

        freed = deleted = skipped = done = 0
        try:
            for t, files, dirs, links in plan:
                ctx.status(f"Limpiando: {t.label}")
                for i, (path, size) in enumerate(files):
                    ctx.check_cancel()
                    if delete_file(path):
                        freed += size
                        deleted += 1
                    else:
                        skipped += 1
                    done += 1
                    if i % 25 == 0:
                        ctx.progress(done / max(total_files, 1) * 100)
                for link in links:
                    try:
                        os.unlink(link)
                    except OSError:
                        try:
                            os.rmdir(link)
                        except OSError:
                            pass
                for d in sorted(dirs, key=len, reverse=True):
                    try:
                        os.rmdir(d)
                    except OSError:
                        pass
                ctx.log(f"✔ {t.label} (liberado hasta ahora: {human_size(freed)})", "ok")
        finally:
            for svc in stopped:
                ctx.log(f"Reiniciando servicio {svc}…", "dim")
                _service("start", svc)

        ctx.progress(100)
        msg = f"Liberados {human_size(freed)} · {deleted} archivos eliminados"
        if skipped:
            msg += f" · {skipped} en uso (omitidos)"
        return TaskResult(True, msg)
