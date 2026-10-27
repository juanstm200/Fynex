"""Estado del disco estilo CrystalDiskInfo/HWiNFO: vida útil, datos escritos/leídos y concepto."""
import struct

from ..core import system
from .common import as_list, fmt_int, kv, run_ps_json

PS_DISKS = r'''
$disks=@()
foreach($d in @(Get-PhysicalDisk)){
  $rc = $d | Get-StorageReliabilityCounter
  $disks += [pscustomobject]@{
    Id=[int]$d.DeviceId; Model=[string]$d.FriendlyName; Manufacturer=[string]$d.Manufacturer
    Serial=[string]$d.SerialNumber; Firmware=[string]$d.FirmwareVersion; Media=[string]$d.MediaType
    Bus=[string]$d.BusType; Size=[int64]$d.Size; Health=[string]$d.HealthStatus
    Wear=$rc.Wear; Temp=$rc.Temperature; Hours=$rc.PowerOnHours
    ReadErr=$rc.ReadErrorsTotal; WriteErr=$rc.WriteErrorsTotal
  }
}
$pnp=@(Get-CimInstance Win32_DiskDrive | ForEach-Object { [pscustomobject]@{Index=[int]$_.Index;Pnp=[string]$_.PNPDeviceID} })
$smart=@(Get-CimInstance -Namespace root\wmi -ClassName MSStorageDriver_ATAPISmartData | ForEach-Object { [pscustomobject]@{Instance=[string]$_.InstanceName;Data=([string]::Join(',',$_.VendorSpecific))} })
ConvertTo-Json -InputObject ([ordered]@{Disks=$disks;Pnp=$pnp;Smart=$smart}) -Depth 4 -Compress
'''

STATES = ("FUNCIONAL", "MEDIO FUNCIONAL", "NO FUNCIONAL", "CRÍTICO · CAMBIAR")
LEVELS = ("ok", "warn", "err", "err")
ADVICE = ("Sin acciones; revisar de nuevo en el próximo preventivo.",
          "Sigue operativo. Respaldar la información y proyectar reemplazo a mediano plazo.",
          "Desgaste avanzado o señales de falla. Respaldar y programar el cambio pronto.",
          "Riesgo alto de pérdida de datos. Respaldar de inmediato y reemplazar el disco.")

_GENERIC = {"", "ata", "nvme", "scsi", "usb", "unknown", "generic", "(standard disk drives)", "msft"}
_VENDOR_TOKENS = [
    ("samsung", "Samsung"), ("western digital", "Western Digital"), ("wdc", "Western Digital"),
    ("sandisk", "SanDisk"), ("kingston", "Kingston"), ("crucial", "Crucial (Micron)"),
    ("micron", "Micron"), ("intel", "Intel"), ("kioxia", "KIOXIA"), ("toshiba", "Toshiba"),
    ("hynix", "SK hynix"), ("seagate", "Seagate"), ("lexar", "Lexar"), ("netac", "Netac"),
    ("adata", "ADATA"), ("patriot", "Patriot"), ("lite-on", "Lite-On"), ("liteon", "Lite-On"),
    ("transcend", "Transcend"), ("teamgroup", "TeamGroup"), ("sabrent", "Sabrent"),
    ("gigabyte", "Gigabyte"), ("corsair", "Corsair"), ("pny", "PNY"), ("hitachi", "Hitachi"),
    ("hgst", "HGST"), ("fanxiang", "Fanxiang"), ("hp ssd", "HP"), ("hfs", "SK hynix"),
]
_VENDOR_PREFIX = [("ct", "Crucial (Micron)"), ("st", "Seagate"), ("wd", "Western Digital"),
                  ("mz", "Samsung"), ("sa4", "Kingston"), ("ssdpe", "Intel"), ("ssdsc", "Intel")]


def infer_manufacturer(model: str, reported=None) -> str:
    rep = (reported or "").strip()
    if rep.lower() not in _GENERIC:
        return rep
    m = (model or "").strip().lower()
    for tok, name in _VENDOR_TOKENS:
        if tok in m:
            return name
    for pre, name in _VENDOR_PREFIX:
        if m.startswith(pre) and len(m) > len(pre) and m[len(pre)].isdigit():
            return name
    return "No reportado"


