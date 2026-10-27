"""Pruebas de la lógica que no depende de Windows. Ejecutar: python -m unittest discover -s tests"""
import os
import queue
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fynex.core.context import TaskContext
from fynex.core.process import LineSplitter, decode_output, parse_percent
from fynex.tasks import bloatware, cleanup
from fynex.tasks.dism_repair import interpret_sfc
from fynex.tasks.registry_repair import definitely_missing, extract_exe_path


def make_ctx(dry=False):
    return TaskContext(queue.Queue(), threading.Event(), dry_run=dry)


class ProcessTests(unittest.TestCase):
    def test_percent(self):
        self.assertEqual(parse_percent("[=====    62.3%    ]"), 62.3)
        self.assertEqual(parse_percent("Verification 45% complete."), 45.0)
        self.assertEqual(parse_percent("Verificación 7,5% completada"), 7.5)
        self.assertIsNone(parse_percent("Deployment Image Servicing"))
        self.assertIsNone(parse_percent("150%"))

    def test_splitter_cr_lf(self):
        s = LineSplitter()
        toks = s.feed(b"a\r[==10%==]\r[==20%==]\nfin") + s.flush()
        self.assertEqual(toks, [b"a", b"[==10%==]", b"[==20%==]", b"fin"])

    def test_decode_utf16_nulls(self):
        self.assertEqual(decode_output("Verification 5%".encode("utf-16le")), "Verification 5%")

    def test_sfc(self):
        self.assertEqual(interpret_sfc("Windows Resource Protection did not find any integrity violations."), "ok")
        self.assertEqual(interpret_sfc("Windows Resource Protection found corrupt files and successfully repaired them"), "fixed")
        self.assertEqual(interpret_sfc("found corrupt files but was unable to fix some of them"), "bad")


class RegistryHelpers(unittest.TestCase):
    def test_extract(self):
        self.assertEqual(extract_exe_path('"C:\\A B\\u.exe" /S'), "C:\\A B\\u.exe")
        self.assertEqual(extract_exe_path("C:\\x\\unins000.exe /silent"), "C:\\x\\unins000.exe")
        self.assertIsNone(extract_exe_path(""))

    def test_missing_is_conservative(self):
        gone = lambda p: False
        self.assertTrue(definitely_missing("C:\\Nope\\a.exe", gone))
        self.assertTrue(definitely_missing("C:\\Nope\\a.exe,0", gone))
        self.assertFalse(definitely_missing("msiexec.exe", gone))          # ruta relativa
        self.assertFalse(definitely_missing("%UNKNOWNVAR%\\a.exe", gone))   # variable sin resolver
        self.assertFalse(definitely_missing("C:\\Real\\a.exe", lambda p: True))
        self.assertFalse(definitely_missing(None, gone))


class BloatwareTests(unittest.TestCase):
    CFG = {"remove": ["Microsoft.BingNews", "*CandyCrush*", "Microsoft.Windows.*"],
           "protect": ["Microsoft.Windows.*", "Microsoft.WindowsStore"]}

    def test_plan(self):
        data = {"packages": [
            {"Name": "Microsoft.BingNews", "PackageFullName": "BN_1", "NonRemovable": False, "IsFramework": False},
            {"Name": "king.com.CandyCrushSaga", "PackageFullName": "CC_1", "NonRemovable": False, "IsFramework": False},
            {"Name": "Microsoft.WindowsStore", "PackageFullName": "ST_1", "NonRemovable": False, "IsFramework": False},
            {"Name": "Microsoft.Windows.Photos", "PackageFullName": "PH_1", "NonRemovable": False, "IsFramework": False},
            {"Name": "Microsoft.BingNews", "PackageFullName": "BN_2", "NonRemovable": True, "IsFramework": False},
        ], "provisioned": {"DisplayName": "Microsoft.BingNews", "PackageName": "BN_PROV"}}  # objeto único
        plan = bloatware.build_plan(data, self.CFG)
        self.assertEqual(set(plan), {"Microsoft.BingNews", "king.com.CandyCrushSaga"})
        self.assertEqual(plan["Microsoft.BingNews"]["full"], ["BN_1"])
        self.assertEqual(plan["Microsoft.BingNews"]["prov"], ["BN_PROV"])

    def test_shipped_config_protects_essentials(self):
        cfg = bloatware.load_config()
        for essential in ("Microsoft.WindowsStore", "Microsoft.WindowsCalculator", "MicrosoftCorporationII.QuickAssist"):
            self.assertTrue(bloatware.matches(essential, cfg["protect"]), essential)


