# Girag_WinMaintenanceTool

Aplicación de escritorio en **Python (Tkinter, sin dependencias externas)** para que el equipo de Soporte de Girag realice **mantenimiento preventivo en equipos con Windows 10 y 11**. Interfaz y colores tomados de [Girag_GLPI_MobileForms](https://github.com/G1r4g/Girag_GLPI_MobileForms) (fondo `#0c1526`, tarjetas `#141f36`, acento naranja `#e8823f`).

> **Estado:** v0.5.1 — prototipo funcional. Lógica y UI probadas en Linux (pruebas unitarias + captura con Xvfb). **Aún NO probado en un Windows real**: ver `HANDOFF.md` antes de usarlo en producción.

## Funciones

| # | Botón | Qué hace |
|---|-------|----------|
| 1 | **Limpiar caché y temporales** | Escanea y borra temporales de Windows y de cada perfil de usuario, descargas de Windows Update, miniaturas, informes de error (WER), volcados y caché de Chrome, Edge, Firefox, Teams (clásico) y Office. Barra de progreso por archivos y total liberado. |
| 2 | **Reparar Windows (DISM + SFC)** | `DISM /CheckHealth` → `/ScanHealth` → `/RestoreHealth` → `sfc /scannow` → `/StartComponentCleanup`. Barra global ponderada y resultado por paso. |
| 3 | **Eliminar bloatware** | Quita apps Appx preinstaladas (lista editable en `config/bloatware.json`) para todos los usuarios y del perfil base. **Nunca** elimina Calculadora, Fotos, Reproductor multimedia/Películas y TV, Clipchamp ni Office (lista `protect`, que siempre gana). |
| 4 | **Reparar registro** | Estilo Glary Utilities: detecta entradas huérfanas (programas desinstalados, App Paths, SharedDLLs, inicio, MUICache). Antes de borrar crea **punto de restauración** y **respaldo .reg**; si el respaldo falla, no borra nada. |
| 5 | **Compact OS** | `compact /CompactOS:always` con estado antes/después y espacio recuperado. |
| 6 | **Actualizar aplicaciones (winget)** | Lista las apps con actualización (`winget upgrade`) y muestra una ventana para marcar cuáles actualizar; cada una se actualiza en silencio con `winget upgrade --id <id> --exact --silent` (equivale a `-h --all` pero solo para las elegidas). Puede guardar la selección como **lista de aprobadas** (`%ProgramData%\GiragMaintenance\winget_approved.json`, semilla en `config/winget_approved.json`). Las aprobadas salen premarcadas con ★. |
| 7 | **Sincronizar con GLPI** | **Fuerza** un inventario inmediato con el GLPI Agent instalado: le ordena al servicio del agente «Forzar inventario» (como su página local `http://127.0.0.1:62354/now`), sigue su estado (ejecutando → en espera) y su log hasta que termina. Si el servicio no responde o el agente apunta a otro servidor, usa `glpi-agent.bat --force --tasks=inventory --server=<URL de Girag>`. Servidor y método en `config/glpi.json` (por defecto `https://soporte.girag.com/plugins/glpiinventory`, `method: auto`). El agente no informa porcentaje: la barra es una estimación. |

**Diagnóstico (solo lectura)** — tres botones en el encabezado, junto al título; el resultado sale en la consola de la derecha:

| Botón | Muestra |
|-------|---------|
| **Equipo** | Hostname, dominio, fabricante/modelo/serial, SO y versión (24H2, build), procesador, RAM (módulos/tipo/MHz), tipo de licencia de Windows y estado, BIOS (versión/fecha), UEFI/Secure Boot, cifrado de disco (BitLocker) y versión del TPM. |
| **Disco** | Por cada disco: fabricante, serial, firmware, vida útil restante %, datos escritos/leídos, horas, temperatura, alertas SMART y un **concepto**: FUNCIONAL ≥70 % · MEDIO FUNCIONAL 40–69 % · NO FUNCIONAL 15–39 % · CRÍTICO <15 % (los errores SMART suben el estado). NVMe se lee con el log de salud 02h; SATA con SMART ATA vía WMI; si no hay SMART se usan los contadores de Windows. |
| **Batería** | Fabricante, serial, salud % (capacidad actual / diseño), Wh de diseño y actuales, ciclos, carga y **concepto**: FUNCIONAL ≥80 % · MEDIO 60–79 % · NO FUNCIONAL 40–59 % · CRÍTICO <40 %; los ciclos (≥400 / ≥700 / ≥1000) pueden subir el estado. Si el equipo no tiene batería lo indica. |

**Ejecutar varias tareas:** marca las casillas y pulsa **▶ Ejecutar seleccionadas** (arriba de la lista). Se ejecutan una tras otra en el orden de la lista (limpieza → DISM → bloatware → registro → Compact OS → winget → GLPI), con barra por tarea y barra de lote. Una tarea que falla no detiene las demás; **Cancelar todo** detiene el lote. Cada tarea sigue pidiendo confirmación antes de modificar algo.

Extras: **modo simulación** (no modifica nada), confirmación antes de cualquier cambio, botón **Cancelar**, tiempo transcurrido y log en pantalla + archivo.

## Requisitos
- Para la tarea 6: **GLPI Agent** instalado (`C:\Program Files\GLPI-Agent`).
- Windows 10/11, **Python 3.10+** (Tkinter viene incluido en el instalador oficial).
- Ejecutar como **administrador** (la app lo solicita sola por UAC).

## Ejecutar
```bat
python main.py
```
Para desarrollar sin elevar: `python main.py --no-elevate`.

## Generar el .exe
```bat
build.bat
```
Crea `dist\GiragMantenimiento.exe` (PyInstaller, pide administrador al abrir).

## Pruebas
```bash
python -m unittest discover -s tests
```
(Corren en cualquier sistema; cubren parseo de progreso, planificador de bloatware, ayudantes del registro y el flujo de limpieza.)

## Personalizar listas (bloatware, GLPI, winget)
Los archivos de `config\` van **dentro** del .exe y se extraen a una carpeta temporal en cada ejecución, así que editarlos allí no sirve. Para personalizar: copia el archivo (por ejemplo `config\bloatware.json`) a `C:\ProgramData\GiragMaintenance\config\` y edítalo ahí; **tiene prioridad** sobre el incluido.

## Dónde guarda cosas
- Logs: `C:\ProgramData\GiragMaintenance\logs\`
- Respaldos del registro: `C:\ProgramData\GiragMaintenance\backups\registro_<fecha>\` (doble clic en un `.reg` para restaurar).

## Estructura
```
main.py                      # arranque + elevación UAC
config/bloatware.json        # lista editable remove / protect / optional
config/glpi.json             # URL del servidor GLPI y rutas del agente
config/winget_approved.json  # lista inicial de IDs de winget aprobados
girag_maint/
  core/   system.py context.py process.py   # admin, rutas, cola de eventos, ejecución de comandos
  tasks/  cleanup.py dism_repair.py bloatware.py registry_repair.py compact_os.py winget_upgrade.py glpi_sync.py
  core/   ... runner.py (ejecución en lote)
  diag/   sysinfo.py disk.py battery.py common.py   # botones Equipo / Disco / Batería (solo lectura)
  ui/     theme.py app.py                    # paleta Girag + ventana Tkinter
tests/test_core.py
HANDOFF.md                   # estado, decisiones y pendientes (leer primero si continúas el trabajo)
```
Para añadir una tarea: crear clase que herede de `tasks/base.py::Task`, implementar `run(ctx)` y registrarla en `tasks/__init__.py`. La UI genera la tarjeta sola.

## Subir a GitHub
> Importante: sube **todo el contenido** del proyecto, incluidas las carpetas `girag_maint/`, `config/` y `tests/` (en la web de GitHub, arrastrar archivos sueltos no sube carpetas). Lo más fiable es usar git:

```bash
git remote add origin https://github.com/juanstm200/Fynex.git
git push -u origin main
```
