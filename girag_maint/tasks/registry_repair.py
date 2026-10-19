"""4. Reparación de registro estilo Glary Utilities: busca entradas huérfanas,
hace COPIA DE SEGURIDAD (.reg) y punto de restauración, y luego las elimina."""
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from ..core import system
from ..core.context import TaskContext, TaskResult
from ..core.process import run_powershell
from .base import Task

try:
    import winreg
except ImportError:  # no Windows (desarrollo)
    winreg = None

VIEW = winreg.KEY_WOW64_64KEY if winreg else 0
CV = r"SOFTWARE\Microsoft\Windows\CurrentVersion"
CV32 = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion"


@dataclass
class Issue:
    hive_name: str          # "HKLM" | "HKCU"
    path: str               # clave afectada (o clave padre si value es un valor)
    value: Optional[str]    # None => se elimina la clave completa
    category: str
    description: str

    @property
    def hive(self):
        return winreg.HKEY_LOCAL_MACHINE if self.hive_name == "HKLM" else winreg.HKEY_CURRENT_USER


# ---------- helpers puros (testeables sin Windows) ----------
def extract_exe_path(cmd: str) -> Optional[str]:
    cmd = os.path.expandvars((cmd or "").strip())
    if not cmd:
        return None
    if cmd[0] == '"':
        end = cmd.find('"', 1)
        return cmd[1:end] if end > 1 else None
    m = re.match(r"^(.+?\.(?:exe|bat|cmd|com|msc|cpl))(?:\s|$|,)", cmd, re.I)
    return m.group(1) if m else None


def syswow_variant(p: str) -> str:
    """C:\\Windows\\system32\\x.dll -> C:\\Windows\\SysWOW64\\x.dll (donde viven las DLL de 32 bits)."""
    return re.sub(r"(?i)^([A-Za-z]:\\[^\\]+)\\system32\\", r"\1\\SysWOW64\\", p)


def definitely_missing(path: Optional[str], exists=os.path.exists, wow32: bool = False) -> bool:
    """True solo si es una ruta absoluta, sin variables sin resolver, que no existe.
    wow32=True (claves WOW6432Node, apps de 32 bits): system32 equivale a SysWOW64, se mira también allí."""
    if not path:
        return False
    p = os.path.expandvars(path.strip().strip('"'))
    p = re.sub(r",\s*-?\d+$", "", p)  # "icono.exe,0"
    if "%" in p or not re.match(r"^[A-Za-z]:\\", p):
        return False
    if exists(p):
        return False
    if wow32:
        alt = syswow_variant(p)
        if alt != p and exists(alt):
            return False
    return True


# ---------- acceso al registro ----------
def _open(hive, path, write=False):
    access = winreg.KEY_READ | VIEW
    if write:
        access |= winreg.KEY_SET_VALUE | winreg.KEY_WRITE
    return winreg.OpenKey(hive, path, 0, access)


def _subkeys(key):
    i = 0
    while True:
        try:
            yield winreg.EnumKey(key, i)
            i += 1
        except OSError:
            return


def _values(key):
    i = 0
    while True:
        try:
            yield winreg.EnumValue(key, i)
            i += 1
        except OSError:
            return


def _get(key, name):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _hive(name):
    return winreg.HKEY_LOCAL_MACHINE if name == "HKLM" else winreg.HKEY_CURRENT_USER


# ---------- comprobaciones ----------
def check_uninstall():
    for hn, base in (("HKLM", CV + r"\Uninstall"), ("HKLM", CV32 + r"\Uninstall"), ("HKCU", CV + r"\Uninstall")):
        try:
            root = _open(_hive(hn), base)
        except OSError:
            continue
        with root:
            for sub in list(_subkeys(root)):
                try:
                    with _open(_hive(hn), base + "\\" + sub) as k:
                        name = _get(k, "DisplayName")
                        if not name or _get(k, "SystemComponent") == 1 or _get(k, "WindowsInstaller") == 1:
                            continue
                        exe = extract_exe_path(_get(k, "UninstallString") or "")
                        loc = _get(k, "InstallLocation") or ""
                        wow = "WOW6432Node" in base
                        loc_gone = (not loc.strip()) or definitely_missing(loc, wow32=wow)
                        if definitely_missing(exe, wow32=wow) and loc_gone:
                            yield Issue(hn, base + "\\" + sub, None, "Programas desinstalados",
                                        f"{name} (desinstalador no existe)")
                except OSError:
                    continue


