"""Información general del equipo: hostname, SO, CPU, RAM, licencia, BIOS, cifrado y TPM."""
from ..core import system
from .common import as_list, kv, run_ps_json

PS_SYSINFO = r'''
$os=Get-CimInstance Win32_OperatingSystem
$cs=Get-CimInstance Win32_ComputerSystem
$bios=Get-CimInstance Win32_BIOS
$cpu=@(Get-CimInstance Win32_Processor)
$mem=@(Get-CimInstance Win32_PhysicalMemory)
$cv=Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
$lic=Get-CimInstance SoftwareLicensingProduct -Filter "ApplicationId='55c92734-d682-4d71-983e-d6ec3f16059f' AND PartialProductKey IS NOT NULL" | Select-Object -First 1
$svc=Get-CimInstance SoftwareLicensingService
$sb=$null; try { $sb = Confirm-SecureBootUEFI -ErrorAction Stop } catch { $sb = $null }
$vols=@()
try { $vols=@(Get-BitLockerVolume -ErrorAction Stop | ForEach-Object { [pscustomobject]@{Mount=[string]$_.MountPoint;Protection=[string]$_.ProtectionStatus;Volume=[string]$_.VolumeStatus;Percent=$_.EncryptionPercentage;Method=[string]$_.EncryptionMethod} }) } catch {}
if($vols.Count -eq 0){
  $vols=@(Get-CimInstance -Namespace root\CIMV2\Security\MicrosoftVolumeEncryption -ClassName Win32_EncryptableVolume | ForEach-Object { [pscustomobject]@{Mount=[string]$_.DriveLetter;Protection=[string]$_.ProtectionStatus;Volume='';Percent=$null;Method=[string]$_.EncryptionMethod} })
}
$tpm=Get-CimInstance -Namespace root\cimv2\security\microsofttpm -ClassName Win32_Tpm
$gt=$null; try { $gt=Get-Tpm -ErrorAction Stop } catch {}
$r=[ordered]@{
 Host=$env:COMPUTERNAME; Domain=$cs.Domain; PartOfDomain=$cs.PartOfDomain
 Maker=$cs.Manufacturer; Model=$cs.Model; Serial=$bios.SerialNumber
 OsCaption=$os.Caption; OsVersion=$os.Version; OsBuild=$os.BuildNumber; OsArch=$os.OSArchitecture
 DisplayVersion=$cv.DisplayVersion; Ubr=$cv.UBR; InstallDate=[string]$os.InstallDate
 Cpu=@($cpu | ForEach-Object { [pscustomobject]@{Name=$_.Name;Cores=$_.NumberOfCores;Threads=$_.NumberOfLogicalProcessors;MaxMHz=$_.MaxClockSpeed} })
 RamTotal=[int64]$cs.TotalPhysicalMemory
 RamModules=@($mem | ForEach-Object { [pscustomobject]@{Cap=[int64]$_.Capacity;Speed=$_.Speed;Type=[int]$_.SMBIOSMemoryType;Maker=$_.Manufacturer} })
 LicName=$lic.Name; LicDesc=$lic.Description; LicStatus=$lic.LicenseStatus; LicChannel=$lic.ProductKeyChannel; LicPartial=$lic.PartialProductKey
 Oa3=(-not [string]::IsNullOrEmpty($svc.OA3xOriginalProductKey))
 BiosMaker=$bios.Manufacturer; BiosVersion=$bios.SMBIOSBIOSVersion; BiosDate=[string]$bios.ReleaseDate
 SecureBoot=$sb
 Volumes=$vols
 TpmSpec=$tpm.SpecVersion; TpmMaker=$tpm.ManufacturerIdTxt; TpmMakerVer=$tpm.ManufacturerVersion
 TpmEnabled=$tpm.IsEnabled_InitialValue; TpmActivated=$tpm.IsActivated_InitialValue
 TpmPresent=$gt.TpmPresent; TpmReady=$gt.TpmReady
}
ConvertTo-Json -InputObject $r -Depth 5 -Compress
'''

