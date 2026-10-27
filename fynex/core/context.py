"""Contexto que recibe cada tarea: comunica con la UI mediante una cola de eventos."""
import queue
import subprocess
import threading
import time
from dataclasses import dataclass

from . import system


class Cancelled(Exception):
    """El usuario canceló la tarea."""


@dataclass
class TaskResult:
    ok: bool
    summary: str
    details: str = ""


class TaskContext:
    def __init__(self, events: "queue.Queue", cancel_event: threading.Event,
                 dry_run: bool = False, log_file=None, auto_approve: bool = False):
        self.events = events
        self.cancel_event = cancel_event
        self.dry_run = dry_run
        self.log_file = log_file
        # True cuando el usuario ya aprobó el lote completo una sola vez: confirm()/choose() no vuelven a preguntar.
        self.auto_approve = auto_approve
        self._proc = None

    def _emit(self, kind, *payload):
        self.events.put((kind, *payload))

    def log(self, msg: str, level: str = "info", ui: bool = True):
        if ui:
            self._emit("log", msg, level)
        if self.log_file:
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%H:%M:%S')} [{level}] {msg}\n")
            except OSError:
                pass

    def status(self, text: str):
        self._emit("status", text)

    def progress(self, pct):
        """pct 0-100, o None para progreso indeterminado."""
        self._emit("progress", None if pct is None else max(0.0, min(100.0, float(pct))))

    def confirm(self, title: str, message: str, details: str = "") -> bool:
        if self.auto_approve:
            # Ya aprobado al iniciar el lote: no se pregunta de nuevo, pero queda constancia en pantalla y en el log
            # (el detalle largo solo va al archivo de log para no llenar la consola).
            self.check_cancel()
            self.log(f"✔ Aprobado en el lote: {title}", "dim")
            self.log(f"{message}\n{details}".strip(), "dim", ui=False)
            return True
        reply: "queue.Queue" = queue.Queue()
        self._emit("confirm", title, message, details, reply)
        while True:
            self.check_cancel()
            try:
                return bool(reply.get(timeout=0.2))
            except queue.Empty:
                pass

    def choose(self, title: str, message: str, items: list, preselected=(), remember_label: str = ""):
        """Diálogo de selección múltiple. items: [{id,name,detail,approved,disabled}].
        Devuelve (ids_seleccionados, recordar) o None si el usuario cancela.
        Con el lote ya aprobado se usan las preseleccionadas (p. ej. la lista de aprobadas ★) sin diálogo; si no
        hay ninguna preseleccionada sí se pregunta, porque entonces no hay nada aprobado de antemano."""
        if self.auto_approve:
            ids = [it["id"] for it in items if it["id"] in set(preselected) and not it.get("disabled")]
            if ids:
                self.check_cancel()
                self.log(f"✔ Aprobado en el lote: {title} ({len(ids)} de {len(items)} elementos, "
                         "los de la lista de aprobadas)", "dim")
                self.log("Seleccionadas: " + ", ".join(ids), "dim", ui=False)
                return ids, False
        reply: "queue.Queue" = queue.Queue()
        self._emit("choose", title, message, items, set(preselected), remember_label, reply)
        while True:
            self.check_cancel()
            try:
                return reply.get(timeout=0.2)
            except queue.Empty:
                pass

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise Cancelled()

    def set_process(self, proc):
        self._proc = proc

    def kill_process(self):
        p = self._proc
        if p and p.poll() is None:
            try:
                if system.IS_WINDOWS:
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"],
                                   capture_output=True, creationflags=system.CREATE_NO_WINDOW)
                else:
                    p.kill()
            except Exception:
                pass