# ---------- SMART ATA (SATA) ----------
def parse_ata_smart(raw: bytes) -> dict:
    """VendorSpecific de 512 bytes: 2 de revisión + 30 atributos de 12 bytes."""
    attrs = {}
    for i in range(30):
        off = 2 + i * 12
        chunk = raw[off:off + 12]
        if len(chunk) < 12 or chunk[0] == 0:
            continue
        attrs[chunk[0]] = {"value": chunk[3], "worst": chunk[4], "raw": int.from_bytes(chunk[5:11], "little")}
    return attrs


def ata_metrics(attrs: dict, manufacturer: str, is_ssd: bool) -> dict:
    m = {}
    if is_ssd:
        for aid in (231, 233, 177, 202, 169):  # valor normalizado = % de vida restante
            a = attrs.get(aid)
            if a and 0 < a["value"] <= 100:
                m["life_pct"] = float(a["value"])
                m["life_src"] = f"SMART {aid}"
                break
    unit = 32 * 2**20 if "intel" in (manufacturer or "").lower() else 512  # Intel: 32 MiB por unidad
    if 241 in attrs:
        m["written"] = attrs[241]["raw"] * unit
    if 242 in attrs:
        m["read"] = attrs[242]["raw"] * unit
    if 9 in attrs:
        m["hours"] = attrs[9]["raw"] & 0xFFFFFFFF
    if 12 in attrs:
        m["power_cycles"] = attrs[12]["raw"] & 0xFFFFFFFF
    if 194 in attrs:
        m["temp"] = attrs[194]["raw"] & 0xFF
    for key, aid in (("realloc", 5), ("pending", 197), ("uncorrectable", 198), ("crc", 199)):
        if aid in attrs:
            m[key] = attrs[aid]["raw"] & 0xFFFFFFFF
    return m


# ---------- NVMe ----------
def parse_nvme_health(b: bytes) -> dict:
    """Log de salud NVMe (página 02h, 512 bytes). Unidades de datos = 1000 × 512 bytes."""
    def u(lo, hi):
        return int.from_bytes(b[lo:hi], "little")
    kelvin = u(1, 3)
    return {"critical_warning": b[0], "temp": (kelvin - 273) if kelvin else None,
            "spare": b[3], "spare_thr": b[4], "used_pct": b[5],
            "read": u(32, 48) * 512000, "written": u(48, 64) * 512000,
            "power_cycles": u(112, 128), "hours": u(128, 144),
            "unsafe_shutdowns": u(144, 160), "media_errors": u(160, 176)}


def nvme_health_log(drive_index: int):
    """Lee el log de salud NVMe con IOCTL_STORAGE_QUERY_PROPERTY (requiere administrador). None si falla."""
    if not system.IS_WINDOWS:
        return None
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.DeviceIoControl.restype = wintypes.BOOL
    k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                    wintypes.LPVOID]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    invalid = ctypes.c_void_p(-1).value
    h = k32.CreateFileW(rf"\\.\PhysicalDrive{drive_index}", 0xC0000000, 3, None, 3, 0, None)
    if h in (None, invalid):
        return None
    try:
        hdr, spd, data = 8, 40, 512
        buf = bytearray(hdr + spd + data)
        struct.pack_into("<II", buf, 0, 50, 0)  # StorageDeviceProtocolSpecificProperty, PropertyStandardQuery
        # ProtocolType=NVMe(3), DataType=LogPage(2), Request=Health(2), Sub=0, Offset=40, Length=512
        struct.pack_into("<10I", buf, hdr, 3, 2, 2, 0, spd, data, 0, 0, 0, 0)
        cbuf = (ctypes.c_char * len(buf)).from_buffer(buf)
        ret = wintypes.DWORD(0)
        ok = k32.DeviceIoControl(h, 0x2D1400, cbuf, len(buf), cbuf, len(buf), ctypes.byref(ret), None)
        if not ok or ret.value < hdr + spd + data:
            return None
        return parse_nvme_health(bytes(buf[hdr + spd:hdr + spd + data]))
    except Exception:
        return None
    finally:
        k32.CloseHandle(h)


