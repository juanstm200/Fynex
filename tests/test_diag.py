"""Pruebas de los diagnósticos (parseo y conceptos). Ejecutar: python -m unittest discover -s tests"""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from girag_maint.diag import battery, disk, sysinfo
from girag_maint.diag.common import as_list, extract_json


def ata_blob(attrs):
    b = bytearray(512)
    for i, (aid, value, raw) in enumerate(attrs):
        off = 2 + i * 12
        b[off] = aid
        b[off + 3] = value
        b[off + 4] = value
        b[off + 5:off + 11] = raw.to_bytes(6, "little")
    return bytes(b)


def nvme_blob(used=13, written_units=100000, read_units=80000, hours=4210, temp_k=311, crit=0, media=0):
    b = bytearray(512)
    b[0] = crit
    b[1:3] = struct.pack("<H", temp_k)
    b[3], b[4], b[5] = 100, 10, used
    b[32:48] = read_units.to_bytes(16, "little")
    b[48:64] = written_units.to_bytes(16, "little")
    b[112:128] = (321).to_bytes(16, "little")
    b[128:144] = hours.to_bytes(16, "little")
    b[160:176] = media.to_bytes(16, "little")
    return bytes(b)


class CommonTests(unittest.TestCase):
    def test_json(self):
        self.assertEqual(extract_json('ruido {"a": [1, 2]} fin'), {"a": [1, 2]})
        self.assertIsNone(extract_json("sin json"))
        self.assertEqual(as_list({"x": 1}), [{"x": 1}])
        self.assertEqual(as_list(None), [])


