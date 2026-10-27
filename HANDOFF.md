# HANDOFF — leer primero si continúas este proyecto (otra IA o persona)

## Objetivo
Herramienta Python para Soporte (Girag/GIT) que haga mantenimiento preventivo en Windows 10/11 con botones y progreso visible. Debe verse como `G1r4g/Girag_GLPI_MobileForms` (paleta en `fynex/ui/theme.py`, copiada de las variables `:root` de su `index.html`; el logo del repo es oscuro y no se ve sobre el fondo, por eso se usa el texto "GIRAG" blanco como hace su `.mark-fallback`).

## Estado (v0.6.1)
Hecho y verificado **solo en Linux**: UI (captura con Xvfb), parseo de progreso, planificación de bloatware, helpers de registro, flujo completo de limpieza sobre carpeta temporal (12 pruebas OK).
**NO verificado en Windows real** — todo lo que llama a `DISM`, `sfc`, `compact`, `PowerShell Appx`, `winreg`, `reg export`, `Checkpoint-Computer`, servicios `wuauserv/bits` y la elevación UAC.

## Primer paso recomendado: validar en una VM Windows 10 y otra Windows 11
1. `python main.py` → ¿pide UAC y abre? ¿Badge "Administrador" en verde?
2. Activar **Modo simulación** y ejecutar las 5 tareas (no deben modificar nada).
3. Sin simulación, en la VM: limpieza → comprobar que la barra avanza y los MB liberados son coherentes.
4. DISM: ¿la barra sigue el `[=== 62.3% ===]`? ¿decodifica bien tildes (códepage OEM)? ¿SFC muestra su % (sale en UTF‑16, se eliminan los nulos)?
5. Bloatware: revisar lista detectada antes de confirmar; comprobar que Tienda/Calculadora siguen.
6. Registro: revisar `logs\registro_problemas_*.txt`, ejecutar, y probar restaurar un `.reg` de `backups\`.
7. Compact OS en VM con SSD; verificar texto de `/query` en español.
8. winget: ejecutar «Actualizar aplicaciones» con 2-3 apps pendientes; verificar lista, selección, ★ de aprobadas, progreso y que solo se toquen las marcadas.
9. Lote: marcar 3 tareas y ejecutar; comprobar orden, barra de lote, que un fallo no detiene las demás y que «Cancelar todo» funciona.
10. GLPI: con el agente instalado, ejecutar la tarea y comprobar en GLPI (Administración → Inventario) que el equipo aparece/actualiza su última fecha de contacto.

## Decisiones de diseño
- **Tkinter + stdlib** (sin pip) para que corra en cualquier equipo de soporte; `ttk` tema `clam` para colorear la barra.
- Cada tarea corre en un hilo y habla con la UI por `queue` (`core/context.py`): `log/status/progress/confirm`. La UI nunca bloquea.
- `core/process.py::stream_process` lee bytes y separa por `\r`/`\n` (DISM/SFC redibujan el % con `\r`). Líneas con `%` → progreso; resto → log.
- Barra DISM global ponderada (5/25/40/25/5). RestoreHealth se queda quieto en ~20 % y ~62 %: es normal.
- **Seguridad**: toda acción destructiva pide confirmación (`ctx.confirm`); `ctx.dry_run` evita cambios (DISM/SFC no se ven afectados por la simulación); limpieza no sigue enlaces/junctions (`collect()`); registro solo borra rutas **absolutas, sin variables sin resolver, que no existen** (`definitely_missing`) y omite MSI/SystemComponent; si falla el respaldo `.reg`, no borra.
- Bloatware: `protect` gana siempre sobre `remove`; se omiten paquetes `NonRemovable`, framework y firma `System`.

## Cambios v0.6.1 (correcciones reportadas por el usuario tras probar la v0.6.0 en Windows)
1. **La app se cerraba al intentar moverla.** Causa probable: `chrome.start_move` usaba `SendMessageW(WM_NCLBUTTONDOWN, HTCAPTION)`, que ejecuta el bucle modal de mover ventana *dentro* de la llamada ctypes (que suelta el GIL); mientras dura el arrastre Tcl sigue disparando `after` (`_poll`, `_tick`, animación de la barra) y reentra a Python con el GIL suelto → cierre del proceso sin mensaje (el .exe no tiene consola). Ahora se usa `PostMessageW` con la posición real del cursor (`GetCursorPos`): el mensaje lo procesa el bucle normal de Tk, como con una barra nativa, y se conserva Aero Snap. Si el envío falla, cae a mover con `geometry()`. **No reproducido aquí (Linux): validar en Windows.** `main.py` ahora activa `faulthandler` hacia `logs\fynex_crash.log` y `App.report_callback_exception` guarda errores de la interfaz en `logs\fynex_ui_errors.log` (antes se perdían sin consola).
2. **Barras naranjas parpadeando al esperar una aprobación.** La barra de progreso seguía en modo indeterminado (animada cada 30 ms) mientras el diálogo esperaba. Ahora `App._ask/_wait_user` detienen la animación, muestran «en espera» y «Esperando tu aprobación…» y restauran el estado al cerrar el diálogo. `RoundedProgress` ya no borra y recrea todo el canvas en cada cuadro (reutiliza dos elementos y solo cambia coordenadas). Los diálogos se traen al frente con foco (`_bring_front`) para que Windows no haga parpadear el botón de la barra de tareas. Si el parpadeo que ve el usuario es solo el del botón de la barra de tareas y persiste, el siguiente paso es `FlashWindowEx(FLASHW_STOP)`.
3. **Una sola aprobación por lote.** `_run_selected` con 2+ tareas muestra un único diálogo «Aprobar el lote» y ejecuta con `auto_approve=True` (`run_tasks` → `TaskContext`): `ctx.confirm()` devuelve True sin diálogo (deja «✔ Aprobado en el lote: …» en pantalla y el detalle completo solo en el archivo de log); `ctx.choose()` usa las preseleccionadas (winget: lista de aprobadas ★) sin diálogo y **sí pregunta** si no hay ninguna aprobada. Con una sola tarea se conserva su propia confirmación (una sola aprobación). Cancelar sigue funcionando; las protecciones internas de cada tarea (listas protegidas, respaldo .reg, punto de restauración, modo simulación) no cambian. Contrapartida a tener presente: en lote ya no se ve la lista detectada (bloatware, tamaño de limpieza, entradas de registro) *antes* de ejecutar; queda en consola y log.
- Pruebas nuevas: `tests/test_approval.py` (10). Pruebas de UI con Tk no se pudieron correr en este entorno (sin tkinter): `test_brand`/`test_widgets` deben correrse en una máquina con Tk.

## Cambios v0.6.0 (marca Fynex y ventana propia)
- **Rebranding Girag → Fynex:** paquete `girag_maint` → `fynex` (`git mv`, imports y pruebas actualizados), `APP_NAME`, textos de la UI, estilos ttk, User-Agent, punto de restauración («Fynex Mantenimiento»), exe `Fynex.exe`. Se conservó lo que es de la empresa: el servidor `soporte.girag.com` (GLPI) y la atribución de la paleta a Girag_GLPI_MobileForms. La paleta de colores no cambió.
- **Carpeta de datos:** `C:\ProgramData\Fynex` con migración automática desde `GiragMaintenance` (`system.data_dir()`); si no puede renombrar, sigue usando la anterior. Pruebas en `tests/test_brand.py`.
- **Logo:** `tools/make_assets.py` (requiere Pillow solo para regenerar) crea `assets/fynex_mark_{16,24,36,54,72,108}.png`, `fynex_icon_256.png` y `fynex.ico` (una imagen por tamaño; a ≤28 px la marca se simplifica a menos brazos para que se lea). Es una marca propia inspirada en la referencia del usuario (remolino de arcos blancos afilados); la imagen de referencia traía marca de agua de «turbologo», por eso no se usó tal cual. **Si el usuario aporta el logo oficial limpio, reemplazar los PNG/ICO conservando nombres y tamaños.** `ui/assets.py` elige el tamaño según la escala de pantalla.
- **Barra de título propia (`ui/chrome.py`):** en Windows quita `WS_CAPTION` y deja `WS_THICKFRAME` (conserva botón en la barra de tareas, Aero Snap y redimensionado); mover = `WM_NCLBUTTONDOWN/HTCAPTION`; doble clic detectado a mano (el nativo maximizaría sobre la barra de tareas); maximizar = ajustar al área de trabajo del monitor compensando los bordes invisibles (`DwmGetWindowAttribute` EXTENDED_FRAME_BOUNDS); Win+↑ se convierte a nuestro maximizado; esquinas redondeadas y color de borde en Windows 11 (DWM). Si `apply()` falla se oculta la barra propia y queda la nativa. Salidas de emergencia: `--native-titlebar` o `FYNEX_NATIVE_TITLEBAR=1`. `FYNEX_CUSTOM_BAR=1` fuerza la barra propia en Linux (solo para pruebas con xvfb).
- `main.py` fija el AppUserModelID `Fynex.Maintenance` (icono y agrupación propios en la barra de tareas). `build.bat`/`build_onedir.bat` usan `--icon assets\fynex.ico` y empaquetan `assets/`.
- **Por validar en Windows (no se pudo probar aquí):** (1) que quitar `WS_CAPTION` en la ventana de Tk deje el botón en la barra de tareas, mover, snap y redimensionar; (2) maximizar/restaurar sin cubrir la barra de tareas ni dejar huecos de 7-8 px (monitores con escalado 125-200 % y varios monitores); (3) minimizar y restaurar desde la barra de tareas; (4) icono del .exe y de la barra de tareas; (5) esquinas/borde en Windows 10 y 11. Las pruebas de ventana corren con `xvfb-run` en Linux (81 pruebas; sin pantalla se omiten 3).

## Cambios v0.5.1 (correcciones a partir de los logs del usuario, 16-17/10/2026)
- Repositorio del proyecto: https://github.com/juanstm200/Fynex (rama main). Su contenido era idéntico a la v0.5.0 más un `test.txt` ("hola mundo") sin uso.
- **Causa raíz de los errores** (`No such file ... _MEI000021582\base_library.zip` y `...\config\bloatware.json`): el .exe de PyInstaller en modo un solo archivo se extrae a `%TEMP%\_MEIxxxx` y corre desde ahí; la tarea de limpieza (que borra el Temp del usuario) eliminó los archivos no bloqueados de esa carpeta (liberó 16 GB a las 13:39). Después fallaron todas las tareas que cargan algo «en el momento»: DISM y Compact OS (códec `oem`), bloatware (JSON), GLPI (códec `idna` de urllib) y el diagnóstico de disco. Winget funcionó porque usaba el códec `utf-8`, ya cargado.
- **Correcciones:** (1) `cleanup.collect` omite `_MEI*` y las rutas de `system.protected_roots()` (`sys._MEIPASS`, carpeta del .exe, datos de la app) y el log lo informa; (2) `core/runtime_guard.py::warm_up()` precarga códecs/urllib/ssl/etc. y las configuraciones al arrancar; (3) `system.load_json_config` lee `config/*.json` con copia en memoria y permite sobrescribir en `C:\ProgramData\GiragMaintenance\config\`; (4) `system.runtime_ok()` evita ejecutar tareas si el entorno temporal ya fue borrado y muestra un mensaje claro en vez de trazas; (5) `build_onedir.bat` como alternativa que no depende de `%TEMP%`.
- **Registro:** `definitely_missing(..., wow32=True)` trata `system32` como `SysWOW64` en claves `WOW6432Node` (el log marcó como huérfanas `msvcr110/msvcp110/vccorlib110.dll`, que son de 32 bits y estaban en SysWOW64; se eliminaron 3 valores de SharedDLLs, con respaldo en `ProgramData\GiragMaintenance\backups\registro_20261016_134114`). También se corrigió un fallo de lógica que ignoraba `InstallLocation` en «Programas desinstalados» y se agregó un bloqueo si el programa corre como 32 bits sobre Windows de 64.
- UI: los diagnósticos que terminan con error ya no dicen «listo» en verde; se limpia el reloj de la tarea anterior; el log de la limpieza agrupa una línea por etiqueta (antes una por carpeta).
- Pruebas nuevas: `tests/test_runtime.py` (reproduce el incidente: falla con el código anterior). Total 67.
- Pendiente de verificar en Windows con el .exe reconstruido: ejecutar limpieza + DISM + bloatware + GLPI + diagnóstico en un mismo lote.

## Cambios v0.5.0 (interfaz redondeada y GLPI forzado)
- **Base:** se partió del zip v0.4.0 que aportó el usuario (paquete `diag/` con Equipo/Disco/Batería). Un borrador paralelo de diagnóstico hecho antes en otra sesión se descartó para no duplicar.
- **UI redondeada:** nuevo `ui/widgets.py` con `RoundedPanel` (tarjetas, panel de estado, consola, diálogos), `RoundedButton` y `RoundedProgress`. Las esquinas son PNG opacos con antialias, ya mezclados con el color del contenedor (no dependen de transparencia); se generan en Python puro (`corner_pixels`, con pruebas). `_button()` en `ui/app.py` ahora devuelve `RoundedButton` (misma API: `configure(text/state/fg)`). Cuidado: en widgets de Tk no usar `_w` como atributo (es el nombre interno) y los `bind("<Configure>")` sobre un `RoundedPanel` deben usar `add="+"` o se pierde el redibujado.
- **GLPI forzado:** `tasks/glpi_sync.py` reescrito. Método principal «service»: `GET http://127.0.0.1:<puerto>/now` al servicio instalado (usa puerto de `HKLM\SOFTWARE\GLPI-Agent\httpd-port` si existe), sondea `/status` (running→waiting) y lee solo las líneas nuevas de `...\GLPI-Agent\logs\glpi-agent.log`. Respaldo «cli»: `glpi-agent.bat --force --tasks=inventory --server=<URL>`. En `auto` se usa solo el CLI si el agente está configurado con otro servidor (clave `server` del registro ≠ URL de Girag). Si el servicio está detenido intenta `net start glpi-agent`. Pruebas con un agente HTTP falso en `tests/test_glpi.py`.
- **Por validar en Windows con el agente real:** (1) que `/now` por GET dispare el inventario (el código reintenta con POST si recibe 405) y que `127.0.0.1` esté en `httpd-trust` (si no, cae solo al CLI); (2) el texto exacto de `/status` («status: waiting» / «running task …»); (3) la ruta real del log del agente (`log_paths` en `config/glpi.json`); (4) que el servidor registre la fecha de último contacto del equipo en GLPI.

## Cambios v0.4.0 (diagnóstico de equipo, disco y batería)
- Paquete `fynex/diag/`: `sysinfo.py`, `disk.py`, `battery.py`, `common.py`. Tres botones en el encabezado (UI: `App._start_diag`, hilo aparte, salida a la consola por la cola de eventos; bloquea tareas mientras corre y limpia la consola antes de imprimir).
- PowerShell se invoca con `-EncodedCommand` (sin problemas de comillas) y devuelve JSON; la lógica de parseo/concepto es pura y tiene pruebas (`tests/test_diag.py`).
- Disco: NVMe vía `IOCTL_STORAGE_QUERY_PROPERTY` (ctypes, log 02h) · SATA vía `MSStorageDriver_ATAPISmartData` (atributos 231/233/177/202/169 = vida, 241/242 = escritos/leídos ×512 B, Intel ×32 MiB) · respaldo: `Get-StorageReliabilityCounter`. Umbrales en `classify_disk`.
- Batería: `root\wmi` (BatteryStaticData/FullChargedCapacity/CycleCount) completado con `powercfg /batteryreport /xml`. Umbrales en `classify_battery`.
- **Por validar en Windows real:** (1) IOCTL NVMe en distintos controladores (Intel RST/VMD puede fallar → cae a contadores); (2) unidades de 241/242 según fabricante SATA (marcado «aprox.»); (3) `Get-BitLockerVolume` en Windows Home (cae a CIM); (4) ciclos de batería que algunos equipos reportan como 0; (5) aspecto de los 3 botones a 1040 px de ancho mínimo.

## Cambios v0.2.0
- Bloatware: se retiraron de `remove` Office Hub, Sway y Clipchamp; `protect` ahora blinda explícitamente Calculadora, Fotos, ZuneMusic/ZuneVideo (Reproductor/Películas y TV), MediaPlayer, Clipchamp, extensiones de video/imagen y todo lo `*Office*`. Decisión de Soporte; hay pruebas que lo garantizan (`ProtectedAppsTests`). Apps de streaming de terceros (Netflix, Disney, Prime Video, Hulu) siguen en `remove`: quitar de la lista si también deben conservarse.
- Nueva tarea `tasks/glpi_sync.py` (tarjeta "Sincronizar con GLPI"): busca el agente, hace GET al servidor (cualquier respuesta HTTP = alcanzable), y ejecuta `glpi-agent.bat --force --server=<URL>` en primer plano mostrando su salida. Progreso = estimación (`SyncProgress`) porque el agente no emite %. Éxito = código 0 y ninguna línea `[error]`/`failed`/`can't connect`. Config en `config/glpi.json`.
- UI: columna de tareas desplazable (con rueda del mouse) para pantallas pequeñas.
- **Por validar en Windows:** ruta real del agente, que `--force --server=` envíe al plugin `glpiinventory`, los textos de log que disparan los hitos de progreso, y el quoting de la ruta con espacios al invocar el `.bat`. Alternativa si falla: pedir inventario al servicio local con `http://127.0.0.1:62354/now`.
- El repo de GitHub recibió solo los archivos de la raíz (faltaban `fynex/`, `config/`, `tests/`): verificar que se subieron completos.

## Cambios v0.3.0
- La app ya fue abierta por el usuario en Windows 11 (build 26300) como administrador: la UI y la elevación UAC funcionan. Las tareas siguen sin validación de extremo a extremo.
- **Ejecución en lote:** casilla por tarjeta (`CheckBox` propio ☐/☑ naranja) + botón «Ejecutar seleccionadas (N)». Lógica en `core/runner.py::run_tasks` (sin Tk, con pruebas): orden = orden de `ALL_TASKS`, continúa tras fallos, `Cancelar todo` detiene el resto. Eventos nuevos: `begin/end/done`. Cada tarea mantiene sus confirmaciones (decisión de seguridad). Idea pendiente: modo desatendido.
- **Nueva tarea `tasks/winget_upgrade.py`:** `parse_upgrade_table` interpreta `winget upgrade` por posición de columnas (EN/ES); diálogo `ChooseDialog` vía `ctx.choose()`; actualiza por `--id --exact --silent --accept-*-agreements --disable-interactivity` (+`--source` winget/msstore). Código 0x8A15002B = ya al día (no es error). Lista de aprobadas en `%ProgramData%\GiragMaintenance\winget_approved.json`.
- **Por validar en Windows:** (1) que la tabla de winget se lea bien con la salida redirigida y la codificación UTF-8; (2) IDs truncados con «…» (se muestran deshabilitados; si es frecuente, resolver por nombre o ensanchar la salida); (3) apps abiertas que impiden actualizar (error reportado por app); (4) comportamiento de winget cuando se ejecuta elevado (paquetes msstore/por usuario).
- La sincronización con GLPI quedó como tarea 7 (última del orden) para reportar el estado final del equipo.

## Limitaciones / riesgos conocidos
- Caché de Teams solo "clásico"; Teams nuevo no incluido.
- La papelera de reciclaje **no** se vacía (puede tener datos del usuario).
- Registro: no repara asociaciones de archivo, ActiveX/COM, fuentes, rutas de ayuda (sí hace Glary: pendiente).
- `Remove-AppxPackage -AllUsers` requiere PS 5.1 y admin; algunas apps pueden fallar y se reportan como fallidas.
- Compact OS en HDD puede ser contraproducente (advertido en el diálogo).
- Sin firma de código: SmartScreen/antivirus pueden avisar con el `.exe`.

## Roadmap sugerido
1. Validación en Windows real y corrección de lo que aparezca.
2. Botón **"Mantenimiento completo"** que encadene las 5 tareas con un resumen final.
3. Reporte exportable (HTML/PDF/CSV) por equipo; integración opcional con GLPI (crear ticket/seguimiento con el resultado) reutilizando la API del proyecto Girag_GLPI_MobileForms.
4. Opción de restaurar respaldos de registro desde la propia app.
5. Más cachés (Teams nuevo, Zoom, Slack, Adobe) y vaciado opcional de papelera.
6. Ícono propio, versión en el `.exe`, firma de código.
7. Registro en Windows Event Log / ejecución silenciosa por línea de comandos (`--task cleanup --silent`) para uso con GPO/Intune.

## Convenciones
- Código y textos de UI en español; comentarios cortos.
- Nueva tarea: heredar de `tasks/base.py::Task`, implementar `run(ctx)->TaskResult`, registrar en `tasks/__init__.py`.
- Probar lógica pura en `tests/` (no depender de Windows); usar `unittest`.
