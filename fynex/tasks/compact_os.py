"""5. Compact OS: comprime los archivos del sistema para recuperar espacio."""
import shutil
import time

from ..core import system
from ..core.context import TaskContext, TaskResult
from ..core.process import stream_process
from ..core.system import human_size
from .base import Task


class CompactOsTask(Task):
    id = "compactos"
    title = "Compactar sistema (Compact OS)"
    description = ("Comprime los archivos de Windows (compact /CompactOS:always) para "
                   "recuperar 1.5–3 GB. Tarda 10–30 min.")
    icon = "C"
    button = "Compactar"

    def _query(self, ctx):
        out = []
        stream_process(["compact.exe", "/CompactOS:query"], ctx, on_text=out.append,
                       on_progress=lambda p, t: out.append(t))
        return " ".join(out).strip()

    def run(self, ctx: TaskContext) -> TaskResult:
        ctx.progress(None)
        ctx.status("Consultando estado actual…")
        before_state = self._query(ctx)
        ctx.log(f"Estado actual: {before_state}", "info")
        drive = system.system_drive()
        free_before = shutil.disk_usage(drive).free

        if ctx.dry_run:
            return TaskResult(True, "Simulación: no se ejecutó compact.", before_state)
        if not ctx.confirm("Compact OS",
                           f"Estado actual:\n{before_state}\n\n"
                           "Se comprimirá el sistema operativo. Recomendado en SSD; en discos "
                           "mecánicos (HDD) o equipos lentos puede notarse más lento. "
                           "No apagues el equipo durante el proceso."):
            return TaskResult(False, "Compact OS cancelado por el usuario.")

        ctx.status("Compactando el sistema operativo… (puede tardar varios minutos)")
        ctx.progress(None)
        t0 = time.time()
        rc = stream_process(["compact.exe", "/CompactOS:always"], ctx,
                            on_progress=lambda pct, text: (ctx.progress(pct), ctx.status(f"Compactando — {pct:.0f} %")),
                            on_text=lambda text: ctx.log("  " + text))
        minutes = (time.time() - t0) / 60
        after_state = self._query(ctx)
        gained = shutil.disk_usage(drive).free - free_before
        ctx.log(f"Estado final: {after_state}", "info")
        if rc != 0:
            return TaskResult(False, f"compact terminó con código {rc}.", after_state)
        ctx.progress(100)
        return TaskResult(True, f"Compact OS completado en {minutes:.1f} min · "
                                f"espacio recuperado: {human_size(max(gained, 0))}", after_state)
