"""Ejecución de comandos externos con lectura de salida en tiempo real."""
import re
import subprocess

from . import system
from .context import TaskContext

PERCENT_RE = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")


def parse_percent(text: str):
    found = PERCENT_RE.findall(text)
    if not found:
        return None
    try:
        v = float(found[-1].replace(",", "."))
    except ValueError:
        return None
    return v if 0 <= v <= 100 else None


def decode_output(raw: bytes, encoding=None) -> str:
    if encoding:
        return raw.decode(encoding, errors="replace")
    # sfc.exe emite UTF-16 aunque se redirija: quitar los nulos basta para ASCII.
    raw = raw.replace(b"\x00", b"")
    if system.IS_WINDOWS:
        try:
            return raw.decode("oem", errors="replace")
        except LookupError:
            pass
    return raw.decode("utf-8", errors="replace")


class LineSplitter:
    """Parte la salida por \\r o \\n (DISM/SFC actualizan el % con \\r)."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data: bytes):
        out = []
        for b in data:
            if b in (10, 13):
                if self.buf:
                    out.append(bytes(self.buf))
                    self.buf.clear()
            else:
                self.buf.append(b)
        return out

    def flush(self):
        if self.buf:
            tok = bytes(self.buf)
            self.buf.clear()
            return [tok]
        return []


def stream_process(cmd, ctx: TaskContext, on_progress=None, on_text=None, encoding=None) -> int:
    """Ejecuta cmd; las líneas con % van a on_progress(pct, texto), el resto a on_text(texto)."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=system.CREATE_NO_WINDOW)
    ctx.set_process(p)
    splitter = LineSplitter()

    def handle(tok: bytes):
        text = decode_output(tok, encoding).strip()
        if not text:
            return
        pct = parse_percent(text)
        if pct is not None:
            if on_progress:
                on_progress(pct, text)
        elif on_text:
            on_text(text)

    try:
        while True:
            chunk = p.stdout.read1(4096)
            if not chunk:
                break
            for tok in splitter.feed(chunk):
                handle(tok)
        for tok in splitter.flush():
            handle(tok)
        p.wait()
    finally:
        ctx.set_process(None)
        try:
            p.stdout.close()
        except Exception:
            pass
    ctx.check_cancel()
    return p.returncode


def run_powershell(script: str, timeout: int = 600):
    """Ejecuta un script PowerShell y devuelve (returncode, salida UTF-8)."""
    prefix = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;$ProgressPreference='SilentlyContinue';"
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", prefix + script],
        capture_output=True, timeout=timeout, creationflags=system.CREATE_NO_WINDOW)
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    err = (r.stderr or b"").decode("utf-8", errors="replace")
    return r.returncode, (out + ("\n" + err if err.strip() else "")).strip()
