"""3. Eliminación de bloatware (aplicaciones Appx preinstaladas) con un botón."""
import json
from fnmatch import fnmatch

from ..core import system
from ..core.context import TaskContext, TaskResult
from ..core.process import run_powershell
from .base import Task

LIST_SCRIPT = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$pk=Get-AppxPackage -AllUsers|Select-Object Name,PackageFullName,NonRemovable,IsFramework,SignatureKind;"
    "$pv=Get-AppxProvisionedPackage -Online|Select-Object DisplayName,PackageName;"
    "@{packages=@($pk);provisioned=@($pv)}|ConvertTo-Json -Compress -Depth 4"
)


def load_config():
    return system.load_json_config("bloatware.json")


def _as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def matches(name: str, patterns) -> bool:
    n = name.lower()
    return any(fnmatch(n, p.lower()) for p in patterns)


def build_plan(data: dict, cfg: dict):
    """Devuelve {nombre: {'full': [PackageFullName], 'prov': [PackageName]}} de lo que se eliminará."""
    remove, protect = cfg["remove"], cfg["protect"]
    plan = {}
    for p in _as_list(data.get("packages")):
        name = p.get("Name") or ""
        if (not name or p.get("NonRemovable") or p.get("IsFramework")
                or str(p.get("SignatureKind", "")).lower() == "system"):
            continue
        if matches(name, remove) and not matches(name, protect):
            entry = plan.setdefault(name, {"full": [], "prov": []})
            if p["PackageFullName"] not in entry["full"]:
                entry["full"].append(p["PackageFullName"])
    for p in _as_list(data.get("provisioned")):
        name = p.get("DisplayName") or ""
        if name and matches(name, remove) and not matches(name, protect):
            entry = plan.setdefault(name, {"full": [], "prov": []})
            if p["PackageName"] not in entry["prov"]:
                entry["prov"].append(p["PackageName"])
    return plan


class BloatwareTask(Task):
    id = "bloatware"
    title = "Eliminar bloatware"
    description = ("Quita apps preinstaladas (juegos, Bing, TikTok…). Protege Tienda, "
                   "Calculadora y Office. Lista editable (ver README).")
    icon = "B"
    button = "Eliminar bloatware"

    def run(self, ctx: TaskContext) -> TaskResult:
        ctx.status("Buscando aplicaciones preinstaladas…")
        ctx.progress(None)
        cfg = load_config()
        rc, out = run_powershell(LIST_SCRIPT, timeout=300)
        try:
            data = json.loads(out[out.index("{"):out.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            ctx.log(out[:500], "err")
            return TaskResult(False, "No se pudo leer la lista de aplicaciones (PowerShell).")
        plan = build_plan(data, cfg)
        if not plan:
            return TaskResult(True, "No se encontró bloatware de la lista.")

        names = sorted(plan)
        for n in names:
            ctx.log(f"Detectado: {n}", "dim")
        if ctx.dry_run:
            return TaskResult(True, f"Simulación: se eliminarían {len(names)} aplicaciones.", "\n".join(names))
        if not ctx.confirm("Eliminar bloatware",
                           f"Se eliminarán {len(names)} aplicaciones para todos los usuarios y se "
                           "quitarán del perfil base (no volverán con usuarios nuevos).",
                           "\n".join(names)):
            return TaskResult(False, "Eliminación cancelada por el usuario.")

        removed, failed = [], []
        for i, name in enumerate(names):
            ctx.check_cancel()
            ctx.status(f"Eliminando {name} ({i + 1}/{len(names)})")
            cmds = [f"Remove-AppxPackage -Package '{f}' -AllUsers -ErrorAction Stop" for f in plan[name]["full"]]
            cmds += [f"Remove-AppxProvisionedPackage -Online -PackageName '{p}' -ErrorAction Stop|Out-Null"
                     for p in plan[name]["prov"]]
            script = "try{" + ";".join(cmds) + "}catch{Write-Output $_.Exception.Message;exit 1}"
            try:
                rc, msg = run_powershell(script, timeout=300)
            except Exception as e:  # tiempo agotado, etc.
                rc, msg = 1, str(e)
            if rc == 0:
                removed.append(name)
                ctx.log(f"✔ {name}", "ok")
            else:
                failed.append(name)
                ctx.log(f"✘ {name}: {msg[:200]}", "err")
            ctx.progress((i + 1) / len(names) * 100)

        summary = f"Eliminadas {len(removed)} de {len(names)} aplicaciones."
        if failed:
            return TaskResult(False, summary + f" Fallaron {len(failed)}.", "Fallidas:\n" + "\n".join(failed))
        return TaskResult(True, summary, "\n".join(removed))
