"""2. Reparación de Windows con DISM + SFC, con progreso."""
from ..core.context import TaskContext, TaskResult
from ..core.process import stream_process
from .base import Task

DISM = ["DISM.exe", "/Online", "/Cleanup-Image"]
# (título, comando, peso en la barra global)
STEPS = [
    ("Comprobando almacén de componentes (CheckHealth)", DISM + ["/CheckHealth"], 5),
    ("Analizando almacén de componentes (ScanHealth)", DISM + ["/ScanHealth"], 25),
    ("Reparando imagen de Windows (RestoreHealth)", DISM + ["/RestoreHealth"], 40),
    ("Verificando archivos del sistema (SFC /scannow)", ["sfc.exe", "/scannow"], 25),
    ("Limpiando componentes obsoletos (StartComponentCleanup)", DISM + ["/StartComponentCleanup"], 5),
]

SFC_OK = ("did not find any integrity violations", "no encontró ninguna infracción")
SFC_FIXED = ("successfully repaired", "reparó correctamente", "los reparó correctamente")
SFC_BAD = ("unable to fix", "no pudo reparar", "no puede reparar")


def interpret_sfc(text: str) -> str:
    t = text.lower()
    if any(k in t for k in SFC_BAD):
        return "bad"
    if any(k in t for k in SFC_FIXED):
        return "fixed"
    if any(k in t for k in SFC_OK):
        return "ok"
    return "unknown"


class DismRepairTask(Task):
    id = "dism"
    title = "Reparar Windows (DISM + SFC)"
    description = ("CheckHealth, ScanHealth, RestoreHealth y SFC con progreso. "
                   "Puede tardar 15–40 min.")
    icon = "R"
    button = "Reparar sistema"

    def run(self, ctx: TaskContext) -> TaskResult:
        if not ctx.confirm("Reparar Windows",
                           "Se ejecutarán 5 pasos (DISM y SFC). Puede tardar hasta 40 minutos y "
                           "RestoreHealth necesita conexión a Windows Update.\n\n"
                           "Es normal que la barra se detenga un rato en 20 % o 62 %."):
            return TaskResult(False, "Reparación cancelada por el usuario.")

        base, lines, report, all_ok = 0.0, [], [], True
        for title, cmd, weight in STEPS:
            ctx.check_cancel()
            ctx.status(title)
            ctx.log(f"▶ {title}", "warn")
            ctx.log("  " + " ".join(cmd), "dim")
            captured = []

            def on_progress(pct, text, _base=base, _w=weight, _t=title):
                ctx.progress(_base + _w * pct / 100)
                ctx.status(f"{_t} — {pct:.0f} %")

            def on_text(text):
                captured.append(text)
                ctx.log("  " + text, "info")

            rc = stream_process(cmd, ctx, on_progress=on_progress, on_text=on_text)
            blob = "\n".join(captured)
            lines.extend(captured)
            base += weight
            ctx.progress(base)

            if cmd[0].lower().startswith("sfc"):
                verdict = interpret_sfc(blob)
                step_ok = verdict in ("ok", "fixed") or (verdict == "unknown" and rc == 0)
                label = {"ok": "sin infracciones", "fixed": "archivos reparados",
                         "bad": "hay archivos que no se pudieron reparar"}.get(verdict, f"código {rc}")
            else:
                step_ok = rc == 0
                label = "OK" if step_ok else f"error (código {rc})"
                if not step_ok and "RestoreHealth" in title:
                    ctx.log("  Pista: error 0x800f081f/0x800f0906 = no hay acceso a los archivos de "
                            "origen (revisa internet/Windows Update o usa /Source).", "warn")
            all_ok &= step_ok
            report.append(f"{'✔' if step_ok else '✘'} {title.split(' (')[0]}: {label}")
            ctx.log(("✔ " if step_ok else "✘ ") + label, "ok" if step_ok else "err")

        details = "\n".join(report)
        if all_ok:
            return TaskResult(True, "Reparación completada sin errores.", details)
        return TaskResult(False, "Reparación terminada con errores. Revisa el log "
                                 "(también C:\\Windows\\Logs\\CBS\\CBS.log).", details)
