"""7. Sincronización con GLPI: FUERZA un inventario inmediato con el GLPI Agent instalado en el equipo.

Método principal («service»): le ordena al servicio del agente instalado que ejecute el inventario ahora
(equivale al enlace «Forzar un inventario» de su página local http://127.0.0.1:62354) y sigue su estado
(running → waiting) y su log hasta que termina. Si el servicio no responde, o el agente está configurado
con otro servidor, usa el método de respaldo («cli»): glpi-agent.bat --force --tasks=inventory
--server=<URL de Girag> en primer plano, mostrando su salida."""
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from ..core import system
from ..core.context import TaskContext, TaskResult
from ..core.process import stream_process
from .base import Task

DEFAULT_CFG = {
    "server_url": "https://soporte.girag.com/plugins/glpiinventory",
    "agent_paths": [r"%ProgramFiles%\GLPI-Agent\glpi-agent.bat",
                    r"%ProgramFiles(x86)%\GLPI-Agent\glpi-agent.bat"],
    "service_name": "glpi-agent",
    "http_port": 62354,
    "method": "auto",
    "log_paths": [r"%ProgramFiles%\GLPI-Agent\logs\glpi-agent.log"],
    "timeout_minutes": 15,
}
ERROR_RX = re.compile(r"\[error\]|\bfatal\b|can't connect|cannot connect|\bfailed\b", re.I)


# ------------------------------------------------------------------ configuración y detección
def load_config() -> dict:
    cfg = dict(DEFAULT_CFG)
    cfg.update(system.load_json_config("glpi.json", {}))
    return cfg


def find_agent(cfg: dict):
    for p in cfg["agent_paths"]:
        p = os.path.expandvars(p)
        if os.path.isfile(p):
            return p
    return shutil.which("glpi-agent.bat") or shutil.which("glpi-agent")


def service_state(name: str):
    """'RUNNING' / 'STOPPED' / ... o None si el servicio no existe (o no es Windows)."""
    if not system.IS_WINDOWS:
        return None
    try:
        r = subprocess.run(["sc", "query", name], capture_output=True, text=True, timeout=20,
                           creationflags=system.CREATE_NO_WINDOW)
    except Exception:
        return None
    m = re.search(r"STATE\s*:\s*\d+\s+(\w+)", r.stdout or "")
    return m.group(1).upper() if m else None


def start_service(name: str, wait: float = 20.0) -> bool:
    try:
        subprocess.run(["net", "start", name], capture_output=True, timeout=60,
                       creationflags=system.CREATE_NO_WINDOW)
    except Exception:
        return False
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if service_state(name) == "RUNNING":
            return True
        time.sleep(1)
    return False


def read_agent_settings() -> dict:
    """Ajustes del agente guardados por su instalador en HKLM\\SOFTWARE\\GLPI-Agent (server, httpd-port...)."""
    try:
        import winreg
    except ImportError:
        return {}
    out = {}
    for path in (r"SOFTWARE\GLPI-Agent", r"SOFTWARE\WOW6432Node\GLPI-Agent"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                i = 0
                while True:
                    try:
                        name, data, _t = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    out.setdefault(name.lower(), data)
                    i += 1
        except OSError:
            continue
    return out


def same_server(a: str, b: str) -> bool:
    """Compara URLs ignorando mayúsculas, barra final y varias URLs separadas por coma."""
    norm = lambda u: u.strip().rstrip("/").lower()
    return norm(b) in {norm(x) for x in re.split(r"[,\s]+", a or "") if x}


# ------------------------------------------------------------------ red
def check_server(url: str, timeout: int = 12):
    """(alcanzable, detalle). Cualquier respuesta HTTP, incluso 4xx, indica que el servidor responde."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Girag-Maintenance/0.5"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return True, f"HTTP {e.code}"
    except Exception as e:
        return False, str(getattr(e, "reason", e))


def http_local(url: str, method: str = "GET", timeout: float = 8.0):
    """Petición al agente local (sin proxy). Devuelve (código|None, cuerpo|error)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None,
                                 headers={"User-Agent": "Girag-Maintenance/0.5"})
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(2048).decode("utf-8", "replace")
    except Exception as e:
        return None, str(getattr(e, "reason", e))


def trigger_now(port: int, http=http_local):
    """Ordena al agente instalado ejecutar el inventario ya. Devuelve (ok, detalle)."""
    url = f"http://127.0.0.1:{port}/now"
    code, body = http(url, "GET")
    if code == 405:                      # algunas versiones exigen POST
        code, body = http(url, "POST")
    if code is None:
        return False, f"el agente no responde en el puerto {port} (¿servicio detenido o HTTP desactivado?)"
    if code in (401, 403) or re.search(r"untrusted|not allowed|forbidden", body or "", re.I):
        return False, "el agente rechazó la orden local (httpd-trust no permite 127.0.0.1)"
    if code >= 400:
        return False, f"el agente respondió HTTP {code}"
    return True, f"HTTP {code}"


