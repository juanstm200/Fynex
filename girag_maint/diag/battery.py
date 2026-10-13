"""Estado de la batería: fabricante, serial, salud (%), ciclos, Wh y concepto."""
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from ..core import system
from .common import as_list, fmt_int, kv, run_ps_json

PS_BATTERY = r'''
$w=@(Get-CimInstance Win32_Battery | ForEach-Object { [pscustomobject]@{Name=[string]$_.Name;Chemistry=[int]$_.Chemistry;Charge=$_.EstimatedChargeRemaining;Status=[int]$_.BatteryStatus} })
$st=@(Get-CimInstance -Namespace root\wmi -ClassName BatteryStaticData | ForEach-Object {
  $chem=''; try { $chem=[System.Text.Encoding]::ASCII.GetString([byte[]]$_.Chemistry).Trim([char]0) } catch {}
  [pscustomobject]@{Manufacturer=[string]$_.ManufactureName;Serial=[string]$_.SerialNumber;Name=[string]$_.DeviceName;Design=[int64]$_.DesignedCapacity;Chem=$chem} })
$fc=@(Get-CimInstance -Namespace root\wmi -ClassName BatteryFullChargedCapacity | ForEach-Object { [int64]$_.FullChargedCapacity })
$cy=@(Get-CimInstance -Namespace root\wmi -ClassName BatteryCycleCount | ForEach-Object { [int64]$_.CycleCount })
ConvertTo-Json -InputObject ([ordered]@{Win32=$w;Static=$st;Full=$fc;Cycles=$cy}) -Depth 4 -Compress
'''

STATES = ("FUNCIONAL", "MEDIO FUNCIONAL", "NO FUNCIONAL", "CRÍTICO · CAMBIAR")
LEVELS = ("ok", "warn", "err", "err")
ADVICE = ("Autonomía normal. Sin acciones.",
          "Autonomía reducida pero usable. Sugerir reemplazo a mediano plazo.",
          "Autonomía muy reducida. Programar el cambio de batería.",
          "Batería agotada o con riesgo (hinchazón/apagones). Reemplazar lo antes posible.")
CHEMISTRY = {1: "Otra", 2: "Desconocida", 3: "Plomo-ácido", 4: "NiCd", 5: "NiMH", 6: "Li-ion", 7: "Zinc-aire",
             8: "Li-polímero"}
STATUS = {1: "Descargando", 2: "Con corriente de CA", 3: "Carga completa", 4: "Baja", 5: "Crítica",
          6: "Cargando", 7: "Cargando (alta)", 8: "Cargando (baja)", 9: "Cargando (crítica)",
          11: "Carga parcial"}


def parse_powercfg_xml(data: bytes) -> list:
    """Extrae baterías de `powercfg /batteryreport /xml` (ignora el espacio de nombres)."""
    root = ET.fromstring(data)
    out = []
    for el in root.iter():
        if el.tag.split("}")[-1] != "Battery":
            continue
        f = {c.tag.split("}")[-1]: (c.text or "").strip() for c in el}
        if "DesignCapacity" not in f and "FullChargeCapacity" not in f:
            continue

        def num(k):
            try:
                return int(f.get(k, ""))
            except ValueError:
                return None
        out.append({"Manufacturer": f.get("Manufacturer"), "Serial": f.get("SerialNumber"),
                    "Chem": f.get("Chemistry"), "Design": num("DesignCapacity"),
                    "Full": num("FullChargeCapacity"), "Cycles": num("CycleCount"),
                    "Date": f.get("ManufactureDate")})
    return out


def powercfg_batteries() -> list:
    path = os.path.join(tempfile.gettempdir(), "girag_battery_report.xml")
    try:
        subprocess.run(["powercfg", "/batteryreport", "/xml", "/output", path], capture_output=True,
                       timeout=60, creationflags=system.CREATE_NO_WINDOW)
        with open(path, "rb") as fh:
            return parse_powercfg_xml(fh.read())
    except Exception:
        return []
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def merge_batteries(raw: dict, pcfg: list) -> list:
    """Une WMI (preferente) con powercfg para completar fabricante, serial y ciclos."""
    wmi, static = as_list(raw.get("Win32")), as_list(raw.get("Static"))
    full, cyc = as_list(raw.get("Full")), as_list(raw.get("Cycles"))
    n = max(len(wmi), len(static), len(pcfg))
    out = []
    for i in range(n):
        s = static[i] if i < len(static) else {}
        p = pcfg[i] if i < len(pcfg) else {}
        w = wmi[i] if i < len(wmi) else {}
        cycles = cyc[i] if i < len(cyc) else None
        if not cycles:  # 0 = no soportado por el controlador
            cycles = p.get("Cycles") or None
        out.append({
            "manufacturer": (s.get("Manufacturer") or p.get("Manufacturer") or "").strip() or "No reportado",
            "serial": (s.get("Serial") or p.get("Serial") or "").strip() or "No reportado",
            "name": (s.get("Name") or w.get("Name") or "").strip(),
            "chemistry": (s.get("Chem") or p.get("Chem") or CHEMISTRY.get(w.get("Chemistry"), "")).strip(),
            "design_mwh": s.get("Design") or p.get("Design") or None,
            "full_mwh": (full[i] if i < len(full) else None) or p.get("Full") or None,
            "cycles": cycles, "charge": w.get("Charge"), "status": w.get("Status"),
            "date": p.get("Date")})
    return out