def check_app_paths():
    base = CV + r"\App Paths"
    try:
        root = _open(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return
    with root:
        for sub in list(_subkeys(root)):
            try:
                with _open(winreg.HKEY_LOCAL_MACHINE, base + "\\" + sub) as k:
                    exe = extract_exe_path(_get(k, "") or "") or _get(k, "")
                    if isinstance(exe, str) and definitely_missing(exe):
                        yield Issue("HKLM", base + "\\" + sub, None, "Rutas de aplicaciones",
                                    f"{sub} → {exe}")
            except OSError:
                continue


def check_shared_dlls():
    for base in (CV + r"\SharedDLLs", CV32 + r"\SharedDLLs"):
        try:
            with _open(winreg.HKEY_LOCAL_MACHINE, base) as k:
                for name, _data, _t in list(_values(k)):
                    if definitely_missing(name, wow32="WOW6432Node" in base):
                        yield Issue("HKLM", base, name, "DLL compartidas", name)
        except OSError:
            continue


def check_run_entries():
    for hn, base in (("HKLM", CV + r"\Run"), ("HKLM", CV32 + r"\Run"), ("HKCU", CV + r"\Run")):
        try:
            with _open(_hive(hn), base) as k:
                for name, data, _t in list(_values(k)):
                    if isinstance(data, str) and definitely_missing(extract_exe_path(data), wow32="WOW6432Node" in base):
                        yield Issue(hn, base, name, "Programas de inicio", f"{name} → {data}")
        except OSError:
            continue


def check_mui_cache():
    base = r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache"
    try:
        with _open(winreg.HKEY_CURRENT_USER, base) as k:
            for name, _d, _t in list(_values(k)):
                exe = re.sub(r"\.(FriendlyAppName|ApplicationCompany)$", "", name, flags=re.I)
                if definitely_missing(exe):
                    yield Issue("HKCU", base, name, "Historial MUI", exe)
    except OSError:
        return


CHECKS = [("Programas desinstalados", check_uninstall), ("Rutas de aplicaciones", check_app_paths),
          ("DLL compartidas", check_shared_dlls), ("Programas de inicio", check_run_entries),
          ("Historial MUI", check_mui_cache)]


# ---------- copia de seguridad y reparación ----------
def backup(issues, folder) -> list:
    parents = {}
    for i in issues:
        parent = i.path if i.value is not None else i.path.rsplit("\\", 1)[0]
        parents[(i.hive_name, parent)] = True
    files = []
    for n, (hn, parent) in enumerate(parents, 1):
        out = os.path.join(folder, f"{n:02d}_{hn}_{re.sub(r'[^A-Za-z0-9]+', '_', parent)[-60:]}.reg")
        r = subprocess.run(["reg", "export", f"{hn}\\{parent}", out, "/y", "/reg:64"],
                           capture_output=True, creationflags=system.CREATE_NO_WINDOW)
        if r.returncode != 0 or not os.path.exists(out):
            raise RuntimeError(f"No se pudo respaldar {hn}\\{parent}")
        files.append(out)
    return files


def _delete_tree(hive, path):
    with _open(hive, path, write=True) as k:
        for sub in list(_subkeys(k)):
            _delete_tree(hive, path + "\\" + sub)
    winreg.DeleteKeyEx(hive, path, VIEW, 0)


def fix_issue(issue: Issue):
    if issue.value is None:
        _delete_tree(issue.hive, issue.path)
    else:
        with _open(issue.hive, issue.path, write=True) as k:
            winreg.DeleteValue(k, issue.value)


class RegistryRepairTask(Task):
    id = "registry"
    title = "Reparar registro"
    description = ("Busca entradas huérfanas como Glary Utilities. Crea respaldo .reg y "
                   "punto de restauración antes de borrar.")
    icon = "G"
    button = "Reparar registro"

    def run(self, ctx: TaskContext) -> TaskResult:
        if winreg is None:
            return TaskResult(False, "El registro solo existe en Windows.")
        if system.is_32bit_on_64bit():
            return TaskResult(False, "Esta aplicación se está ejecutando como programa de 32 bits en un "
                                     "Windows de 64 bits: el registro y las carpetas del sistema quedarían "
                                     "redirigidos y se marcarían entradas válidas. Usa la versión de 64 bits.")
        issues, report = [], []
        for n, (cat, fn) in enumerate(CHECKS):
            ctx.check_cancel()
            ctx.status(f"Analizando: {cat}")
            ctx.progress(n / len(CHECKS) * 100)
            found = list(fn())
            issues.extend(found)
            ctx.log(f"{cat}: {len(found)} problemas", "warn" if found else "dim")
            report.append(f"{cat}: {len(found)}")
        ctx.progress(100)

        stamp = time.strftime("%Y%m%d_%H%M%S")
        listing = system.logs_dir() / f"registro_problemas_{stamp}.txt"
        listing.write_text("\n".join(f"[{i.category}] {i.hive_name}\\{i.path}"
                                     f"{'  valor: ' + i.value if i.value else ''}  — {i.description}"
                                     for i in issues), encoding="utf-8")
        if not issues:
            return TaskResult(True, "El registro está limpio: no se encontraron entradas huérfanas.")
        ctx.log(f"Lista completa guardada en {listing}", "dim")

        sample = "\n".join(f"[{i.category}] {i.description}" for i in issues[:60])
        if len(issues) > 60:
            sample += f"\n… y {len(issues) - 60} más (ver archivo de log)."
        if ctx.dry_run:
            return TaskResult(True, f"Simulación: {len(issues)} entradas por reparar.", "\n".join(report))
        if not ctx.confirm("Reparar registro",
                           f"Se encontraron {len(issues)} entradas inválidas.\n"
                           "Se hará una copia de seguridad (.reg) y un punto de restauración antes de eliminarlas.",
                           sample):
            return TaskResult(False, "Reparación de registro cancelada por el usuario.")

        ctx.status("Creando punto de restauración…")
        ctx.progress(None)
        try:
            rc, msg = run_powershell("Checkpoint-Computer -Description 'Girag Mantenimiento' "
                                     "-RestorePointType MODIFY_SETTINGS -ErrorAction Stop", timeout=180)
            ctx.log("Punto de restauración creado." if rc == 0 else
                    f"No se pudo crear punto de restauración (continúa con respaldo .reg): {msg[:160]}",
                    "ok" if rc == 0 else "warn")
        except Exception as e:
            ctx.log(f"Punto de restauración omitido: {e}", "warn")

        folder = system.backups_dir() / f"registro_{stamp}"
        folder.mkdir(parents=True, exist_ok=True)
        ctx.status("Guardando copia de seguridad del registro…")
        try:
            files = backup(issues, str(folder))
        except Exception as e:
            return TaskResult(False, f"Abortado: no se pudo crear el respaldo. No se borró nada. ({e})")
        ctx.log(f"Respaldo: {folder} ({len(files)} archivos .reg)", "ok")

        fixed = errors = 0
        for n, issue in enumerate(issues, 1):
            ctx.check_cancel()
            ctx.status(f"Reparando {n}/{len(issues)}: {issue.category}")
            try:
                fix_issue(issue)
                fixed += 1
            except OSError as e:
                errors += 1
                ctx.log(f"✘ {issue.description}: {e}", "err")
            ctx.progress(n / len(issues) * 100)
        ctx.log(f"Reparadas {fixed} entradas.", "ok")
        summary = f"Reparadas {fixed} de {len(issues)} entradas. Respaldo en {folder}"
        return TaskResult(errors == 0, summary + (f" · {errors} con error" if errors else ""),
                          "\n".join(report))