class DiskTests(unittest.TestCase):
    def test_nvme_parse(self):
        h = disk.parse_nvme_health(nvme_blob())
        self.assertEqual(h["used_pct"], 13)
        self.assertEqual(h["temp"], 311 - 273)
        self.assertEqual(h["written"], 100000 * 512000)
        self.assertEqual(h["hours"], 4210)

    def test_ata_parse_and_metrics(self):
        attrs = disk.parse_ata_smart(ata_blob([(9, 100, 12000), (177, 92, 30), (241, 99, 2_000_000_000),
                                                (242, 99, 1_000_000_000), (194, 60, 35), (5, 100, 0)]))
        m = disk.ata_metrics(attrs, "Samsung", True)
        self.assertEqual(m["life_pct"], 92.0)
        self.assertEqual(m["written"], 2_000_000_000 * 512)
        self.assertEqual(m["hours"], 12000)
        self.assertEqual(m["temp"], 35)
        self.assertNotIn("life_pct", disk.ata_metrics(attrs, "Seagate", False))  # HDD: sin vida %

    def test_manufacturer(self):
        self.assertEqual(disk.infer_manufacturer("SAMSUNG MZVL2512HCJQ", "NVMe"), "Samsung")
        self.assertEqual(disk.infer_manufacturer("CT500MX500SSD1", "ATA"), "Crucial (Micron)")
        self.assertEqual(disk.infer_manufacturer("ST1000LM035-1RK172", ""), "Seagate")
        self.assertEqual(disk.infer_manufacturer("WDC PC SN730", None), "Western Digital")
        self.assertEqual(disk.infer_manufacturer("XYZ-123", "ATA"), "No reportado")
        self.assertEqual(disk.infer_manufacturer("lo que sea", "Kingston"), "Kingston")

    def test_classify_levels(self):
        base = {"is_ssd": True}
        for life, expected in ((95, 0), (70, 0), (55, 1), (30, 2), (10, 3)):
            self.assertEqual(disk.classify_disk({**base, "life_pct": life})[0], expected)

    def test_classify_flags_raise_level(self):
        self.assertEqual(disk.classify_disk({"is_ssd": True, "life_pct": 95, "critical_warning": 1})[0], 3)
        self.assertEqual(disk.classify_disk({"is_ssd": True, "life_pct": 95, "health": "Warning"})[0], 2)
        self.assertEqual(disk.classify_disk({"is_ssd": False, "hours": 5000, "pending": 3})[0], 2)
        self.assertEqual(disk.classify_disk({"is_ssd": True, "life_pct": 95, "media_errors": 2})[0], 1)

    def test_classify_estimates(self):
        self.assertEqual(disk.classify_disk({"is_ssd": True, "written": 100e12})[0], 0)
        self.assertEqual(disk.classify_disk({"is_ssd": True, "written": 700e12})[0], 3)
        self.assertEqual(disk.classify_disk({"is_ssd": False, "hours": 50000})[0], 2)
        self.assertIsNone(disk.classify_disk({"is_ssd": True})[0])

    def test_full_report_nvme_and_sata(self):
        raw = {"Disks": [
            {"Id": 0, "Model": "SAMSUNG MZVL2512HCJQ", "Manufacturer": "NVMe", "Serial": "S123_4567.", "Firmware": "GXA7",
             "Media": "SSD", "Bus": "NVMe", "Size": 512110190592, "Health": "Healthy", "Wear": 13, "Temp": 38, "Hours": 4000},
            {"Id": 1, "Model": "ST1000LM035-1RK172", "Manufacturer": "ATA", "Serial": "ZZ", "Firmware": "SDM1",
             "Media": "HDD", "Bus": "SATA", "Size": 1000204886016, "Health": "Healthy"},
            {"Id": 2, "Model": "USB DISK", "Manufacturer": "", "Media": "Unspecified", "Bus": "USB", "Size": 16e9, "Health": "Healthy"}],
            "Pnp": [{"Index": 1, "Pnp": "SCSI\\Disk&Ven_ATA&Prod_ST1000LM035"}],
            "Smart": [{"Instance": "SCSI\\Disk&Ven_ATA&Prod_ST1000LM035_0",
                       "Data": ",".join(str(x) for x in ata_blob([(9, 90, 51000), (5, 100, 4)]))}]}
        lines = disk.report(raw, nvme_reader=lambda i: disk.parse_nvme_health(nvme_blob()))
        text = "\n".join(t for t, _ in lines)
        self.assertIn("FUNCIONAL — vida útil restante 87 %", text)
        self.assertIn("NO FUNCIONAL", text)           # HDD 51000 h
        self.assertIn("SIN DATOS SUFICIENTES", text)  # USB
        self.assertIn("Seagate", text)

    def test_nvme_reader_failure_falls_back_to_counters(self):
        raw = {"Disks": [{"Id": 0, "Model": "WDC PC SN730", "Manufacturer": "", "Media": "SSD", "Bus": "NVMe",
                          "Size": 5e11, "Health": "Healthy", "Wear": 40, "Hours": 100}]}
        text = "\n".join(t for t, _ in disk.report(raw, nvme_reader=lambda i: None))
        self.assertIn("MEDIO FUNCIONAL", text)
        self.assertIn("Contadores de Windows", text)


class BatteryTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(battery.classify_battery(92, 120)[0], 0)
        self.assertEqual(battery.classify_battery(70, 200)[0], 1)
        self.assertEqual(battery.classify_battery(50, 200)[0], 2)
        self.assertEqual(battery.classify_battery(30, 200)[0], 3)
        self.assertEqual(battery.classify_battery(90, 1500)[0], 1)   # muchos ciclos, capacidad alta: tope "medio"
        self.assertEqual(battery.classify_battery(65, 1500)[0], 2)
        self.assertEqual(battery.classify_battery(None, 1100)[0], 3)
        self.assertIsNone(battery.classify_battery(None, None)[0])

    def test_powercfg_xml(self):
        xml = (b'<?xml version="1.0"?><BatteryReport xmlns="http://schemas.microsoft.com/battery/2012">'
               b'<Batteries><Battery><Id>DELL ABC</Id><Manufacturer>SMP</Manufacturer><SerialNumber>1234</SerialNumber>'
               b'<Chemistry>LION</Chemistry><DesignCapacity>51000</DesignCapacity><FullChargeCapacity>42000</FullChargeCapacity>'
               b'<CycleCount>250</CycleCount></Battery></Batteries></BatteryReport>')
        b = battery.parse_powercfg_xml(xml)
        self.assertEqual(b[0]["Design"], 51000)
        self.assertEqual(b[0]["Cycles"], 250)

    def test_report_merges_powercfg_for_cycles(self):
        raw = {"Win32": [{"Name": "Battery", "Chemistry": 6, "Charge": 80, "Status": 1}],
               "Static": [{"Manufacturer": "SMP", "Serial": "ABC", "Name": "DELL", "Design": 51000, "Chem": "LION"}],
               "Full": [42000], "Cycles": [0]}
        text = "\n".join(t for t, _ in battery.report(raw, [{"Cycles": 310}]))
        self.assertIn("82 %", text)
        self.assertIn("310", text)
        self.assertIn("FUNCIONAL", text)
        self.assertIn("SMP", text)

    def test_no_battery(self):
        text = "\n".join(t for t, _ in battery.report({"Win32": [], "Static": []}))
        self.assertIn("no tiene batería", text)


