"""Aprobación única del lote, arrastre de ventana seguro y barra sin animación al esperar. Sin Tk ni Windows."""
import queue
import re
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fynex.core.context import TaskContext, TaskResult
from fynex.core.runner import run_tasks
from fynex.tasks.base import Task


def kinds(q):
    out = []
    while not q.empty():
        out.append(q.get())
    return out


class BatchApprovalTests(unittest.TestCase):
    def test_confirm_asks_user_when_not_pre_approved(self):
        q = queue.Queue()
        ctx = TaskContext(q, threading.Event())
        threading.Timer(0.1, lambda: self._answer(q, True)).start()
        self.assertTrue(ctx.confirm("T", "m", "d"))

    @staticmethod
    def _answer(q, value):
        while True:
            ev = q.get()
            if ev[0] == "confirm":
                ev[4].put(value)
                return

    def test_confirm_is_silent_when_pre_approved(self):
        q = queue.Queue()
        ctx = TaskContext(q, threading.Event(), auto_approve=True)
        self.assertTrue(ctx.confirm("Eliminar bloatware", "Se eliminarán 3 apps", "A\nB\nC"))
        evs = kinds(q)
        self.assertNotIn("confirm", [e[0] for e in evs])
        self.assertTrue(any("Aprobado en el lote" in e[1] for e in evs if e[0] == "log"))
        self.assertFalse(any("A\nB\nC" in str(e) for e in evs))      # el detalle largo no inunda la consola

    def test_details_go_to_log_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.log"
            ctx = TaskContext(queue.Queue(), threading.Event(), log_file=f, auto_approve=True)
            ctx.confirm("Limpieza", "Se eliminarán 10 archivos", "temporales: 1 GB")
            text = f.read_text(encoding="utf-8")
            self.assertIn("Aprobado en el lote: Limpieza", text)
            self.assertIn("temporales: 1 GB", text)

    def test_cancel_still_works_when_pre_approved(self):
        from fynex.core.context import Cancelled
        cancel = threading.Event()
        cancel.set()
        ctx = TaskContext(queue.Queue(), cancel, auto_approve=True)
        with self.assertRaises(Cancelled):
            ctx.confirm("T", "m")

    def test_choose_uses_pre_approved_items_without_dialog(self):
        q = queue.Queue()
        ctx = TaskContext(q, threading.Event(), auto_approve=True)
        items = [{"id": "a", "name": "A"}, {"id": "b", "name": "B", "disabled": True}, {"id": "c", "name": "C"}]
        self.assertEqual(ctx.choose("Apps", "m", items, preselected=["a", "b"]), (["a"], False))
        self.assertNotIn("choose", [e[0] for e in kinds(q)])

    def test_choose_still_asks_when_nothing_is_pre_approved(self):
        q = queue.Queue()
        ctx = TaskContext(q, threading.Event(), auto_approve=True)

        def answer():
            while True:
                ev = q.get()
                if ev[0] == "choose":
                    ev[-1].put((["x"], False))
                    return
        threading.Timer(0.1, answer).start()
        self.assertEqual(ctx.choose("Apps", "m", [{"id": "x", "name": "X"}], preselected=[]), (["x"], False))

    def test_runner_applies_one_approval_to_every_task(self):
        asked = []

        class Ask(Task):
            def __init__(self, i):
                self.id = self.title = f"t{i}"

            def run(self, ctx):
                asked.append(ctx.confirm(self.title, "ok?"))
                return TaskResult(True, "hecho")

        q = queue.Queue()
        results = run_tasks([Ask(1), Ask(2), Ask(3)], q, threading.Event(), auto_approve=True)
        self.assertEqual(asked, [True, True, True])
        self.assertTrue(all(r.ok for _, r in results))
        self.assertNotIn("confirm", [e[0] for e in kinds(q)])


class SourceGuardTests(unittest.TestCase):
    """Protegen las correcciones de la v0.6.1 (los módulos de UI importan tkinter, que no siempre existe)."""

    def test_window_drag_never_blocks_inside_ctypes(self):
        src = (ROOT / "fynex" / "ui" / "chrome.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        self.assertIn("PostMessageW(self.hwnd, WM_NCLBUTTONDOWN", code)
        self.assertNotRegex(code, r"SendMessageW\(\s*self\.hwnd,\s*WM_NCLBUTTONDOWN")

    def test_dialogs_pause_the_progress_animation(self):
        src = (ROOT / "fynex" / "ui" / "app.py").read_text(encoding="utf-8")
        self.assertIn("self._ask(ConfirmDialog(", src)
        self.assertIn("self._ask(ChooseDialog(", src)
        self.assertRegex(src, r"def _wait_user\(self, waiting\):[\s\S]+?self\.pb\.stop\(\)")
        self.assertNotRegex(src, r"self\.wait_window\(dlg\)\s*\n\s*reply\.put")

    def test_batch_is_approved_once(self):
        src = (ROOT / "fynex" / "ui" / "app.py").read_text(encoding="utf-8")
        self.assertIn("self._run(tasks, auto_approve=True)", src)
        self.assertIn("auto_approve=auto_approve", src)


if __name__ == "__main__":
    unittest.main()