def parse_status(body: str):
    """'running' | 'waiting' | None según la página /status del agente."""
    t = re.sub(r"<[^>]+>", " ", body or "").lower()
    if "running" in t:
        return "running"
    if "waiting" in t:
        return "waiting"
    return None


class LogTail:
    """Lee solo las líneas NUEVAS de los logs del agente (desde que se creó el objeto)."""

    def __init__(self, paths):
        self.offsets = {}
        for p in paths:
            p = os.path.expandvars(p)
            if os.path.isfile(p):
                self.offsets[p] = os.path.getsize(p)

    def read_new(self):
        lines = []
        for p, off in list(self.offsets.items()):
            try:
                size = os.path.getsize(p)
                if size < off:           # rotado
                    off = 0
                with open(p, "rb") as f:
                    f.seek(off)
                    data = f.read()
                self.offsets[p] = off + len(data)
                lines += [l.strip() for l in data.decode("utf-8", "replace").splitlines() if l.strip()]
            except OSError:
                continue
        return lines


class SyncProgress:
    """El agente no informa porcentaje. Se estima por hitos del log y avance suave por línea."""
    STAGES = [(re.compile(r"target|server", re.I), 10),
              (re.compile(r"running task|task inventory|inventory task", re.I), 25),
              (re.compile(r"sending|contact request|post", re.I), 80),
              (re.compile(r"accepted|saved|sent|success|done", re.I), 95)]

    def __init__(self):
        self.value = 3.0

    def feed(self, line: str) -> float:
        floor = 0
        for rx, pct in self.STAGES:
            if rx.search(line):
                floor = max(floor, pct)
        cap = 78.0 if self.value < 80 else 95.0
        self.value = max(self.value, floor, min(self.value + 0.8, cap))
        return min(self.value, 99.0)


def build_command(agent: str, server_url: str):
    # --force: envía el inventario aunque el servidor no lo pida; --tasks=inventory: solo inventario.
    return [agent, "--force", "--tasks=inventory", f"--server={server_url}"]


def analyze(lines, rc: int):
    errors = [l for l in lines if ERROR_RX.search(l)]
    return (rc == 0 and not errors), errors


# ------------------------------------------------------------------ métodos
def sync_via_service(ctx: TaskContext, cfg: dict, port: int, tail: LogTail, http=http_local,
                     poll: float = 1.0, grace: float = 40.0, sleep=time.sleep):
    """Pide al servicio del agente un inventario inmediato y espera a que termine.
    Devuelve (ok, motivo_si_falla, líneas_de_log)."""
    ok, detail = trigger_now(port, http)
    if not ok:
        return False, detail, []
    ctx.log(f"Orden enviada al agente instalado (puerto {port}).", "ok")
    ctx.status("El agente está generando y enviando el inventario…")
    model, lines, seen_running, last = SyncProgress(), [], False, None
    status_url = f"http://127.0.0.1:{port}/status"
    timeout_s, t0 = cfg["timeout_minutes"] * 60, time.monotonic()
    while True:
        ctx.check_cancel()
        elapsed = time.monotonic() - t0
        code, body = http(status_url, "GET", 6.0)
        state = parse_status(body) if code == 200 else None
        for ln in tail.read_new():
            lines.append(ln)
            ctx.log("  agente: " + ln, "err" if ERROR_RX.search(ln) else "dim")
            model.feed(ln)
        if state and state != last:
            ctx.log(f"  estado del agente: {'ejecutando inventario' if state == 'running' else 'en espera'}", "dim")
            last = state
        if state == "running":
            seen_running = True
        pct = max(model.value, min(85.0, 8 + elapsed * 0.9)) if seen_running else max(model.value, min(20.0, 3 + elapsed * 0.5))
        ctx.progress(min(pct, 99.0))
        if seen_running and state == "waiting":
            errors = [l for l in lines if ERROR_RX.search(l)]
            return (not errors), ("el log del agente reporta errores" if errors else ""), lines
        if not seen_running and elapsed >= grace:
            return False, "el agente aceptó la orden pero no se observó la ejecución del inventario", lines
        if elapsed >= timeout_s:
            return False, "tiempo de espera agotado esperando al agente", lines
        sleep(poll)


def sync_via_cli(ctx: TaskContext, agent: str, url: str):
    """Respaldo: ejecuta el agente en primer plano. Devuelve (ok, errores)."""
    cmd = build_command(agent, url)
    ctx.log("▶ " + " ".join(cmd), "dim")
    ctx.status("Generando y enviando inventario (línea de comandos)…")
    model, lines = SyncProgress(), []

    def handle(text, level="info"):
        lines.append(text)
        lvl = "err" if re.search(r"\[error\]", text, re.I) else "warn" if re.search(r"\[warning\]", text, re.I) else level
        ctx.log("  " + text, lvl)
        ctx.progress(model.feed(text))

    rc = stream_process(cmd, ctx, on_text=handle, on_progress=lambda pct, text: handle(text))
    ok, errors = analyze(lines, rc)
    return ok, (errors[:8] or ([f"código de salida {rc}"] if rc else []))