MEM_TYPES = {20: "DDR", 21: "DDR2", 24: "DDR3", 26: "DDR4", 29: "LPDDR3", 30: "LPDDR4",
             34: "DDR5", 35: "LPDDR5"}
TPM_MAKERS = {"INTC": "Intel (PTT)", "AMD": "AMD (fTPM)", "IFX": "Infineon", "STM": "STMicroelectronics",
              "NTC": "Nuvoton", "MSFT": "Microsoft (virtual)", "QCOM": "Qualcomm", "ATML": "Atmel"}
LICENSE_STATUS = {0: "Sin licencia", 1: "Activado", 2: "Período de gracia (inicial)",
                  3: "Período de gracia (tolerancia)", 4: "Período de gracia (no genuino)",
                  5: "Notificación", 6: "Período de gracia extendido"}


def license_channel(channel, desc) -> str:
    c = (channel or desc or "").lower()
    if "oem" in c:
        return "OEM (clave en BIOS/fabricante)"
    if "retail" in c:
        return "Retail (compra individual / licencia digital)"
    if "gvlk" in c or "kms" in c:
        return "Volumen · KMS (GVLK)"
    if "mak" in c:
        return "Volumen · MAK"
    if "volume" in c:
        return "Volumen"
    return channel or desc or "No determinado"


def format_ram(total, modules) -> str:
    mods = [m for m in as_list(modules) if m.get("Cap")]
    if mods:
        gb = sum(m["Cap"] for m in mods) / 2**30
        caps = sorted({round(m["Cap"] / 2**30) for m in mods})
        kinds = sorted({MEM_TYPES.get(m.get("Type"), "") for m in mods} - {""})
        speeds = [m.get("Speed") for m in mods if m.get("Speed")]
        txt = f"{gb:.0f} GB"
        detail = f"{len(mods)} × {caps[0]} GB" if len(caps) == 1 else f"{len(mods)} módulos"
        if kinds:
            detail += " " + "/".join(kinds)
        if speeds:
            detail += f" @ {max(speeds)} MHz"
        return f"{txt}  ({detail})"
    return f"{total / 2**30:.1f} GB" if total else "No disponible"


def encryption_state(v) -> tuple:
    prot = str(v.get("Protection") or "").lower()
    vol = str(v.get("Volume") or "").lower()
    pct = v.get("Percent")
    on = prot in ("on", "1")
    if "progress" in vol:
        return f"Cifrando/descifrando… {pct if pct is not None else '?'} %", "warn"
    if vol.replace(" ", "") == "fullydecrypted":
        return "No cifrado", "warn"
    if on:
        return "Cifrado (BitLocker activo)", "ok"
    if "encrypted" in vol:
        return "Cifrado, pero la protección está suspendida", "warn"
    if prot in ("off", "0"):
        return "No cifrado", "warn"
    return "Estado no determinado", "dim"


def tpm_summary(d) -> tuple:
    spec = (d.get("TpmSpec") or "").split(",")[0].strip()
    present = d.get("TpmPresent")
    if not spec and not present:
        return "No detectado (desactivado en BIOS o sin TPM)", "err"
    maker = TPM_MAKERS.get((d.get("TpmMaker") or "").strip("\x00 "), d.get("TpmMaker") or "")
    txt = f"TPM {spec}" if spec else "TPM presente"
    if maker:
        txt += f" · {maker}"
    if d.get("TpmMakerVer"):
        txt += f" · fw {str(d['TpmMakerVer']).strip()}"
    ready = d.get("TpmReady")
    on = d.get("TpmEnabled")
    txt += "  [" + ("listo" if ready else "habilitado" if on else "presente, no listo") + "]"
    ok = spec.startswith("2")
    return txt, ("ok" if ok else "warn")


