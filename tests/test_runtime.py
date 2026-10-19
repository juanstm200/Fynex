"""Regresión del incidente de logs (16/10/2026): la limpieza borraba %TEMP%\\_MEIxxxx, el entorno del propio
.exe empaquetado, y luego fallaban DISM, Compact OS, bloatware, GLPI y diagnósticos."""
import json
import os
import queue
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from girag_maint.core import system
from girag_maint.core.context import TaskContext
from girag_maint.core.runtime_guard import warm_up
from girag_maint.tasks import cleanup
from girag_maint.tasks.registry_repair import definitely_missing, syswow_variant


def run_cleanup(targets):
    ctx = TaskContext(queue.Queue(), threading.Event())
    orig = cleanup.build_targets
    cleanup.build_targets = lambda: targets
    res = {}
    t = threading.Thread(target=lambda: res.setdefault("r", cleanup.CleanupTask().run(ctx)))
    t.start()
    logs = []
    while t.is_alive() or not ctx.events.empty():
        try:
            kind, *p = ctx.events.get(timeout=0.1)
        except queue.Empty:
            continue
        if kind == "confirm":
            p[3].put(True)
        elif kind == "log":
            logs.append(p[0])
    t.join()
    cleanup.build_targets = orig
    return res["r"], logs


def make_fake_temp(base: Path):
    temp = base / "Temp"
    mei = temp / "_MEI000021582"                     # entorno del .exe en ejecución
    (mei / "config").mkdir(parents=True)
    (mei / "base_library.zip").write_bytes(b"zip" * 1000)
    (mei / "config" / "bloatware.json").write_text("{}")
    (mei / "python312.dll").write_bytes(b"dll")
    other = temp / "_MEI000099999"                   # otra app empaquetada que también está abierta
    other.mkdir()
    (other / "base_library.zip").write_bytes(b"zip")
    (temp / "basura.tmp").write_bytes(b"x" * 500)
    (temp / "cache").mkdir()
    (temp / "cache" / "a.bin").write_bytes(b"y" * 300)
    return temp, mei, other


class CleanupDoesNotKillTheApp(unittest.TestCase):
    def test_runtime_dirs_survive_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            temp, mei, other = make_fake_temp(Path(d))
            with mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "_MEIPASS", str(mei), create=True):
                res, logs = run_cleanup([cleanup.Target("Usuario", "Temporales de soporte", temp)])
            self.assertTrue(res.ok, res)
            self.assertTrue((mei / "base_library.zip").exists())
            self.assertTrue((mei / "config" / "bloatware.json").exists())
            self.assertTrue((mei / "python312.dll").exists())
            self.assertTrue((other / "base_library.zip").exists())   # tampoco toca a otras apps empaquetadas
            self.assertFalse((temp / "basura.tmp").exists())          # lo demás sí se limpia
            self.assertFalse((temp / "cache" / "a.bin").exists())
            self.assertTrue(any("_MEI" in l and "omitieron" in l for l in logs))

    def test_target_equal_to_runtime_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            temp, mei, _ = make_fake_temp(Path(d))
            with mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "_MEIPASS", str(mei), create=True):
                skipped = []
                self.assertEqual(cleanup.collect(mei, skipped), ([], [], []))
                self.assertEqual(skipped, [str(mei)])

    def test_log_has_one_line_per_label(self):
        with tempfile.TemporaryDirectory() as d:
            targets = []
            for i in range(5):
                p = Path(d) / f"thumb{i}"
                p.mkdir()
                (p / "f.db").write_bytes(b"1" * 10)
                targets.append(cleanup.Target("Usuario", "Miniaturas de Ana", p))
            q = Path(d) / "otra"
            q.mkdir()
            (q / "g.tmp").write_bytes(b"2")
            targets.append(cleanup.Target("Usuario", "Temporales de Ana", q))
            res, logs = run_cleanup(targets)
            self.assertEqual(sum("Miniaturas de Ana" in l for l in logs), 2)   # 1 detección + 1 ✔ (antes eran 10)
            self.assertEqual(sum("Temporales de Ana" in l for l in logs), 2)


class RuntimeGuardTests(unittest.TestCase):
    def test_runtime_ok_detects_deleted_environment(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "_MEIPASS", d, create=True):
                ok, msg = system.runtime_ok()
                self.assertFalse(ok)
                self.assertIn("Cierra la aplicación", msg)
                (Path(d) / "base_library.zip").write_bytes(b"z")
                self.assertEqual(system.runtime_ok(), (True, ""))

    def test_runtime_ok_when_not_frozen(self):
        self.assertEqual(system.runtime_ok(), (True, ""))

    def test_warm_up_is_safe(self):
        warm_up()
        self.assertEqual("girag".encode("idna"), b"girag")


class ConfigLoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "bundle" / "config").mkdir(parents=True)
        (base / "data" / "config").mkdir(parents=True)
        (base / "bundle" / "config" / "x.json").write_text(json.dumps({"v": "incluida"}))
        self.p = [mock.patch.object(system, "resource_path", lambda rel: base / "bundle" / rel),
                  mock.patch.object(system, "data_dir", lambda: base / "data")]
        for p in self.p:
            p.start()
        system._CONFIG_CACHE.pop("x.json", None)
        self.base = base

    def tearDown(self):
        for p in self.p:
            p.stop()
        system._CONFIG_CACHE.pop("x.json", None)
        self.tmp.cleanup()

    def test_cached_copy_survives_file_deletion(self):
        self.assertEqual(system.load_json_config("x.json"), {"v": "incluida"})
        (self.base / "bundle" / "config" / "x.json").unlink()          # alguien limpia %TEMP%
        self.assertEqual(system.load_json_config("x.json"), {"v": "incluida"})

    def test_programdata_override_has_priority(self):
        (self.base / "data" / "config" / "x.json").write_text(json.dumps({"v": "personalizada"}))
        self.assertEqual(system.load_json_config("x.json"), {"v": "personalizada"})

    def test_missing_uses_default_or_raises(self):
        self.assertEqual(system.load_json_config("nope.json", {}), {})
        with self.assertRaises(FileNotFoundError):
            system.load_json_config("nope.json")


class RegistryWow64Tests(unittest.TestCase):
    def test_syswow_variant(self):
        self.assertEqual(syswow_variant(r"C:\Windows\system32\msvcr110.dll"), r"C:\Windows\SysWOW64\msvcr110.dll")
        self.assertEqual(syswow_variant(r"C:\Program Files\x\a.dll"), r"C:\Program Files\x\a.dll")

    def test_32bit_dll_in_syswow64_is_not_orphan(self):
        only_syswow = lambda p: "syswow64" in p.lower()
        dll = r"C:\Windows\system32\msvcr110.dll"        # caso real del log: SharedDLLs de WOW6432Node
        self.assertFalse(definitely_missing(dll, only_syswow, wow32=True))
        self.assertTrue(definitely_missing(dll, only_syswow, wow32=False))      # en vista de 64 bits sí faltaría
        self.assertTrue(definitely_missing(dll, lambda p: False, wow32=True))   # ausente en ambos: huérfana


if __name__ == "__main__":
    unittest.main()
