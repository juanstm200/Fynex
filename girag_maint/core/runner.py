"""Ejecuta una o varias tareas en secuencia (hilo de trabajo). Sin dependencias de Tk."""
import time
import traceback
from pathlib import Path

from .context import Cancelled, TaskContext, TaskResult


def run_tasks(tasks, events, cancel_event, dry_run=False, log_dir=None, set_ctx=None):
    """Eventos emitidos: ("begin", i, n, task), ("end", task, result), ("done", [(task, result)]).
    Cancelar detiene el lote completo; la cancelación de un diálogo solo omite esa tarea."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    results, n = [], len(tasks)
    for i, task in enumerate(tasks, 1):
        if cancel_event.is_set():
            results.append((task, TaskResult(False, "No se ejecutó (proceso cancelado).")))
            continue
        log_file = Path(log_dir) / f"{task.id}_{stamp}.log" if log_dir else None
        ctx = TaskContext(events, cancel_event, dry_run=dry_run, log_file=log_file)
        if set_ctx:
            set_ctx(ctx)
        events.put(("begin", i, n, task))
        try:
            result = task.run(ctx)
        except Cancelled:
            result = TaskResult(False, "Tarea cancelada por el usuario.")
        except Exception as e:
            ctx.log(traceback.format_exc(), "err")
            result = TaskResult(False, f"Error inesperado: {e}")
        events.put(("end", task, result))
        results.append((task, result))
    events.put(("done", results))
    return results