def _wmi_date(s):
    s = str(s or "")
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 and s[:8].isdigit() else s[:10]


def report(d: dict) -> list:
    L = [("━━━ INFORMACIÓN DEL EQUIPO ━━━", "warn")]
    L.append(kv("Hostname", d.get("Host") or "?"))
    dom = d.get("Domain") or ""
    L.append(kv("Dominio/Grupo", f"{dom}  ({'dominio' if d.get('PartOfDomain') else 'grupo de trabajo'})"))
    L.append(kv("Equipo", f"{(d.get('Maker') or '').strip()} {(d.get('Model') or '').strip()}".strip() or "?"))
    L.append(kv("Serial del equipo", d.get("Serial") or "No reportado"))
    L.append(("", "info"))
    L.append(kv("Sistema operativo", f"{d.get('OsCaption') or '?'} ({d.get('OsArch') or ''})"))
    ver = d.get("DisplayVersion") or ""
    build = f"{d.get('OsBuild')}.{d.get('Ubr')}" if d.get("Ubr") is not None else d.get("OsBuild")
    L.append(kv("Versión del SO", f"{ver + ' · ' if ver else ''}build {build} (NT {d.get('OsVersion')})"))
    L.append(("", "info"))
    for i, c in enumerate(as_list(d.get("Cpu"))):
        name = " ".join(str(c.get("Name") or "?").split())
        L.append(kv("Procesador" if i == 0 else "", f"{name}  ({c.get('Cores')} núcleos / {c.get('Threads')} hilos)"))
    L.append(kv("RAM", format_ram(d.get("RamTotal"), d.get("RamModules"))))
    L.append(("", "info"))
    st = LICENSE_STATUS.get(d.get("LicStatus"), "No determinado")
    L.append((f"  {'Licencia Windows':<22}{license_channel(d.get('LicChannel'), d.get('LicDesc'))}", "info"))
    L.append((f"  {'  Estado':<22}{st}" + (f"  · clave …{d['LicPartial']}" if d.get("LicPartial") else ""),
              "ok" if d.get("LicStatus") == 1 else "err"))
    L.append(kv("  Clave OEM en BIOS", "Sí" if d.get("Oa3") else "No"))
    L.append(("", "info"))
    L.append(kv("BIOS", f"{(d.get('BiosMaker') or '').strip()} · versión {(d.get('BiosVersion') or '?').strip()}"
                        f" · {_wmi_date(d.get('BiosDate'))}"))
    sb = d.get("SecureBoot")
    L.append(kv("Arranque", "Legacy/BIOS (sin Secure Boot)" if sb is None else
                f"UEFI · Secure Boot {'activado' if sb else 'desactivado'}"))
    L.append(("", "info"))
    vols = [v for v in as_list(d.get("Volumes"))]
    if vols:
        for i, v in enumerate(vols):
            txt, lvl = encryption_state(v)
            extra = f" · {v['Method']}" if v.get("Method") and v["Method"] not in ("None", "0") and lvl == "ok" else ""
            L.append((f"  {'Cifrado de disco' if i == 0 else '':<22}{v.get('Mount') or '?'}  {txt}{extra}", lvl))
    else:
        L.append(kv("Cifrado de disco", "No se pudo consultar (¿sin administrador o edición sin BitLocker?)"))
    txt, lvl = tpm_summary(d)
    L.append((f"  {'TPM':<22}{txt}", lvl))
    return L


def collect() -> dict:
    return run_ps_json(PS_SYSINFO, timeout=120) or {}


def run() -> list:
    if not system.IS_WINDOWS:
        return [("Solo disponible en Windows.", "err")]
    try:
        data = collect()
        if not data:
            return [("No se pudo leer la información del sistema (PowerShell no devolvió datos).", "err")]
        return report(data)
    except Exception as e:  # nunca romper la UI
        return [(f"Error al leer la información del equipo: {e}", "err")]
