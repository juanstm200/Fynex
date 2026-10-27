"""Barra de título propia de Fynex.

En Windows se quita el estilo de título nativo (WS_CAPTION) pero se conserva el marco con tamaño
(WS_THICKFRAME): la ventana sigue teniendo botón en la barra de tareas, Aero Snap, redimensionado por los
bordes y esquinas redondeadas de Windows 11. Mover = mensaje nativo HTCAPTION; maximizar = ajustar al área
de trabajo (para no tapar la barra de tareas). Si algo falla, la app cae sola a la barra nativa.
Forzar la barra nativa: argumento --native-titlebar o variable FYNEX_NATIVE_TITLEBAR=1."""
import ctypes
import os
import sys
import time
import tkinter as tk
from tkinter import font as tkfont

from ..core import system
from . import theme as T

GWL_STYLE = -16
WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_SYSMENU = 0x00020000, 0x00010000, 0x00080000
SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x2, 0x1, 0x4, 0x10, 0x20
WM_NCLBUTTONDOWN, HTCAPTION, SW_MINIMIZE, MONITOR_DEFAULTTONEAREST = 0x00A1, 2, 6, 2
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWA_BORDER_COLOR, DWMWA_EXTENDED_FRAME_BOUNDS = 33, 34, 9
DOUBLE_CLICK_SECONDS = 0.4


def wants_custom_bar(argv=None, environ=None, is_windows=None) -> bool:
    argv = sys.argv if argv is None else argv
    environ = os.environ if environ is None else environ
    is_windows = system.IS_WINDOWS if is_windows is None else is_windows
    if "--native-titlebar" in argv or environ.get("FYNEX_NATIVE_TITLEBAR"):
        return False
    return bool(is_windows or environ.get("FYNEX_CUSTOM_BAR"))      # FYNEX_CUSTOM_BAR: solo para pruebas en Linux


