"""6. Actualización de aplicaciones con winget, solo las que el técnico apruebe."""
import json
import re
import shutil
import subprocess

from ..core import system
from ..core.context import TaskContext, TaskResult
from ..core.process import stream_process
from .base import Task

NOT_APPLICABLE = 0x8A15002B   # "no hay actualización aplicable": no es un error
SIZE_LINE = re.compile(r"^\s*[\d.,]+\s*[KMG]?B\s*/\s*[\d.,]+\s*[KMG]?B", re.I)
FOOTER = re.compile(r"^\d+\s+(upgrades?|actualizaci|package|paquete)", re.I)


def find_winget():
    return shutil.which("winget") or shutil.which("winget.exe")


def approved_path():
    return system.data_dir() / "winget_approved.json"


def load_approved() -> set:
    try:
        with open(approved_path(), encoding="utf-8") as f:
            return set(json.load(f).get("approved", []))
    except (OSError, ValueError):
        pass
    return set(system.load_json_config("winget_approved.json", {}).get("approved", []))


def save_approved(ids) -> None:
    approved_path().write_text(json.dumps({"approved": sorted(ids)}, indent=2, ensure_ascii=False),
                               encoding="utf-8")


def parse_upgrade_table(text: str, approved=()):
    """Interpreta la tabla de `winget upgrade` (inglés o español) por posición de columnas."""
    lines = [l.rstrip() for l in re.split(r"[\r\n]+", text)]
    sep = next((i for i, l in enumerate(lines) if i > 0 and re.fullmatch(r"-{8,}", l.strip())), None)
    if sep is None:
        return []
    header = next((lines[j] for j in range(sep - 1, -1, -1) if lines[j].strip()), "")
    st = [m.start() for m in re.finditer(r"\S+", header)]
    if len(st) < 4:
        return []
    items = []
    for l in lines[sep + 1:]:
        if not l.strip() or len(l) <= st[1] or FOOTER.match(l):
            break
        name = l[:st[1]].strip()
        wid = l[st[1]:st[2]].strip()
        ver = l[st[2]:st[3]].strip()
        avail = l[st[3]:st[4] if len(st) > 4 else None].strip()
        src = l[st[4]:].strip() if len(st) > 4 else ""
        if not wid or " " in wid:
            continue
        truncated = wid.endswith("…") or wid.endswith("...")
        items.append({"id": wid, "name": name, "detail": f"{ver} → {avail}", "source": src,
                      "approved": wid in approved, "disabled": truncated,
                      "note": "ID truncado por winget" if truncated else ""})
    return items


def list_upgrades(winget: str, approved=()):
    r = subprocess.run([winget, "upgrade", "--accept-source-agreements", "--disable-interactivity"],
                       capture_output=True, timeout=300, creationflags=system.CREATE_NO_WINDOW)
    return parse_upgrade_table(r.stdout.decode("utf-8", errors="replace"), approved)


def upgrade_command(winget: str, item: dict):
    cmd = [winget, "upgrade", "--id", item["id"], "--exact", "--silent",
           "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity"]
    if item.get("source") in ("winget", "msstore"):
        cmd += ["--source", item["source"]]
    return cmd


class WingetUpgradeTask(Task):
    id = "winget"
    title = "Actualizar aplicaciones (winget)"
    description = ("Lista las apps con actualización disponible y actualiza en silencio solo las "
                   "que apruebes (winget upgrade -h). Puedes guardar la lista de aprobadas.")
    icon = "A"
    button = "Actualizar apps"

    def run(self, ctx: TaskContext) -> TaskResult:
        winget = find_winget()
        if not winget:
            return TaskResult(False, "No se encontró winget (Instalador de aplicaciones) en este equipo.")
        ctx.progress(None)
        ctx.status("Buscando actualizaciones disponibles…")
        approved = load_approved()
        try:
            items = list_upgrades(winget, approved)
        except Exception as e:
            return TaskResult(False, f"No se pudo consultar winget: {e}")
        if not items:
            return TaskResult(True, "Todas las aplicaciones están al día.")
        for it in items:
            ctx.log(f"Disponible: {it['name']} ({it['id']}) {it['detail']}", "dim")
        ctx.log(f"{len(items)} aplicaciones con actualización disponible.", "warn")
        if ctx.dry_run:
            return TaskResult(True, f"Simulación: {len(items)} aplicaciones por actualizar.",
                              "\n".join(f"{i['name']}  {i['detail']}" for i in items))

        pick = ctx.choose("Actualizar aplicaciones",
                          "Marca las aplicaciones que quieres actualizar. Las marcadas con ★ están en la "
                          "lista de aprobadas. Se actualizarán en silencio, una por una.",
                          items, preselected=[i["id"] for i in items if i["approved"]],
                          remember_label="Guardar esta selección como lista de aprobadas")
        if pick is None:
            return TaskResult(False, "Actualización cancelada por el usuario.")
        ids, remember = pick
        if remember:
            shown = {i["id"] for i in items}
            save_approved((approved - shown) | set(ids))
            ctx.log("Lista de aprobadas guardada.", "dim")
        chosen = [i for i in items if i["id"] in ids and not i["disabled"]]
        if not chosen:
            return TaskResult(True, "No se seleccionó ninguna aplicación.")

        ok_names, failed, n = [], [], len(chosen)
        for idx, it in enumerate(chosen):
            ctx.check_cancel()
            ctx.status(f"Actualizando {it['name']} ({idx + 1}/{n})")
            ctx.log(f"▶ {it['name']} ({it['id']})", "warn")

            def on_progress(pct, text, _i=idx, _name=it["name"]):
                ctx.progress((_i + pct / 100) / n * 100)
                ctx.status(f"Actualizando {_name} ({_i + 1}/{n}) — {pct:.0f} %")

            def on_text(text):
                if len(text) <= 1 or SIZE_LINE.match(text) or not re.search(r"\w", text):
                    return  # ruedita de espera / contador de MB: no llenar el log
                ctx.log("  " + text)

            rc = stream_process(upgrade_command(winget, it), ctx, on_progress=on_progress,
                                on_text=on_text, encoding="utf-8")
            code = rc & 0xFFFFFFFF
            if rc == 0 or code == NOT_APPLICABLE:
                ok_names.append(it["name"])
                ctx.log("✔ " + ("actualizada" if rc == 0 else "ya estaba al día"), "ok")
            else:
                failed.append(f"{it['name']} (0x{code:08X})")
                ctx.log(f"✘ falló (código 0x{code:08X}). ¿La app está abierta?", "err")
            ctx.progress((idx + 1) / n * 100)

        summary = f"Actualizadas {len(ok_names)} de {n} aplicaciones."
        if failed:
            return TaskResult(False, summary + f" Fallaron {len(failed)}.", "Fallidas:\n" + "\n".join(failed))
        return TaskResult(True, summary, "\n".join(ok_names))
