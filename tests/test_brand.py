"""Marca Fynex: recursos gráficos, migración de la carpeta de datos y barra de título propia."""
import os
import struct
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fynex
from fynex.core import system
from fynex.ui import assets, chrome

ROOT = Path(__file__).resolve().parents[1]


class AssetsTests(unittest.TestCase):
    def test_marks_exist_with_the_size_in_their_name(self):
        for s in assets.MARK_SIZES:
            data = (ROOT / "assets" / f"fynex_mark_{s}.png").read_bytes()
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", data[16:24]), (s, s))

    def test_ico_has_all_sizes(self):
        data = (ROOT / "assets" / "fynex.ico").read_bytes()
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind), (0, 1))
        sizes = {(data[6 + 16 * i] or 256) for i in range(count)}
        self.assertEqual(sizes, {16, 24, 32, 48, 64, 128, 256})

    def test_pick_mark_size_follows_screen_scale(self):
        self.assertEqual(assets.pick_mark_size(40, 1.0), 36)
        self.assertEqual(assets.pick_mark_size(40, 1.5), 54)
        self.assertEqual(assets.pick_mark_size(40, 2.0), 72)
        self.assertEqual(assets.pick_mark_size(16, 1.0), 16)
        self.assertEqual(assets.pick_mark_size(16, 1.5), 24)


class BrandTests(unittest.TestCase):
    def test_app_name(self):
        self.assertTrue(fynex.APP_NAME.startswith("Fynex"))
        self.assertNotIn("Girag", fynex.APP_NAME)

    def test_ui_code_has_no_old_brand(self):
        for f in (ROOT / "fynex" / "ui").glob("*.py"):
            self.assertNotRegex(f.read_text(encoding="utf-8"), r"GIRAG|Girag(?!_GLPI)", f.name)


class DataDirMigrationTests(unittest.TestCase):
    def test_legacy_folder_is_renamed_keeping_files(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.object(Path, "home", return_value=Path(home)), \
             mock.patch.object(system, "IS_WINDOWS", False):
            legacy = Path(home) / ".girag_maintenance" / "backups"
            legacy.mkdir(parents=True)
            (legacy / "registro.reg").write_text("x")
            new = system.data_dir()
            self.assertEqual(new, Path(home) / ".fynex")
            self.assertTrue((new / "backups" / "registro.reg").exists())
            self.assertFalse((Path(home) / ".girag_maintenance").exists())

    def test_falls_back_to_legacy_if_rename_fails(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.object(Path, "home", return_value=Path(home)), \
             mock.patch.object(system, "IS_WINDOWS", False):
            (Path(home) / ".girag_maintenance").mkdir()
            with mock.patch.object(Path, "rename", side_effect=PermissionError):
                self.assertEqual(system.data_dir(), Path(home) / ".girag_maintenance")

    def test_fresh_install_uses_new_folder(self):
        with tempfile.TemporaryDirectory() as home, mock.patch.object(Path, "home", return_value=Path(home)), \
             mock.patch.object(system, "IS_WINDOWS", False):
            self.assertEqual(system.data_dir(), Path(home) / ".fynex")


class CustomBarSwitchTests(unittest.TestCase):
    def test_windows_uses_custom_bar_by_default(self):
        self.assertTrue(chrome.wants_custom_bar([], {}, True))

    def test_native_titlebar_escape_hatches(self):
        self.assertFalse(chrome.wants_custom_bar(["--native-titlebar"], {}, True))
        self.assertFalse(chrome.wants_custom_bar([], {"FYNEX_NATIVE_TITLEBAR": "1"}, True))

    def test_other_systems_use_native_unless_forced_for_tests(self):
        self.assertFalse(chrome.wants_custom_bar([], {}, False))
        self.assertTrue(chrome.wants_custom_bar([], {"FYNEX_CUSTOM_BAR": "1"}, False))


def has_display():
    try:
        r = tk.Tk()
        r.destroy()
        return True
    except tk.TclError:
        return False


@unittest.skipUnless(has_display(), "requiere pantalla (xvfb-run)")
class WindowSmokeTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"FYNEX_CUSTOM_BAR": "1"})
        self.env.start()
        from fynex.ui.app import App
        self.app = App()
        self.app.update()

    def tearDown(self):
        self.app.destroy()
        self.env.stop()

    def test_titlebar_header_and_logo(self):
        self.assertIsNotNone(self.app.titlebar)
        self.assertIsNotNone(self.app._logo)
        self.assertEqual(self.app.title(), fynex.APP_NAME)

    def test_maximize_and_restore(self):
        c, a = self.app.chrome, self.app
        before = a.geometry()
        c.toggle_maximize()
        a.update()
        self.assertTrue(c.maxed)
        self.assertEqual(a.titlebar.btn_max.lbl.cget("text"), a.titlebar.glyphs[2])
        c.toggle_maximize()
        a.update()
        self.assertFalse(c.maxed)
        self.assertEqual(a.titlebar.btn_max.lbl.cget("text"), a.titlebar.glyphs[1])
        self.assertEqual(a.geometry().split("+")[0], before.split("+")[0])

    def test_double_click_toggles_and_close_button_calls_handler(self):
        c = self.app.chrome
        ev = mock.Mock(x_root=10, y_root=10, x=1, y=1)
        c.start_move(ev)             # primer clic: prepara arrastre
        c.start_move(ev)             # segundo clic rápido: maximiza
        self.assertTrue(c.maxed)
        closed = []
        c.on_close = lambda: closed.append(1)
        self.app.titlebar.btn_close.command()
        self.assertEqual(closed, [1])


if __name__ == "__main__":
    unittest.main()
