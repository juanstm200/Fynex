"""Pruebas de la parte pura de los widgets redondeados (sin abrir ventanas)."""
import struct
import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from girag_maint.ui.widgets import _hex, _png, corner_pixels


class CornerTests(unittest.TestCase):
    def test_corner_blends_bg_border_fill(self):
        r = 12
        px = corner_pixels(r, "#141f36", "#26314c", "#0c1526")
        self.assertEqual(px[0][0], _hex("#0c1526"))            # punta exterior = fondo (esquina cortada)
        self.assertEqual(px[r - 1][r - 1], _hex("#141f36"))    # interior = relleno
        self.assertEqual(px[0][r - 1], _hex("#26314c"))        # borde superior = color de borde
        self.assertEqual(px[r - 1][0], _hex("#26314c"))        # borde izquierdo
        self.assertEqual(len(px), r)
        self.assertTrue(all(len(row) == r for row in px))

    def test_no_border_variant_uses_fill_only(self):
        px = corner_pixels(10, "#e8823f", None, "#0c1526")
        self.assertEqual(px[9][9], _hex("#e8823f"))
        self.assertEqual(px[0][0], _hex("#0c1526"))

    def test_png_is_valid(self):
        data = _png(2, 2, [[(1, 2, 3), (4, 5, 6)], [(7, 8, 9), (10, 11, 12)]])
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        n = struct.unpack(">I", data[8:12])[0]
        self.assertEqual(data[12:16], b"IHDR")
        w, h = struct.unpack(">II", data[16:24])
        self.assertEqual((w, h), (2, 2))
        i = data.index(b"IDAT")
        ln = struct.unpack(">I", data[i - 4:i])[0]
        raw = zlib.decompress(data[i + 4:i + 4 + ln])
        self.assertEqual(len(raw), 2 * (1 + 2 * 3))


if __name__ == "__main__":
    unittest.main()