def classify_battery(health, cycles):
    """health = % de capacidad actual vs. diseño. Devuelve (índice 0-3 o None, [motivos])."""
    level, why = None, []
    if health is not None:
        level = 0 if health >= 80 else 1 if health >= 60 else 2 if health >= 40 else 3
        why.append(f"salud {min(health, 100):.0f} % de la capacidad de diseño")
    if cycles:
        c = 0 if cycles < 400 else 1 if cycles < 700 else 2 if cycles < 1000 else 3
        if health is not None:  # con buena capacidad, los ciclos solos no condenan la batería
            c = min(c, 1 if health >= 80 else 2 if health >= 60 else 3)
        if c:
            why.append(f"{fmt_int(cycles)} ciclos (uso alto)")
        level = c if level is None else max(level, c)
        if not c and level == 0:
            why.append(f"{fmt_int(cycles)} ciclos")
    return level, why


def battery_report(b: dict, n: int, total: int) -> list:
    L = [("", "info"), (f"Batería {n}" if total > 1 else "Batería", "warn")]
    L.append(kv("Fabricante", b["manufacturer"]))
    L.append(kv("Serial", b["serial"]))
    if b["name"] or b["chemistry"]:
        L.append(kv("Modelo / química", " · ".join(x for x in (b["name"], b["chemistry"]) if x)))
    if b.get("date"):
        L.append(kv("Fabricación", b["date"]))
    d, f = b["design_mwh"], b["full_mwh"]
    health = (f / d * 100) if d and f else None
    if health is not None:
        L.append(kv("Vida útil (salud)", f"{min(health, 100):.0f} %" + ("  (capacidad ≥ diseño)" if health > 100 else "")))
        L.append(kv("Capacidad de diseño", f"{d / 1000:.1f} Wh"))
        L.append(kv("Capacidad actual", f"{f / 1000:.1f} Wh   (perdidos {max(d - f, 0) / 1000:.1f} Wh)"))
    else:
        L.append(kv("Vida útil (salud)", "No disponible (el equipo no reporta capacidades)"))
    L.append(kv("Ciclos de carga", fmt_int(b["cycles"]) if b["cycles"] else "No reportado por el equipo"))
    if b.get("charge") is not None:
        L.append(kv("Carga actual", f"{b['charge']} %  · {STATUS.get(b.get('status'), '')}".rstrip(" ·")))
    idx, why = classify_battery(health, b["cycles"])
    if idx is None:
        L.append(("  ▶ CONCEPTO: SIN DATOS SUFICIENTES para evaluar la batería.", "dim"))
    else:
        L.append((f"  ▶ CONCEPTO: {STATES[idx]} — {'; '.join(why)}", LEVELS[idx]))
        L.append((f"    {ADVICE[idx]}", LEVELS[idx]))
    return L


def report(raw: dict, pcfg=None) -> list:
    L = [("━━━ ESTADO DE LA BATERÍA ━━━", "warn")]
    if not as_list(raw.get("Win32")) and not as_list(raw.get("Static")):
        L.append(("Este equipo no tiene batería (escritorio/All-in-One) o Windows no la detecta.", "dim"))
        return L
    bats = merge_batteries(raw, pcfg or [])
    for i, b in enumerate(bats, 1):
        L.extend(battery_report(b, i, len(bats)))
    L.append(("", "info"))
    L.append(("Concepto: FUNCIONAL ≥80 % de capacidad · MEDIO 60–79 % · NO FUNCIONAL 40–59 % · CRÍTICO <40 %. "
              "Los ciclos suben el estado (≥400 medio, ≥700 no funcional, ≥1000 crítico) salvo que la capacidad siga alta.",
              "dim"))
    return L


def run() -> list:
    if not system.IS_WINDOWS:
        return [("Solo disponible en Windows.", "err")]
    try:
        raw = run_ps_json(PS_BATTERY, timeout=60)
        if raw is None:
            return [("No se pudo leer la batería (PowerShell no devolvió datos).", "err")]
        pcfg = powercfg_batteries() if (as_list(raw.get("Win32")) or as_list(raw.get("Static"))) else []
        return report(raw, pcfg)
    except Exception as e:
        return [(f"Error al leer la batería: {e}", "err")]
