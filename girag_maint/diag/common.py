"""Utilidades comunes de los diagnósticos (solo lectura, nunca modifican el equipo)."""
import base64
import json
import subprocess

from ..core import system

PS_PREFIX = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
             "$ProgressPreference='SilentlyContinue';$ErrorActionPreference='SilentlyContinue';")


def extract_json(text: str):
    i = text.find("{")
    if i < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[i:])
        return obj
    except ValueError:
        return None


def run_ps_json(script: str, timeout: int = 120):
    """Ejecuta PowerShell con -EncodedCommand (sin problemas de comillas) y devuelve el JSON impreso."""
    enc = base64.b64encode((PS_PREFIX + script).encode("utf-16-le")).decode("ascii")
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-EncodedCommand", enc],
        capture_output=True, timeout=timeout, creationflags=system.CREATE_NO_WINDOW)
    return extract_json((r.stdout or b"").decode("utf-8", errors="replace"))


def as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def kv(label: str, value, width: int = 22):
    return (f"  {label:<{width}}{value}", "info")


def fmt_int(n) -> str:
    return f"{int(n):,}".replace(",", ".")