# ---------- ensamblado y concepto ----------
def build_disk(d: dict, attrs=None, nvme=None) -> dict:
    bus = (d.get("Bus") or "").strip()
    media = (d.get("Media") or "").strip()
    is_nvme = bus.lower() == "nvme"
    is_ssd = media.upper() == "SSD" or is_nvme
    model = (d.get("Model") or "").strip()
    manu = infer_manufacturer(model, d.get("Manufacturer"))
    info = {"id": d.get("Id"), "model": model, "manufacturer": manu, "serial": (d.get("Serial") or "").strip().rstrip("."),
            "firmware": (d.get("Firmware") or "").strip(), "bus": bus, "media": media or "?", "is_ssd": is_ssd,
            "size": d.get("Size") or 0, "health": (d.get("Health") or "").strip(),
            "read_err": d.get("ReadErr"), "write_err": d.get("WriteErr"), "source": "Contadores de Windows"}
    if d.get("Wear") is not None and is_ssd:
        info["life_pct"] = max(0.0, min(100.0, 100.0 - float(d["Wear"])))
    if d.get("Hours") is not None:
        info["hours"] = int(d["Hours"])
    if d.get("Temp") is not None and d["Temp"] > 0:
        info["temp"] = int(d["Temp"])
    if nvme:
        info.update(source="NVMe SMART (log de salud 02h)", life_pct=max(0.0, 100.0 - nvme["used_pct"]),
                    written=nvme["written"], read=nvme["read"], hours=nvme["hours"],
                    power_cycles=nvme["power_cycles"], unsafe=nvme["unsafe_shutdowns"],
                    media_errors=nvme["media_errors"], critical_warning=nvme["critical_warning"],
                    spare=nvme["spare"], spare_thr=nvme["spare_thr"])
        if nvme["temp"]:
            info["temp"] = nvme["temp"]
    elif attrs:
        met = ata_metrics(attrs, manu, is_ssd)
        info.update(met)
        info["source"] = "SMART ATA (WMI)" + (f" · vida: {met['life_src']}" if "life_src" in met else "")
    return info


def classify_disk(d: dict):
    """Devuelve (índice 0-3 o None, [motivos]). 0 funcional · 1 medio · 2 no funcional · 3 crítico."""
    state, why = [None], []

    def bump(level, reason):
        state[0] = level if state[0] is None else max(state[0], level)
        why.append(reason)

    life = d.get("life_pct")
    if life is not None:
        bump(0 if life >= 70 else 1 if life >= 40 else 2 if life >= 15 else 3,
             f"vida útil restante {life:.0f} %")
    elif d.get("is_ssd") and d.get("written") is not None:
        tb = d["written"] / 1e12
        bump(0 if tb < 150 else 1 if tb < 300 else 2 if tb < 600 else 3,
             f"{tb:.0f} TB escritos (estimado: el disco no informa % de vida)")
    elif not d.get("is_ssd") and d.get("hours") is not None:
        h = d["hours"]
        bump(0 if h < 30000 else 1 if h < 43800 else 2 if h < 61320 else 3,
             f"{fmt_int(h)} h encendido (disco mecánico, estimado)")
    health = (d.get("health") or "").lower()
    if health == "warning":
        bump(2, "Windows reporta salud: Advertencia")
    elif health == "unhealthy":
        bump(3, "Windows reporta salud: Con fallas")
    if d.get("critical_warning"):
        bump(3, f"advertencia crítica NVMe (0x{d['critical_warning']:02X})")
    if d.get("spare_thr") and d.get("spare") is not None and d["spare"] <= d["spare_thr"]:
        bump(3, f"reserva de bloques ({d['spare']} %) en o bajo el umbral")
    if (d.get("media_errors") or 0) > 0:
        bump(2 if d["media_errors"] >= 50 else 1, f"{d['media_errors']} errores de medio")
    if (d.get("pending") or 0) > 0 or (d.get("uncorrectable") or 0) > 0:
        bump(2, "sectores pendientes/incorregibles")
    if (d.get("realloc") or 0) > 0:
        bump(2 if d["realloc"] >= 100 else 1, f"{d['realloc']} sectores reasignados")
    return state[0], why