class GlpiSyncTask(Task):
    id = "glpi"
    title = "Sincronizar con GLPI"
    description = ("Fuerza ahora el inventario del GLPI Agent instalado y lo envía a "
                   "soporte.girag.com, mostrando el avance.")
    icon = "S"
    button = "Sincronizar"

    def run(self, ctx: TaskContext) -> TaskResult:
        cfg = load_config()
        url, name = cfg["server_url"], cfg["service_name"]
        ctx.progress(None)
        ctx.status("Buscando el GLPI Agent instalado…")
        agent = find_agent(cfg)
        state = service_state(name)
        if not agent and state is None:
            return TaskResult(False, "No se encontró el GLPI Agent en este equipo "
                                     "(C:\\Program Files\\GLPI-Agent). Instálalo y vuelve a intentar.")
        settings = read_agent_settings()
        configured = str(settings.get("server") or "")
        port = int(settings.get("httpd-port") or cfg["http_port"])
        estado = {"RUNNING": "en ejecución", "STOPPED": "detenido"}.get(state, state or "no instalado")
        ctx.log(f"GLPI Agent: {agent or 'no encontrado'} · servicio «{name}»: {estado}", "dim")
        mismatch = bool(configured) and not same_server(configured, url)
        if configured:
            ctx.log(f"Servidor configurado en el agente: {configured}", "warn" if mismatch else "dim")

        ctx.status("Comprobando conexión con el servidor GLPI…")
        ok, detail = check_server(url)
        ctx.log(f"Servidor {url}: {'responde' if ok else 'NO responde'} ({detail})", "ok" if ok else "err")
        if not ok:
            return TaskResult(False, f"No hay conexión con {url}. Revisa red/VPN y vuelve a intentar.", detail)

        method = str(cfg.get("method", "auto")).lower()
        plan = []
        if method in ("auto", "service") and state is not None and not mismatch:
            plan.append("service")
        if method in ("auto", "cli") and agent:
            plan.append("cli")
        if mismatch and method == "auto":
            ctx.log("El agente envía a otro servidor: se usará la línea de comandos con la URL de Girag.", "warn")
        if not plan:
            return TaskResult(False, "No hay un método disponible para forzar el inventario "
                                     "(revisa «method» en config/glpi.json y el servicio del agente).")
        if ctx.dry_run:
            return TaskResult(True, "Simulación: agente y servidor listos; no se forzó el inventario.",
                              "Métodos previstos: " + " → ".join(plan))

        t0, reasons = time.time(), []
        for m in plan:
            ctx.check_cancel()
            if m == "service":
                if state != "RUNNING":
                    ctx.status("Iniciando el servicio del agente…")
                    ctx.log("El servicio del agente está detenido: iniciándolo…", "warn")
                    if not start_service(name):
                        reasons.append("no se pudo iniciar el servicio del agente")
                        ctx.log("✘ " + reasons[-1], "err")
                        continue
                    time.sleep(3)
                ctx.log("Método: orden «forzar inventario» al servicio del agente instalado.", "info")
                ok, why, _lines = sync_via_service(ctx, cfg, port, LogTail(self._logs(cfg, settings)),
                                                   poll=float(cfg.get("poll_seconds", 1.0)),
                                                   grace=float(cfg.get("start_grace_seconds", 40.0)))
                if ok:
                    ctx.progress(100)
                    return TaskResult(True, f"Inventario forzado y enviado a GLPI por el agente instalado "
                                            f"en {time.time() - t0:.0f} s.")
                reasons.append(why)
                ctx.log(f"✘ Servicio: {why}.", "err")
                if "cli" in plan:
                    ctx.log("Se intentará con la línea de comandos del agente…", "warn")
            else:
                ctx.log("Método: glpi-agent.bat --force (en primer plano).", "info")
                ok, errors = sync_via_cli(ctx, agent, url)
                if ok:
                    ctx.progress(100)
                    return TaskResult(True, f"Inventario forzado y enviado a GLPI con el agente "
                                            f"(línea de comandos) en {time.time() - t0:.0f} s.")
                reasons += errors
        return TaskResult(False, "No se pudo forzar la sincronización con GLPI.",
                          "\n".join(reasons[:8]) or "Revisa el log para más detalles.")

    @staticmethod
    def _logs(cfg, settings):
        paths = list(cfg.get("log_paths", []))
        if settings.get("logfile"):
            paths.append(str(settings["logfile"]))
        return paths
