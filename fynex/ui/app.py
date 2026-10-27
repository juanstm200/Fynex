"""Interfaz Tkinter de Fynex (tema oscuro azul marino + acento naranja)."""
import os
import queue
import threading
import time
import tkinter as tk
import traceback
from tkinter import messagebox, ttk

from .. import APP_NAME, __version__
from ..core import system
from ..core.runner import run_tasks
from ..diag import battery, disk, sysinfo
from ..tasks import ALL_TASKS
from . import assets
from . import theme as T
from .chrome import TitleBar, WindowChrome
from .widgets import RoundedButton, RoundedPanel, RoundedProgress

LOG_COLORS = {"info": T.INK, "dim": T.INK_SOFT, "ok": T.OK, "err": T.ERR, "warn": T.ACCENT}


def _font(size=10, weight="normal"):
    return (T.FONT, size, weight)


def _button(parent, text, command, primary=False, **kw):
    """Botón redondeado (primario naranja o secundario)."""
    kw.setdefault("font", _font(10, "bold") if primary else _font(9, "bold"))
    kw.setdefault("padx", 18 if primary else 14)
    kw.setdefault("pady", 7)
    extra = {}
    for k in ("fg", "bg"):
        if k in kw:
            extra[k] = kw.pop(k)
    if "activeforeground" in kw:
        extra["hover_fg"] = kw.pop("activeforeground")
    return RoundedButton(parent, text, command, style="primary" if primary else "ghost", **kw, **extra)


class CheckBox(tk.Label):
    """Casilla grande y legible: ☐ gris / ☑ naranja Fynex."""

    def __init__(self, parent, variable, bg, size=17):
        super().__init__(parent, bg=bg, cursor="hand2", font=("Segoe UI Symbol", size))
        self.var, self._enabled = variable, True
        variable.trace_add("write", lambda *_: self._paint())
        self.bind("<Button-1>", self._toggle)
        self._paint()

    def _paint(self):
        on = self.var.get()
        self.configure(text="\u2611" if on else "\u2610",
                       fg=(T.ACCENT if on else T.INK_SOFT) if self._enabled else T.INK_MUTED)

    def _toggle(self, _e=None):
        if self._enabled:
            self.var.set(not self.var.get())

    def set_enabled(self, enabled):
        self._enabled = enabled
        self.configure(cursor="hand2" if enabled else "arrow")
        self._paint()


def _center(win, parent, third=True):
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_reqheight()) // (3 if third else 2)
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")