def disk_report(d: dict) -> list:
    size = system.human_size(d["size"]) if d.get("size") else "?"
    L = [("", "info"), (f"Disco {d['id']} · {d['model'] or 'Desconocido'}   [{d['bus']} · {d['media']} · {size}]", "warn")]
    L.append(kv("Fabricante", d["manufacturer"]))
    if d.get("serial"):
        L.append(kv("Serial", d["serial"]))
    if d.get("firmware"):
        L.append(kv("Firmware", d["firmware"]))
    life = d.get("life_pct")
    if life is not None:
        L.append(kv("Vida útil restante", f"{life:.0f} %   (consumido {100 - life:.0f} %)"))
    elif d.get("is_ssd"):
        L.append(kv("Vida útil restante", "No informada por el disco"))
    else:
        L.append(kv("Vida útil restante", "No aplica (disco mecánico)"))
    L.append(kv("Datos escritos", system.human_size(d["written"]) + ("  (aprox.)" if "SMART ATA" in d["source"] else "")
                if d.get("written") is not None else "No informado"))
    L.append(kv("Datos leídos", system.human_size(d["read"]) + ("  (aprox.)" if "SMART ATA" in d["source"] else "")
                if d.get("read") is not None else "No informado"))
    if d.get("hours") is not None:
        L.append(kv("Horas encendido", f"{fmt_int(d['hours'])} h  (≈ {d['hours'] / 8760:.1f} años)"))
    if d.get("power_cycles") is not None:
        L.append(kv("Encendidos", fmt_int(d["power_cycles"])))
    if d.get("temp") is not None:
        L.append(kv("Temperatura", f"{d['temp']} °C"))
    L.append(kv("Salud (Windows)", d["health"] or "?"))
    extra = []
    for key, name in (("media_errors", "errores de medio"), ("realloc", "reasignados"), ("pending", "pendientes"),
                      ("uncorrectable", "incorregibles"), ("crc", "errores CRC"), ("unsafe", "apagados bruscos")):
        if d.get(key):
            extra.append(f"{fmt_int(d[key])} {name}")
    if extra:
        L.append(kv("Alertas SMART", " · ".join(extra)))
    L.append(kv("Fuente de datos", d["source"]))
    idx, why = classify_disk(d)
    if idx is None:
        L.append(("  ▶ CONCEPTO: SIN DATOS SUFICIENTES — el disco/controladora no expone SMART (¿USB o RAID?).", "dim"))
    else:
        L.append((f"  ▶ CONCEPTO: {STATES[idx]} — {'; '.join(why)}", LEVELS[idx]))
        L.append((f"    {ADVICE[idx]}", LEVELS[idx]))
    return L


def build_all(raw: dict, nvme_reader=nvme_health_log) -> list:
    pnp = {p["Index"]: (p.get("Pnp") or "").lower() for p in as_list(raw.get("Pnp"))}
    smart = []
    for s in as_list(raw.get("Smart")):
        try:
            data = bytes(int(x) for x in str(s.get("Data") or "").split(",") if x != "")
        except ValueError:
            continue
        smart.append(((s.get("Instance") or "").lower(), data))
    out = []
    for d in as_list(raw.get("Disks")):
        attrs = nvme = None
        if (d.get("Bus") or "").lower() == "nvme":
            nvme = nvme_reader(d["Id"]) if nvme_reader else None
        else:
            key = pnp.get(d.get("Id"), "")
            for inst, data in smart:
                if key and inst.startswith(key):
                    attrs = parse_ata_smart(data)
                    break
        out.append(build_disk(d, attrs, nvme))
    return sorted(out, key=lambda x: x["id"] if x["id"] is not None else 99)


def report(raw: dict, nvme_reader=nvme_health_log) -> list:
    disks = build_all(raw, nvme_reader)
    L = [("━━━ ESTADO DEL DISCO ━━━", "warn")]
    if not disks:
        L.append(("No se detectaron discos (¿ejecutas como administrador?).", "err"))
        return L
    for d in disks:
        L.extend(disk_report(d))
    L.append(("", "info"))
    L.append(("Concepto: FUNCIONAL ≥70 % vida · MEDIO 40–69 % · NO FUNCIONAL 15–39 % · CRÍTICO <15 % "
              "(o errores SMART). Es una guía de Soporte, no una garantía del fabricante.", "dim"))
    return L


def run() -> list:
    if not system.IS_WINDOWS:
        return [("Solo disponible en Windows.", "err")]
    try:
        raw = run_ps_json(PS_DISKS, timeout=180)
        if not raw:
            return [("No se pudo leer el estado de los discos (PowerShell no devolvió datos).", "err")]
        return report(raw)
    except Exception as e:
        return [(f"Error al leer el estado del disco: {e}", "err")]