class CleanupTests(unittest.TestCase):
    def test_collect_and_delete(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "cache"
            (root / "sub" / "deep").mkdir(parents=True)
            (root / "a.bin").write_bytes(b"x" * 100)
            (root / "sub" / "b.bin").write_bytes(b"y" * 50)
            (root / "sub" / "deep" / "c.bin").write_bytes(b"z" * 10)
            files, dirs, links = cleanup.collect(root)
            self.assertEqual(sum(s for _, s in files), 160)
            self.assertEqual(len(files), 3)
            self.assertEqual(len(dirs), 2)
            for f, _ in files:
                self.assertTrue(cleanup.delete_file(f))
            for dd in sorted(dirs, key=len, reverse=True):
                os.rmdir(dd)
            self.assertEqual(list(root.iterdir()), [])
            self.assertTrue(cleanup.delete_file(str(root / "missing")))

    @unittest.skipIf(os.name == "nt", "enlaces simbólicos requieren privilegios en Windows")
    def test_symlink_not_followed(self):
        with tempfile.TemporaryDirectory() as d:
            outside = Path(d) / "outside"
            outside.mkdir()
            (outside / "keep.txt").write_text("keep")
            root = Path(d) / "cache"
            root.mkdir()
            os.symlink(outside, root / "link")
            files, dirs, links = cleanup.collect(root)
            self.assertEqual(files, [])
            self.assertEqual(len(links), 1)


class CleanupFlowTest(unittest.TestCase):
    """Flujo completo de la tarea con confirmación automática, sobre una carpeta temporal."""

    def _run(self, dry):
        import queue as q
        from fynex.tasks.cleanup import CleanupTask, Target
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "tmp"
            (root / "x").mkdir(parents=True)
            for i in range(60):
                (root / "x" / f"f{i}.tmp").write_bytes(b"0" * 1000)
            orig = cleanup.build_targets
            cleanup.build_targets = lambda: [Target("Windows", "Prueba", root)]
            ctx = make_ctx(dry)
            res = {}
            t = threading.Thread(target=lambda: res.setdefault("r", CleanupTask().run(ctx)))
            t.start()
            progress = []
            while t.is_alive() or not ctx.events.empty():
                try:
                    kind, *p = ctx.events.get(timeout=0.1)
                except q.Empty:
                    continue
                if kind == "confirm":
                    p[3].put(True)
                elif kind == "progress":
                    progress.append(p[0])
            t.join()
            cleanup.build_targets = orig
            return res["r"], progress, list(root.rglob("*.tmp"))

    def test_real_run_deletes_and_reports_progress(self):
        r, progress, left = self._run(False)
        self.assertTrue(r.ok)
        self.assertEqual(left, [])
        self.assertEqual(progress[-1], 100.0)
        self.assertIn("60 archivos", r.summary)

    def test_dry_run_keeps_files(self):
        r, _, left = self._run(True)
        self.assertTrue(r.ok)
        self.assertEqual(len(left), 60)
        self.assertIn("Simulación", r.summary)


class ProtectedAppsTests(unittest.TestCase):
    """Decisión de Soporte: nunca eliminar Calculadora, Fotos, multimedia/video ni Office."""

    def test_never_removed(self):
        cfg = bloatware.load_config()
        names = ["Microsoft.WindowsCalculator", "Microsoft.Windows.Photos", "Microsoft.ZuneMusic",
                 "Microsoft.ZuneVideo", "Microsoft.MicrosoftOfficeHub", "Microsoft.Office.Sway",
                 "Microsoft.Office.OneNote", "Clipchamp.Clipchamp", "Microsoft.MPEG2VideoExtension",
                 "Microsoft.HEVCVideoExtension", "Microsoft.Windows.MediaPlayer"]
        data = {"packages": [{"Name": n, "PackageFullName": n + "_1", "NonRemovable": False,
                              "IsFramework": False} for n in names]}
        self.assertEqual(bloatware.build_plan(data, cfg), {})

    def test_real_bloat_still_removed(self):
        cfg = bloatware.load_config()
        data = {"packages": [{"Name": n, "PackageFullName": n + "_1", "NonRemovable": False, "IsFramework": False}
                             for n in ("Microsoft.BingNews", "king.com.CandyCrushSaga", "Microsoft.MicrosoftSolitaireCollection")]}
        self.assertEqual(len(bloatware.build_plan(data, cfg)), 3)


class GlpiSyncTests(unittest.TestCase):
    def test_progress_monotonic_and_capped(self):
        from fynex.tasks.glpi_sync import SyncProgress
        m, last = SyncProgress(), 0
        for line in ["[info] target server0: server https://x", "[info] running task Inventory",
                     "[info] collecting", "[info] sending inventory", "[info] inventory accepted"] + ["x"] * 200:
            v = m.feed(line)
            self.assertGreaterEqual(v, last)
            self.assertLess(v, 100)
            last = v
        self.assertGreaterEqual(last, 95)

    def test_analyze(self):
        from fynex.tasks.glpi_sync import analyze
        self.assertTrue(analyze(["[info] ok"], 0)[0])
        self.assertFalse(analyze(["[error] Can't connect to host"], 0)[0])
        self.assertFalse(analyze(["[info] ok"], 2)[0])

    def test_command_and_config(self):
        from fynex.tasks.glpi_sync import build_command, load_config
        self.assertEqual(load_config()["server_url"], "https://soporte.girag.com/plugins/glpiinventory")
        self.assertEqual(build_command("a.bat", "https://s")[1:], ["--force", "--tasks=inventory", "--server=https://s"])

    @unittest.skipIf(os.name == "nt", "usa un script sh como agente falso")
    def test_full_flow_with_fake_agent(self):
        import queue as q
        from fynex.tasks import glpi_sync
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "agent.sh"
            fake.write_text("#!/bin/sh\necho '[info] target server0'\necho '[info] running task Inventory'\n"
                            "echo '[info] sending inventory'\necho '[info] inventory accepted'\n")
            fake.chmod(0o755)
            orig = (glpi_sync.find_agent, glpi_sync.check_server)
            glpi_sync.find_agent = lambda cfg: str(fake)
            glpi_sync.check_server = lambda url, timeout=12: (True, "HTTP 200")
            try:
                ctx = make_ctx()
                res = glpi_sync.GlpiSyncTask().run(ctx)
            finally:
                glpi_sync.find_agent, glpi_sync.check_server = orig
            self.assertTrue(res.ok, res)
            prog = []
            while not ctx.events.empty():
                k, *p = ctx.events.get()
                if k == "progress":
                    prog.append(p[0])
            self.assertEqual(prog[-1], 100.0)

    def test_missing_agent_and_unreachable_server(self):
        from fynex.tasks import glpi_sync
        orig = (glpi_sync.find_agent, glpi_sync.check_server)
        try:
            glpi_sync.find_agent = lambda cfg: None
            self.assertFalse(glpi_sync.GlpiSyncTask().run(make_ctx()).ok)
            glpi_sync.find_agent = lambda cfg: "x"
            glpi_sync.check_server = lambda url, timeout=12: (False, "timeout")
            self.assertFalse(glpi_sync.GlpiSyncTask().run(make_ctx()).ok)
        finally:
            glpi_sync.find_agent, glpi_sync.check_server = orig


EN_TABLE = (
    "\r   - \r   \\ \r"
    "Name                      Id                       Version      Available    Source\r\n"
    "-------------------------------------------------------------------------------------\r\n"
    "7-Zip 23.01 (x64)         7zip.7zip                23.01        24.08        winget\r\n"
    "Google Chrome             Google.Chrome            129.0.1      130.0.2      winget\r\n"
    "Aplicación muy larga…     Vendor.Very.Long.Pack…   1.0          2.0          winget\r\n"
    "3 upgrades available.\r\n"
)
ES_TABLE = (
    "Nombre              Id                  Versión   Disponible  Origen\n"
    "-----------------------------------------------------------------\n"
    "Mozilla Firefox     Mozilla.Firefox     130.0     131.0       winget\n"
    "2 actualizaciones disponibles.\n"
)


class WingetParseTests(unittest.TestCase):
    def test_english_table(self):
        from fynex.tasks.winget_upgrade import parse_upgrade_table
        items = parse_upgrade_table(EN_TABLE, approved={"Google.Chrome"})
        self.assertEqual([i["id"] for i in items], ["7zip.7zip", "Google.Chrome", "Vendor.Very.Long.Pack…"])
        self.assertEqual(items[0]["detail"], "23.01 → 24.08")
        self.assertEqual(items[0]["source"], "winget")
        self.assertTrue(items[1]["approved"] and not items[0]["approved"])
        self.assertTrue(items[2]["disabled"])          # ID truncado: no se puede actualizar por ID

    def test_spanish_table_and_footer(self):
        from fynex.tasks.winget_upgrade import parse_upgrade_table
        items = parse_upgrade_table(ES_TABLE)
        self.assertEqual([i["id"] for i in items], ["Mozilla.Firefox"])

    def test_no_table(self):
        from fynex.tasks.winget_upgrade import parse_upgrade_table
        self.assertEqual(parse_upgrade_table("No installed package found matching input criteria."), [])

    def test_command(self):
        from fynex.tasks.winget_upgrade import upgrade_command
        cmd = upgrade_command("winget", {"id": "7zip.7zip", "source": "winget"})
        for flag in ("--id", "7zip.7zip", "--exact", "--silent", "--source"):
            self.assertIn(flag, cmd)


class WingetFlowTest(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "usa un script sh como winget falso")
    def test_selected_apps_only_and_failures_reported(self):
        import queue as q
        from fynex.tasks import winget_upgrade as wu
        items = [{"id": "good.app", "name": "Good", "detail": "1 → 2", "source": "winget", "approved": True, "disabled": False},
                 {"id": "bad.app", "name": "Bad", "detail": "1 → 2", "source": "winget", "approved": False, "disabled": False},
                 {"id": "skip.app", "name": "Skip", "detail": "1 → 2", "source": "winget", "approved": False, "disabled": False}]
        with tempfile.TemporaryDirectory() as d:
            calls = Path(d) / "calls.txt"
            fake = Path(d) / "winget.sh"
            fake.write_text("#!/bin/sh\necho \"$*\" >> %s\necho '  ####  50%%'\necho '  ####  100%%'\n"
                            "case \"$*\" in *bad.app*) exit 1;; esac\nexit 0\n" % calls)
            fake.chmod(0o755)
            saved = {}
            orig = (wu.find_winget, wu.list_upgrades, wu.load_approved, wu.save_approved)
            wu.find_winget = lambda: str(fake)
            wu.list_upgrades = lambda w, a=(): items
            wu.load_approved = lambda: {"good.app"}
            wu.save_approved = lambda ids: saved.setdefault("ids", set(ids))
            try:
                ctx = make_ctx()
                res = {}
                t = threading.Thread(target=lambda: res.setdefault("r", wu.WingetUpgradeTask().run(ctx)))
                t.start()
                while t.is_alive() or not ctx.events.empty():
                    try:
                        kind, *p = ctx.events.get(timeout=0.1)
                    except q.Empty:
                        continue
                    if kind == "choose":
                        p[5].put((["good.app", "bad.app"], True))   # el técnico marca 2 de 3 y guarda
                t.join()
            finally:
                wu.find_winget, wu.list_upgrades, wu.load_approved, wu.save_approved = orig
            r = res["r"]
            self.assertFalse(r.ok)
            self.assertIn("1 de 2", r.summary)
            self.assertIn("Bad", r.details)
            run_calls = calls.read_text()
            self.assertIn("good.app", run_calls)
            self.assertNotIn("skip.app", run_calls)           # no seleccionada: no se toca
            self.assertEqual(saved["ids"], {"good.app", "bad.app"})


class BatchRunnerTests(unittest.TestCase):
    def _tasks(self):
        from fynex.core.context import TaskResult
        from fynex.tasks.base import Task
        order = []

        def make(tid, ok=True, boom=False):
            class T(Task):
                id = tid
                title = tid

                def run(self, ctx):
                    order.append(tid)
                    if boom:
                        raise RuntimeError("fallo")
                    return TaskResult(ok, tid)
            return T()
        return order, make

    def test_runs_in_given_order_and_continues_after_failure(self):
        from fynex.core.runner import run_tasks
        order, make = self._tasks()
        events = queue.Queue()
        results = run_tasks([make("a"), make("b", boom=True), make("c", ok=False), make("d")],
                            events, threading.Event())
        self.assertEqual(order, ["a", "b", "c", "d"])
        self.assertEqual([r.ok for _, r in results], [True, False, False, True])
        kinds = []
        while not events.empty():
            kinds.append(events.get()[0])
        self.assertEqual(kinds.count("begin"), 4)
        self.assertEqual(kinds[-1], "done")

    def test_cancel_stops_remaining(self):
        from fynex.core.runner import run_tasks
        order, make = self._tasks()
        cancel = threading.Event()
        first = make("a")
        orig = first.run
        first.run = lambda ctx: (cancel.set(), orig(ctx))[1]
        results = run_tasks([first, make("b"), make("c")], queue.Queue(), cancel)
        self.assertEqual(order, ["a"])
        self.assertEqual(len(results), 3)
        self.assertIn("No se ejecutó", results[1][1].summary)


if __name__ == "__main__":
    unittest.main()