# ------------------------------------------------------------------ Win32
def _win():
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

    u = ctypes.windll.user32
    u.GetParent.restype, u.GetParent.argtypes = ctypes.c_void_p, [ctypes.c_void_p]
    u.GetWindowLongW.restype, u.GetWindowLongW.argtypes = ctypes.c_long, [ctypes.c_void_p, ctypes.c_int]
    u.SetWindowLongW.restype, u.SetWindowLongW.argtypes = ctypes.c_long, [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
    u.SetWindowPos.restype = wintypes.BOOL
    u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
    u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
    u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
    u.MonitorFromWindow.restype, u.MonitorFromWindow.argtypes = ctypes.c_void_p, [ctypes.c_void_p, wintypes.DWORD]
    u.PostMessageW.restype = wintypes.BOOL
    u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
    u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
    return u, RECT, MONITORINFO


class WindowChrome:
    """Estado y acciones de la ventana sin marco nativo."""

    def __init__(self, window, on_close):
        self.w, self.on_close = window, on_close
        self.enabled = wants_custom_bar()
        self.hwnd, self.maxed, self.normal, self._last_press, self._converting = None, False, None, 0.0, False
        self.on_state = lambda maxed: None       # lo asigna la barra para cambiar el icono de maximizar
        self._drag = (0, 0)
        self._manual_drag = False                # True si el arrastre nativo falló: se mueve con geometry()

    # --- activación
    def apply(self) -> bool:
        """Quita la barra nativa. Idempotente. Devuelve False si no se pudo (se usará la nativa)."""
        if not self.enabled:
            return False
        if not system.IS_WINDOWS:
            return True                          # Linux/pruebas: no hay marco nativo que quitar
        try:
            u, _R, _M = _win()
            hwnd = u.GetParent(self.w.winfo_id()) or self.w.winfo_id()
            style = u.GetWindowLongW(hwnd, GWL_STYLE)
            want = (style & ~WS_CAPTION) | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU
            if want != style:
                u.SetWindowLongW(hwnd, GWL_STYLE, want)
                u.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
            self.hwnd = hwnd
            self._dwm_polish(hwnd)
            return True
        except Exception:
            self.enabled = False
            return False

    def _dwm_polish(self, hwnd):
        """Windows 11: esquinas redondeadas y borde del color de la app (se ignora en Windows 10)."""
        try:
            dwm = ctypes.windll.dwmapi
            val = ctypes.c_int(2)                                    # DWMWCP_ROUND
            dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(val), 4)
            r, g, b = (int(T.LINE[i:i + 2], 16) for i in (1, 3, 5))
            col = ctypes.c_int(r | (g << 8) | (b << 16))             # COLORREF 0x00BBGGRR
            dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_BORDER_COLOR, ctypes.byref(col), 4)
        except Exception:
            pass

    # --- acciones
    def start_move(self, event):
        """Arrastrar: mensaje nativo en Windows (con Aero Snap); manual en otros sistemas."""
        now = time.monotonic()
        if now - self._last_press < DOUBLE_CLICK_SECONDS:        # doble clic = maximizar/restaurar
            self._last_press = 0.0
            self.toggle_maximize()
            return
        self._last_press = now
        if self.maxed:
            return
        self._manual_drag = False
        if self.hwnd and system.IS_WINDOWS:
            # IMPORTANTE: PostMessage (no SendMessage). SendMessage(WM_NCLBUTTONDOWN, HTCAPTION) entra al bucle modal
            # de mover ventana DENTRO de esta llamada ctypes, que suelta el GIL; durante ese bucle Tcl sigue
            # ejecutando temporizadores (after: _poll, _tick, barra de progreso) y vuelve a entrar a Python con el
            # GIL suelto -> cierre inmediato del proceso sin ningún aviso. Con PostMessage el mensaje se procesa desde
            # el bucle normal de Tk, igual que con una barra nativa, y el arrastre conserva Aero Snap.
            try:
                from ctypes import wintypes
                u, _R, _M = _win()
                pt = wintypes.POINT()
                u.GetCursorPos(ctypes.byref(pt))
                u.ReleaseCapture()
                lparam = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
                if u.PostMessageW(self.hwnd, WM_NCLBUTTONDOWN, HTCAPTION, lparam):
                    return
            except Exception:
                pass
            self._manual_drag = True              # respaldo: mover con geometry() (sin Aero Snap)
        self._drag = (event.x_root - self.w.winfo_x(), event.y_root - self.w.winfo_y())

    def drag_motion(self, event):
        if (self._manual_drag or not (self.hwnd and system.IS_WINDOWS)) and not self.maxed:
            self.w.geometry(f"+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}")

    def minimize(self):
        if self.hwnd and system.IS_WINDOWS:
            _win()[0].ShowWindow(self.hwnd, SW_MINIMIZE)
        else:
            self.w.iconify()

    def toggle_maximize(self):
        self._set_maximized(not self.maxed)

    def _set_maximized(self, value: bool):
        if value == self.maxed:
            return
        if self.hwnd and system.IS_WINDOWS:
            u, RECT, MONITORINFO = _win()
            if value:
                rect = RECT()
                u.GetWindowRect(self.hwnd, ctypes.byref(rect))
                self.normal = (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
                mon = u.MonitorFromWindow(self.hwnd, MONITOR_DEFAULTTONEAREST)
                mi = MONITORINFO()
                mi.cbSize = ctypes.sizeof(MONITORINFO)
                u.GetMonitorInfoW(mon, ctypes.byref(mi))
                wk, il, it, ir, ib = mi.rcWork, 0, 0, 0, 0
                ext = RECT()          # marco visible: el rectángulo de la ventana incluye bordes invisibles
                if ctypes.windll.dwmapi.DwmGetWindowAttribute(ctypes.c_void_p(self.hwnd), DWMWA_EXTENDED_FRAME_BOUNDS,
                                                              ctypes.byref(ext), ctypes.sizeof(ext)) == 0:
                    il, it, ir, ib = ext.left - rect.left, ext.top - rect.top, rect.right - ext.right, rect.bottom - ext.bottom
                u.SetWindowPos(self.hwnd, None, wk.left - il, wk.top - it, wk.right - wk.left + il + ir,
                               wk.bottom - wk.top + it + ib, SWP_NOZORDER | SWP_NOACTIVATE)
            elif self.normal:
                x, y, w, h = self.normal
                u.SetWindowPos(self.hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE)
        else:                                                    # Linux/pruebas
            if value:
                self.normal = self.w.geometry()
                self.w.geometry(f"{self.w.winfo_screenwidth()}x{self.w.winfo_screenheight()}+0+0")
            elif self.normal:
                self.w.geometry(self.normal)
        self.maxed = value
        self.on_state(value)

    def on_configure(self, _event=None):
        """Win+↑ / arrastrar arriba (maximizado nativo, taparía la barra de tareas): se convierte al nuestro."""
        if self.hwnd and system.IS_WINDOWS and not self._converting and self.w.state() == "zoomed":
            self._converting = True
            self.w.state("normal")
            self.maxed = False
            self.w.after(40, lambda: (self._set_maximized(True), setattr(self, "_converting", False)))

    def close(self):
        self.on_close()


# ------------------------------------------------------------------ barra visible
class _CaptionButton(tk.Frame):
    def __init__(self, parent, glyph, command, font, height, hover_bg=T.CARD_2, hover_fg=T.INK):
        super().__init__(parent, bg=T.TITLE_BG, width=48, height=height, cursor="hand2")
        self.pack_propagate(False)
        self.hover_bg, self.hover_fg, self.command = hover_bg, hover_fg, command
        self.lbl = tk.Label(self, text=glyph, bg=T.TITLE_BG, fg=T.INK_SOFT, font=font)
        self.lbl.place(relx=0.5, rely=0.5, anchor="center")
        for w in (self, self.lbl):
            w.bind("<Enter>", lambda e: self._paint(True))
            w.bind("<Leave>", lambda e: self._paint(False))
            w.bind("<ButtonRelease-1>", lambda e: self.command())

    def _paint(self, hover):
        self.configure(bg=self.hover_bg if hover else T.TITLE_BG)
        self.lbl.configure(bg=self.hover_bg if hover else T.TITLE_BG, fg=self.hover_fg if hover else T.INK_SOFT)

    def set_glyph(self, glyph):
        self.lbl.configure(text=glyph)


class TitleBar(tk.Frame):
    def __init__(self, parent, chrome: WindowChrome, title: str, icon=None, scale: float = 1.0):
        height = int(round(36 * min(scale, 1.5)))
        super().__init__(parent, bg=T.TITLE_BG, height=height)
        self.pack_propagate(False)
        self.chrome = chrome
        mdl2 = "Segoe MDL2 Assets" in tkfont.families(parent)
        font = ("Segoe MDL2 Assets", 10) if mdl2 else (T.FONT, 11)
        self.glyphs = ("\uE921", "\uE922", "\uE923", "\uE8BB") if mdl2 else ("—", "☐", "❐", "✕")
        left = tk.Frame(self, bg=T.TITLE_BG)
        left.pack(side="left", padx=(12, 0))
        if icon is not None:
            tk.Label(left, image=icon, bg=T.TITLE_BG).pack(side="left", padx=(0, 8))
        tk.Label(left, text=title, bg=T.TITLE_BG, fg=T.INK_SOFT, font=(T.FONT, 9)).pack(side="left")
        self.btn_close = _CaptionButton(self, self.glyphs[3], chrome.close, font, height, hover_bg=T.ERR, hover_fg="#ffffff")
        self.btn_max = _CaptionButton(self, self.glyphs[1], chrome.toggle_maximize, font, height)
        self.btn_min = _CaptionButton(self, self.glyphs[0], chrome.minimize, font, height)
        for b in (self.btn_close, self.btn_max, self.btn_min):
            b.pack(side="right")
        for w in (self, left, *left.winfo_children()):
            w.bind("<ButtonPress-1>", chrome.start_move)
            w.bind("<B1-Motion>", chrome.drag_motion)
        chrome.on_state = lambda maxed: self.btn_max.set_glyph(self.glyphs[2] if maxed else self.glyphs[1])