class SysinfoTests(unittest.TestCase):
    def test_helpers(self):
        self.assertIn("OEM", sysinfo.license_channel("OEM:DM", ""))
        self.assertIn("KMS", sysinfo.license_channel("Volume:GVLK", ""))
        self.assertEqual(sysinfo.encryption_state({"Protection": "On", "Volume": "FullyEncrypted"})[0], "Cifrado (BitLocker activo)")
        self.assertEqual(sysinfo.encryption_state({"Protection": "Off", "Volume": "FullyDecrypted"})[0], "No cifrado")
        self.assertIn("Cifrando", sysinfo.encryption_state(
            {"Protection": "Off", "Volume": "EncryptionInProgress", "Percent": 40})[0])
        self.assertEqual(sysinfo.encryption_state({"Protection": "1", "Volume": ""})[1], "ok")  # CIM numérico
        self.assertEqual(sysinfo.tpm_summary({"TpmSpec": "2.0, 0, 1.38", "TpmMaker": "INTC", "TpmPresent": True,
                                              "TpmReady": True})[1], "ok")
        self.assertEqual(sysinfo.tpm_summary({})[1], "err")
        txt = sysinfo.format_ram(17000000000, [{"Cap": 8 * 2**30, "Speed": 3200, "Type": 26},
                                                 {"Cap": 8 * 2**30, "Speed": 3200, "Type": 26}])
        self.assertIn("16 GB", txt)
        self.assertIn("2 × 8 GB DDR4 @ 3200 MHz", txt)

    def test_report(self):
        d = {"Host": "BOG-PC01", "Domain": "girag.local", "PartOfDomain": True, "Maker": "Dell Inc.", "Model": "Latitude 5440",
             "Serial": "ABC123", "OsCaption": "Microsoft Windows 11 Pro", "OsVersion": "10.0.26100", "OsBuild": "26100",
             "OsArch": "64-bit", "DisplayVersion": "24H2", "Ubr": 2033,
             "Cpu": [{"Name": "13th Gen Intel(R) Core(TM) i5-1335U", "Cores": 10, "Threads": 12}],
             "RamTotal": 16e9, "RamModules": [], "LicChannel": "OEM:DM", "LicStatus": 1, "LicPartial": "ABCDE", "Oa3": True,
             "BiosMaker": "Dell Inc.", "BiosVersion": "1.12.0", "BiosDate": "20240115000000.000000+000", "SecureBoot": True,
             "Volumes": [{"Mount": "C:", "Protection": "On", "Volume": "FullyEncrypted", "Percent": 100, "Method": "XtsAes128"}],
             "TpmSpec": "2.0, 0, 1.59", "TpmMaker": "INTC", "TpmPresent": True, "TpmReady": True}
        text = "\n".join(t for t, _ in sysinfo.report(d))
        for needle in ("BOG-PC01", "Windows 11 Pro", "24H2", "i5-1335U", "OEM", "1.12.0", "2024-01-15", "C:", "TPM 2.0"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