def _scrollable(parent):
    """Canvas desplazable con rueda del mouse. Devuelve (contenedor, frame_interno, canvas)."""
    wrap = tk.Frame(parent, bg=T.PAPER)
    canvas = tk.Canvas(wrap, bg=T.PAPER, highlightthickness=0, bd=0)
    vsb = ttk.Scrollbar(wrap, style="Fynex.Vertical.TScrollbar", orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    canvas.pack(side="left", fill="both", expand=True)
    inner = tk.Frame(canvas, bg=T.PAPER)
    win = canvas.create_window((0, 0), window=inner, anchor="nw")

    def refresh(_e=None):
        canvas.configure(scrollregion=canvas.bbox("all"))
        if inner.winfo_reqheight() > canvas.winfo_height() + 1:
            if not vsb.winfo_ismapped():
                vsb.pack(side="right", fill="y", before=canvas)
        else:
            vsb.pack_forget()
            canvas.yview_moveto(0)

    inner.bind("<Configure>", refresh)
    canvas.bind("<Configure>", lambda e: (canvas.itemconfigure(win, width=e.width), refresh()))

    def wheel(e):
        if inner.winfo_reqheight() > canvas.winfo_height():
            canvas.yview_scroll((-1 if (e.num == 4 or e.delta > 0) else 1) * 2, "units")

    events = ("<MouseWheel>", "<Button-4>", "<Button-5>")
    canvas.bind("<Enter>", lambda e: [canvas.bind_all(x, wheel) for x in events])
    canvas.bind("<Leave>", lambda e: [canvas.unbind_all(x) for x in events])
    return wrap, inner, canvas


def _bring_front(win):
    """Lleva el diálogo al frente con foco: si queda detrás, Windows hace parpadear el botón de la barra de tareas."""
    def go():
        try:
            win.lift()
            win.focus_force()
        except tk.TclError:
            pass
    win.after(30, go)


class ConfirmDialog(tk.Toplevel):
    def __init__(self, parent, title, message, details="", ok_text="Continuar"):
        super().__init__(parent, bg=T.PAPER)
        self.result = False
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        panel = RoundedPanel(self, fill=T.CARD, border=T.LINE, radius=16, parent_bg=T.PAPER)
        panel.pack(padx=16, pady=16, fill="both", expand=True)
        body = panel.body
        tk.Label(body, text=title.upper(), bg=T.CARD, fg=T.ACCENT, font=_font(9, "bold")).pack(anchor="w", padx=18, pady=(16, 4))
        tk.Label(body, text=message, bg=T.CARD, fg=T.INK, font=_font(10), justify="left",
                 wraplength=520).pack(anchor="w", padx=18, pady=(0, 10))
        if details:
            box = tk.Text(body, height=min(12, details.count("\n") + 2), width=70, bg=T.PAPER, fg=T.INK_SOFT,
                          relief="flat", font=("Consolas", 9), highlightthickness=1, highlightbackground=T.LINE)
            box.insert("1.0", details)
            box.configure(state="disabled")
            box.pack(padx=18, pady=(0, 10), fill="x")
        row = tk.Frame(body, bg=T.CARD)
        row.pack(fill="x", padx=18, pady=(4, 16))
        _button(row, ok_text, lambda: self._close(True), primary=True).pack(side="right")
        _button(row, "Cancelar", lambda: self._close(False), pady=7).pack(side="right", padx=(0, 10))
        self.protocol("WM_DELETE_WINDOW", lambda: self._close(False))
        _center(self, parent)
        self.grab_set()
        self.focus_set()
        _bring_front(self)

    def _close(self, value):
        self.result = value
        self.destroy()


class ChooseDialog(tk.Toplevel):
    """Lista con casillas (p. ej. apps a actualizar). result = (ids, recordar) o None."""

    def __init__(self, parent, title, message, items, preselected, remember_label=""):
        super().__init__(parent, bg=T.PAPER)
        self.result = None
        self.items = items
        self.title(title)
        self.transient(parent)
        self.geometry("780x580")
        self.minsize(640, 420)
        panel = RoundedPanel(self, fill=T.CARD, border=T.LINE, radius=16, parent_bg=T.PAPER)
        panel.pack(fill="both", expand=True, padx=16, pady=16)
        body = panel.body
        tk.Label(body, text=title.upper(), bg=T.CARD, fg=T.ACCENT, font=_font(9, "bold")).pack(anchor="w", padx=18, pady=(16, 4))
        tk.Label(body, text=message, bg=T.CARD, fg=T.INK, font=_font(10), justify="left",
                 wraplength=700).pack(anchor="w", padx=18, pady=(0, 10))
        bar = tk.Frame(body, bg=T.CARD)
        bar.pack(fill="x", padx=18, pady=(0, 8))
        _button(bar, "Marcar aprobadas ★", self._approved).pack(side="left")
        _button(bar, "Todas", lambda: self._set_all(True)).pack(side="left", padx=6)
        _button(bar, "Ninguna", lambda: self._set_all(False)).pack(side="left")
        self.count = tk.Label(bar, bg=T.CARD, fg=T.INK_SOFT, font=_font(9, "bold"))
        self.count.pack(side="right")

        wrap, inner, _canvas = _scrollable(body)
        wrap.configure(highlightthickness=1, highlightbackground=T.LINE)
        wrap.pack(fill="both", expand=True, padx=18)
        self.vars = {}
        for it in items:
            var = tk.BooleanVar(value=(it["id"] in preselected) and not it.get("disabled"))
            var.trace_add("write", lambda *_: self._count())
            self.vars[it["id"]] = var
            row = tk.Frame(inner, bg=T.PAPER)
            row.pack(fill="x", padx=8, pady=2)
            cb = CheckBox(row, var, T.PAPER, size=15)
            cb.set_enabled(not it.get("disabled"))
            cb.pack(side="left", padx=(0, 4))
            tk.Label(row, text=("★ " if it.get("approved") else "") + it["name"], bg=T.PAPER, fg=T.INK,
                     font=_font(10, "bold"), anchor="w").pack(side="left")
            tk.Label(row, text="   " + it.get("detail", "") + (f"   ({it['note']})" if it.get("note") else ""),
                     bg=T.PAPER, fg=T.INK_SOFT, font=_font(9)).pack(side="left")
            tk.Label(row, text=it["id"], bg=T.PAPER, fg=T.INK_MUTED, font=_font(8)).pack(side="right", padx=8)

        self.remember = tk.BooleanVar(value=False)
        if remember_label:
            rem = tk.Frame(body, bg=T.CARD)
            rem.pack(anchor="w", padx=18, pady=(10, 0))
            CheckBox(rem, self.remember, T.CARD, size=14).pack(side="left")
            tk.Label(rem, text=" " + remember_label, bg=T.CARD, fg=T.INK_SOFT, font=_font(9)).pack(side="left")
        row = tk.Frame(body, bg=T.CARD)
        row.pack(fill="x", padx=18, pady=(10, 16))
        _button(row, "Actualizar seleccionadas", self._ok, primary=True).pack(side="right")
        _button(row, "Cancelar", self.destroy).pack(side="right", padx=(0, 10))
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._count()
        _center(self, parent, third=False)
        self.grab_set()
        self.focus_set()
        _bring_front(self)

    def _count(self):
        self.count.configure(text=f"{sum(v.get() for v in self.vars.values())} de {len(self.vars)} seleccionadas")

    def _set_all(self, value):
        for it in self.items:
            if not it.get("disabled"):
                self.vars[it["id"]].set(value)

    def _approved(self):
        for it in self.items:
            if not it.get("disabled"):
                self.vars[it["id"]].set(bool(it.get("approved")))

    def _ok(self):
        self.result = ([i for i, v in self.vars.items() if v.get()], self.remember.get())
        self.destroy()


class TaskCard(RoundedPanel):
    def __init__(self, parent, task, on_run, on_toggle):
        super().__init__(parent, fill=T.CARD, border=T.LINE, radius=14, parent_bg=T.PAPER)
        b = self.body
        self.task = task
        self.var = tk.BooleanVar(value=False)
        self.var.trace_add("write", lambda *_: on_toggle())
        icon = RoundedPanel(b, fill=T.CARD_2, border=T.CARD_2, radius=10, parent_bg=T.CARD)
        icon.grid(row=0, column=0, rowspan=2, padx=(6, 12), pady=5, sticky="n")
        tk.Label(icon.body, text=task.icon, bg=T.CARD_2, fg=T.ACCENT, font=_font(14, "bold"),
                 width=3).pack(ipady=4)
        self.title_lbl = tk.Label(b, text=task.title, bg=T.CARD, fg=T.INK, font=_font(11, "bold"),
                                  anchor="w", justify="left")
        self.title_lbl.grid(row=0, column=1, sticky="we", pady=(5, 0))
        self.desc = tk.Label(b, text=task.description, bg=T.CARD, fg=T.INK_SOFT, font=_font(9),
                             anchor="w", justify="left", wraplength=300)
        self.desc.grid(row=1, column=1, sticky="we", pady=(2, 5))
        self.chk = CheckBox(b, self.var, T.CARD)
        self.chk.grid(row=0, column=2, rowspan=2, padx=(6, 4))
        self.btn = _button(b, task.button, lambda: on_run(task), primary=True, font=_font(9, "bold"),
                           pady=8, width_px=150)
        self.btn.grid(row=0, column=3, rowspan=2, padx=(4, 8))
        b.columnconfigure(1, weight=1)
        self.bind("<Configure>", self._wrap, add="+")

    def _wrap(self, event):
        w = max(150, event.width - 300)
        self.desc.configure(wraplength=w)
        self.title_lbl.configure(wraplength=w)

    def set_enabled(self, enabled):
        self.btn.configure(state="normal" if enabled else "disabled")
        self.chk.set_enabled(enabled)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.configure(bg=T.PAPER)
        self.geometry("1200x900")
        self.minsize(1040, 600)
        self.events: "queue.Queue" = queue.Queue()
        self.cancel_event = threading.Event()
        self.ctx = None
        self.busy = False
        self.started = 0.0
        self.total = 1
        self.idx = 0
        self.pb_mode = "determinate"
        self._last_pct = None             # último progreso real (para restaurarlo tras esperar una aprobación)
        self._prev_status = ""
        self.dry_run = tk.BooleanVar(value=False)
        self.cards = []
        self.diag_busy = False
        self.diag_btns = []
        self._style()
        assets.set_window_icon(self)
        self.chrome = WindowChrome(self, self._on_close)
        self._build_titlebar()
        self._build_header()
        self._build_body()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.chrome.enabled:
            self.bind("<Configure>", self.chrome.on_configure, add="+")
            self.update_idletasks()
            for ms in (30, 400):         # se reaplica por si Tk repone el marco al mostrar la ventana
                self.after(ms, self._apply_chrome)
        self.after(80, self._poll)
        self.after(500, self._tick)
        self._log(f"{system.windows_version()} · {'Administrador' if system.is_admin() else 'SIN permisos de administrador'}", "dim")

    # ---------- construcción ----------
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("Fynex.Horizontal.TProgressbar", troughcolor=T.CARD_2, background=T.ACCENT,
                    bordercolor=T.LINE, lightcolor=T.ACCENT, darkcolor=T.ACCENT, thickness=12)
        s.configure("Total.Horizontal.TProgressbar", troughcolor=T.CARD_2, background=T.OK,
                    bordercolor=T.LINE, lightcolor=T.OK, darkcolor=T.OK, thickness=6)
        s.configure("Fynex.Vertical.TScrollbar", background=T.CARD_2, troughcolor=T.PAPER,
                    bordercolor=T.PAPER, arrowcolor=T.INK_SOFT, lightcolor=T.CARD_2, darkcolor=T.CARD_2)
        s.map("Fynex.Vertical.TScrollbar", background=[("active", T.LINE), ("!active", T.CARD_2)],
              lightcolor=[("!disabled", T.CARD_2)], darkcolor=[("!disabled", T.CARD_2)])

    def _build_titlebar(self):
        """Barra de título propia (reemplaza la nativa de Windows)."""
        self.titlebar = None
        if not self.chrome.enabled:
            return
        self._bar_icon = assets.mark_image(self, 16)
        self.titlebar = TitleBar(self, self.chrome, APP_NAME, self._bar_icon, assets.ui_scale(self))
        self.titlebar.pack(fill="x")
        tk.Frame(self, bg=T.LINE, height=1).pack(fill="x")

    def _apply_chrome(self):
        if self.chrome.apply():
            return
        if self.titlebar is not None:        # no se pudo quitar la barra nativa: se usa la de Windows
            self.titlebar.pack_forget()
            self.titlebar = None

    def _build_header(self):
        bar = tk.Frame(self, bg=T.PAPER)
        bar.pack(fill="x")
        inner = tk.Frame(bar, bg=T.PAPER)
        inner.pack(fill="x", padx=24, pady=12)
        brand = tk.Frame(inner, bg=T.PAPER)
        brand.pack(side="left")
        self._logo = assets.mark_image(self, 40)
        if self._logo is not None:
            tk.Label(brand, image=self._logo, bg=T.PAPER).pack(side="left", padx=(0, 12))
        words = tk.Frame(brand, bg=T.PAPER)
        words.pack(side="left")
        tk.Label(words, text="FYNEX", bg=T.PAPER, fg="#ffffff", font=_font(20)).pack(anchor="w")
        tk.Label(words, text="PRECISION IN EVERY PROCESS", bg=T.PAPER, fg=T.TAGLINE,
                 font=_font(7)).pack(anchor="w")
        tools = tk.Frame(inner, bg=T.PAPER)
        tools.pack(side="left", padx=(32, 0))
        for text, title, fn in (("Equipo", "Información del equipo", sysinfo.run),
                                ("Disco", "Estado del disco", disk.run),
                                ("Batería", "Estado de la batería", battery.run)):
            b = _button(tools, text, lambda t=title, f=fn: self._start_diag(t, f), padx=12, pady=4)
            b.pack(side="left", padx=(0, 8))
            self.diag_btns.append(b)
        admin = system.is_admin()
        tk.Label(inner, text="● Administrador" if admin else "● Sin permisos de administrador",
                 bg=T.PAPER, fg=T.OK if admin else T.ERR, font=_font(9, "bold")).pack(side="right")
        sim = tk.Frame(inner, bg=T.PAPER)
        sim.pack(side="right", padx=20)
        CheckBox(sim, self.dry_run, T.PAPER, size=14).pack(side="left")
        tk.Label(sim, text=" Modo simulación (no modifica nada)", bg=T.PAPER, fg=T.INK_SOFT,
                 font=_font(9)).pack(side="left")
        tk.Frame(self, bg=T.LINE, height=1).pack(fill="x")

    def _section(self, parent, text):
        tk.Label(parent, text=text.upper(), bg=T.PAPER, fg=T.ACCENT, font=_font(9, "bold")
                 ).pack(anchor="w", pady=(0, 8))

    def _build_body(self):
        body = tk.Frame(self, bg=T.PAPER)
        body.pack(fill="both", expand=True, padx=24, pady=18)
        left = tk.Frame(body, bg=T.PAPER, width=600)
        left.pack(side="left", fill="y", padx=(0, 18))
        left.pack_propagate(False)

        head = tk.Frame(left, bg=T.PAPER)
        head.pack(fill="x", pady=(0, 8))
        tk.Label(head, text="TAREAS", bg=T.PAPER, fg=T.ACCENT, font=_font(9, "bold")).pack(side="left")
        self.run_sel = _button(head, "▶  Ejecutar seleccionadas (0)", self._run_selected, primary=True,
                               font=_font(9, "bold"), padx=12, pady=5, state="disabled")
        self.run_sel.pack(side="right")
        for text, val in (("Ninguna", False), ("Todas", True)):
            tk.Button(head, text=text, command=lambda v=val: self._select_all(v), bg=T.PAPER, fg=T.INK_SOFT,
                      activebackground=T.PAPER, activeforeground=T.ACCENT, relief="flat", bd=0,
                      font=_font(9, "bold"), cursor="hand2", highlightthickness=0
                      ).pack(side="right", padx=(0, 12))

        wrap, inner, _canvas = _scrollable(left)
        wrap.pack(fill="both", expand=True)
        for task in ALL_TASKS:
            card = TaskCard(inner, task, lambda t: self._run([t]), self._update_selection)
            card.pack(fill="x", pady=(0, 8), padx=(0, 6))
            self.cards.append(card)

        right = tk.Frame(body, bg=T.PAPER)
        right.pack(side="left", fill="both", expand=True)
        self._section(right, "Estado")
        panel = RoundedPanel(right, fill=T.CARD, border=T.LINE, radius=18, parent_bg=T.PAPER)
        panel.pack(fill="both", expand=True)
        pb_ = panel.body

        self.batch_frame = tk.Frame(pb_, bg=T.CARD)  # visible solo al ejecutar varias tareas
        self.batch_lbl = tk.Label(self.batch_frame, text="", bg=T.CARD, fg=T.OK, font=_font(9, "bold"), anchor="w")
        self.batch_lbl.pack(fill="x")
        self.pb_total = RoundedProgress(self.batch_frame, color=T.OK, trough=T.CARD_2, height=8)
        self.pb_total.pack(fill="x", pady=(4, 0))

        self.status = tk.Label(pb_, text="Listo. Elige una tarea o marca varias y pulsa «Ejecutar seleccionadas».",
                               bg=T.CARD, fg=T.INK, font=_font(12, "bold"), anchor="w", justify="left", wraplength=560)
        self.status.pack(fill="x", padx=18, pady=(16, 8))
        panel.bind("<Configure>", lambda e: self.status.configure(wraplength=max(200, e.width - 40)), add="+")
        self.pb = RoundedProgress(pb_, color=T.ACCENT, trough=T.CARD_2, height=12)
        self.pb.pack(fill="x", padx=18)
        meta = tk.Frame(pb_, bg=T.CARD)
        meta.pack(fill="x", padx=18, pady=(6, 10))
        self.pct = tk.Label(meta, text="0 %", bg=T.CARD, fg=T.ACCENT, font=_font(10, "bold"))
        self.pct.pack(side="left")
        self.elapsed = tk.Label(meta, text="", bg=T.CARD, fg=T.INK_SOFT, font=_font(9))
        self.elapsed.pack(side="right")

        logpanel = RoundedPanel(pb_, fill=T.PAPER, border=T.LINE, radius=14, parent_bg=T.CARD)
        logpanel.pack(fill="both", expand=True, padx=18)
        logwrap = logpanel.body
        sb = ttk.Scrollbar(logwrap, style="Fynex.Vertical.TScrollbar", orient="vertical")
        self.log = tk.Text(logwrap, bg=T.PAPER, fg=T.INK, relief="flat", font=("Consolas", 9), wrap="word",
                           state="disabled", yscrollcommand=sb.set, insertbackground=T.INK, padx=10, pady=8,
                           highlightthickness=0, bd=0)
        sb.configure(command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        for name, color in LOG_COLORS.items():
            self.log.tag_configure(name, foreground=color)

        row = tk.Frame(pb_, bg=T.CARD)
        row.pack(fill="x", padx=18, pady=(12, 10))
        self.cancel_btn = _button(row, "Cancelar tarea", self._cancel, state="disabled", fg=T.ERR,
                                  activeforeground=T.ERR)
        self.cancel_btn.pack(side="left")
        for text, cmd in (("Abrir carpeta de logs", self._open_logs), ("Limpiar pantalla", self._clear_log)):
            _button(row, text, cmd).pack(side="right", padx=(8, 0))
        tk.Label(self, text=f"v{__version__}", bg=T.PAPER, fg=T.INK_MUTED, font=_font(8)
                 ).pack(anchor="e", padx=24, pady=(0, 6))

    # ---------- selección ----------
    def _selected(self):
        return [c.task for c in self.cards if c.var.get()]

    def _select_all(self, value):
        if not self.busy:
            for c in self.cards:
                c.var.set(value)

    def _update_selection(self):
        n = len(self._selected())
        self.run_sel.configure(text=f"▶  Ejecutar seleccionadas ({n})",
                               state="normal" if (n and not self.busy and not self.diag_busy) else "disabled")

    def _run_selected(self):
        tasks = self._selected()
        if not tasks or self.busy:
            return
        if len(tasks) == 1:                    # una sola tarea: su propia confirmación ya es la única aprobación
            self._run(tasks)
            return
        details = "\n".join(f"{i}. {t.title}" for i, t in enumerate(tasks, 1))
        note = "\n\nMODO SIMULACIÓN activo: no se modificará nada." if self.dry_run.get() else ""
        if any(t.id == "winget" for t in tasks) and not self.dry_run.get():
            note += ("\n\nActualizar aplicaciones usará solo la lista de aprobadas ★; si todavía no hay ninguna, "
                     "te mostrará la lista para que elijas.")
        dlg = ConfirmDialog(self, "Aprobar el lote",
                            f"Se ejecutarán {len(tasks)} tareas, una tras otra y en este orden.\n\n"
                            "Al aprobar autorizas TODAS las acciones de estas tareas de una sola vez: no se volverá a "
                            "pedir confirmación durante el lote. Lo que se detecte y modifique queda en la consola y en "
                            "el log. «Cancelar todo» detiene el lote en cualquier momento." + note, details,
                            ok_text="Aprobar y ejecutar")
        self.wait_window(dlg)
        if dlg.result:
            self._run(tasks, auto_approve=True)

    # ---------- utilidades UI ----------
    def _log(self, msg, level="info"):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n", level)
        if int(self.log.index("end-1c").split(".")[0]) > 4000:
            self.log.delete("1.0", "500.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _open_logs(self):
        path = str(system.logs_dir())
        if system.IS_WINDOWS:
            os.startfile(path)  # noqa
        else:
            messagebox.showinfo("Logs", path)

    def _set_progress(self, pct):
        self._last_pct = pct
        if pct is None:
            if self.pb_mode != "indeterminate":
                self.pb.configure(mode="indeterminate")
                self.pb.start(14)
                self.pb_mode = "indeterminate"
            self.pct.configure(text="…")
        else:
            if self.pb_mode != "determinate":
                self.pb.stop()
                self.pb.configure(mode="determinate")
                self.pb_mode = "determinate"
            self.pb["value"] = pct
            self.pct.configure(text=f"{pct:.0f} %")
        if self.total > 1:
            frac = (self.idx - 1 + ((pct or 0) / 100)) / self.total
            self.pb_total["value"] = max(0, min(frac, 1)) * 100

    def _wait_user(self, waiting):
        """Mientras hay un diálogo esperando al usuario la barra se queda quieta (sin animación ni parpadeo)."""
        if waiting:
            self._prev_status = self.status.cget("text")
            self.pb.stop()
            self.pb_mode = "determinate"
            self.pb.configure(mode="determinate")
            self.pb["value"] = self._last_pct or 0
            self.pct.configure(text="en espera")
            self.status.configure(text="Esperando tu aprobación…", fg=T.ACCENT)
        else:
            self.status.configure(text=self._prev_status, fg=T.INK)
            self._set_progress(self._last_pct)

    def _ask(self, dialog):
        """Muestra un diálogo modal con la barra en pausa y devuelve su resultado."""
        self._wait_user(True)
        try:
            self.wait_window(dialog)
        finally:
            self._wait_user(False)
        return dialog.result

    def _set_busy(self, busy):
        self.busy = busy
        for c in self.cards:
            c.set_enabled(not busy)
        for b in self.diag_btns:
            b.configure(state="disabled" if busy else "normal")
        self.cancel_btn.configure(state="normal" if busy else "disabled")
        self._update_selection()

    # ---------- diagnósticos (solo lectura, salen en la consola) ----------
    def _start_diag(self, title, func):
        if self.busy or self.diag_busy:
            return
        ok, msg = system.runtime_ok()
        if not ok:
            messagebox.showerror("Archivos temporales eliminados", msg)
            return
        if not system.IS_WINDOWS:
            messagebox.showwarning("Solo Windows", "Este diagnóstico lee datos de Windows (WMI/PowerShell) "
                                   "y solo funciona en Windows 10/11.")
            return
        self._set_diag_busy(True)
        self._clear_log()
        self.elapsed.configure(text="")
        self.status.configure(text=f"Consultando: {title}…", fg=T.INK)
        self._log(f"Consultando {title.lower()}… (puede tardar unos segundos)", "dim")
        threading.Thread(target=self._diag_worker, args=(title, func), daemon=True).start()

    def _diag_worker(self, title, func):
        failed = False
        try:
            for text, level in func():
                failed = failed or level == "err"
                self.events.put(("log", text, level))
        except Exception as e:
            failed = True
            self.events.put(("log", f"Error: {e}", "err"))
        self.events.put(("diag_done", title, failed))

    def _set_diag_busy(self, flag):
        self.diag_busy = flag
        for b in self.diag_btns:
            b.configure(state="disabled" if (flag or self.busy) else "normal")
        for c in self.cards:
            c.set_enabled(not flag and not self.busy)
        self._update_selection()

    # ---------- ejecución ----------
    def _run(self, tasks, auto_approve=False):
        if self.busy or self.diag_busy or not tasks:
            return
        ok, msg = system.runtime_ok()
        if not ok:
            messagebox.showerror("Archivos temporales eliminados", msg)
            return
        if not system.IS_WINDOWS and any(t.needs_windows for t in tasks):
            messagebox.showwarning("Solo Windows", "Estas tareas solo funcionan en Windows 10/11.\n"
                                   "Puedes activar el modo simulación para revisar la interfaz.")
            if not self.dry_run.get():
                return
        if system.IS_WINDOWS and any(t.needs_admin for t in tasks) and not system.is_admin():
            if not messagebox.askyesno("Sin permisos de administrador",
                                       "Esta herramienta no se está ejecutando como administrador; las tareas "
                                       "probablemente fallen.\n\n¿Continuar de todas formas?"):
                return
        self.total, self.idx = len(tasks), 0
        self.cancel_event.clear()
        self.started = time.time()
        self._clear_log()
        self._set_busy(True)
        self.cancel_btn.configure(text="Cancelar todo" if self.total > 1 else "Cancelar tarea")
        if self.total > 1:
            self.pb_total["value"] = 0
            self.batch_lbl.configure(text=f"Lote de {self.total} tareas")
            self.batch_frame.pack(fill="x", padx=18, pady=(16, 0), before=self.status)
            self.status.pack_configure(pady=(10, 8))
        else:
            self.batch_frame.pack_forget()
            self.status.pack_configure(pady=(16, 8))
        self._set_progress(None)
        threading.Thread(target=self._worker, args=(tasks, self.dry_run.get(), auto_approve), daemon=True).start()

    def _worker(self, tasks, dry, auto_approve=False):
        run_tasks(tasks, self.events, self.cancel_event, dry_run=dry, log_dir=system.logs_dir(),
                  set_ctx=lambda c: setattr(self, "ctx", c), auto_approve=auto_approve)

    def _cancel(self):
        if self.busy and self.ctx:
            self.cancel_event.set()
            self.ctx.kill_process()
            self.status.configure(text="Cancelando…", fg=T.ACCENT)

    def _poll(self):
        self.after(80, self._poll)
        try:
            while True:
                kind, *p = self.events.get_nowait()
                if kind == "log":
                    self._log(*p)
                elif kind == "status":
                    self.status.configure(text=p[0], fg=T.INK)
                elif kind == "progress":
                    self._set_progress(p[0])
                elif kind == "confirm":
                    title, message, details, reply = p
                    reply.put(self._ask(ConfirmDialog(self, title, message, details)))
                elif kind == "choose":
                    title, message, items, pre, remember_label, reply = p
                    reply.put(self._ask(ChooseDialog(self, title, message, items, pre, remember_label)))
                elif kind == "diag_done":
                    self._set_diag_busy(False)
                    failed = len(p) > 1 and p[1]
                    self.status.configure(text=f"{p[0]}: terminó con errores (revisa la consola)." if failed
                                          else f"{p[0]}: listo.", fg=T.ERR if failed else T.OK)
                elif kind == "begin":
                    self._begin(*p)
                elif kind == "end":
                    self._end(*p)
                elif kind == "done":
                    self._finish(p[0])
        except queue.Empty:
            pass

    def _begin(self, i, n, task):
        self.idx = i
        self.status.configure(text=f"{task.title}…", fg=T.INK)
        sim = "  [SIMULACIÓN]" if self.dry_run.get() else ""
        if n > 1:
            self.batch_lbl.configure(text=f"Tarea {i} de {n} · {task.title}")
            self._log(f"━━━ {i}/{n} · {task.title}{sim} ━━━", "warn")
        else:
            self._log(f"▶ {task.title}{sim}", "warn")
        self._set_progress(None)

    def _end(self, task, result):
        self._log(("✔ " if result.ok else "✘ ") + result.summary, "ok" if result.ok else "err")
        if result.details:
            self._log(result.details, "dim")
        if self.total > 1:
            self.pb_total["value"] = self.idx / self.total * 100
        elif result.ok:
            self._set_progress(100)

    def _finish(self, results):
        self._set_busy(False)
        self.cancel_btn.configure(text="Cancelar tarea")
        if self.total <= 1 and len(results) == 1:
            task, result = results[0]
            self.status.configure(text=result.summary, fg=T.OK if result.ok else T.ERR)
            (messagebox.showinfo if result.ok else messagebox.showwarning)(task.title, result.summary)
            return
        okc = sum(1 for _, r in results if r.ok)
        lines = [f"{'✔' if r.ok else '✘'} {t.title}: {r.summary}" for t, r in results]
        all_ok = okc == len(results)
        self.pb_total["value"] = 100 if all_ok else self.pb_total["value"]
        if all_ok:
            self._set_progress(100)
        summary = f"Lote terminado: {okc} de {len(results)} tareas sin errores."
        self.status.configure(text=summary, fg=T.OK if all_ok else T.ERR)
        self._log("━━━ " + summary + " ━━━", "ok" if all_ok else "err")
        (messagebox.showinfo if all_ok else messagebox.showwarning)("Lote terminado", summary + "\n\n" + "\n".join(lines))

    def _tick(self):
        self.after(500, self._tick)
        if self.busy:
            s = int(time.time() - self.started)
            self.elapsed.configure(text=f"Tiempo: {s // 60:02d}:{s % 60:02d}")

    def report_callback_exception(self, exc, val, tb):
        """En el .exe sin consola los errores de la interfaz se perdían: se muestran en la consola de la app y se guardan."""
        text = "".join(traceback.format_exception(exc, val, tb))
        try:
            self._log("Error interno de la interfaz (se guardó en logs\\fynex_ui_errors.log):\n" + text, "err")
            with open(system.logs_dir() / "fynex_ui_errors.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{text}\n")
        except Exception:
            pass

    def destroy(self):
        """Cancela los temporizadores pendientes antes de cerrar (evita avisos de Tk al salir)."""
        try:
            for after_id in self.tk.splitlist(self.tk.call("after", "info")):
                self.after_cancel(after_id)
        except tk.TclError:
            pass
        super().destroy()

    def _on_close(self):
        if self.busy:
            if not messagebox.askyesno("Tarea en curso", "Hay una tarea ejecutándose. ¿Cancelar y salir?"):
                return
            self._cancel()
        self.destroy()
