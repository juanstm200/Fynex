"""Recursos gráficos de Fynex (logo e iconos), con selección de tamaño según la escala de pantalla."""
import tkinter as tk

from ..core import system

MARK_SIZES = (16, 24, 36, 54, 72, 108)


def ui_scale(widget) -> float:
    """1.0 a 96 ppp (100 %), 1.5 al 150 %, etc. (Tk expresa la escala en píxeles por punto)."""
    try:
        return max(1.0, float(widget.tk.call("tk", "scaling")) / (96 / 72))
    except (tk.TclError, ValueError):
        return 1.0


def pick_mark_size(px: int, scale: float = 1.0) -> int:
    """Tamaño disponible más cercano a px×scale."""
    target = px * scale
    return min(MARK_SIZES, key=lambda s: abs(s - target))


def mark_image(widget, px: int):
    """PhotoImage de la marca con ~px píxeles lógicos; None si falta el archivo."""
    size = pick_mark_size(px, ui_scale(widget))
    try:
        return tk.PhotoImage(master=widget, file=str(system.resource_path(f"assets/fynex_mark_{size}.png")))
    except tk.TclError:
        return None


def set_window_icon(window) -> None:
    """Icono de la ventana y de la barra de tareas."""
    try:
        if system.IS_WINDOWS:
            window.iconbitmap(default=str(system.resource_path("assets/fynex.ico")))
        else:
            window._icon_ref = tk.PhotoImage(master=window, file=str(system.resource_path("assets/fynex_icon_256.png")))
            window.iconphoto(True, window._icon_ref)
    except tk.TclError:
        pass
