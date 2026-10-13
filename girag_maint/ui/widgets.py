"""Componentes con esquinas redondeadas y antialias (Tkinter no los trae).
Las esquinas se generan como imágenes PNG opacas ya mezcladas con el color de fondo del
contenedor, así no dependen de transparencia y se ven suaves en Windows y Linux."""
import base64
import math
import struct
import tkinter as tk
import tkinter.font as tkfont
import zlib

from . import theme as T

_CACHE = {}   # (r, fill, border, bg, bw) -> {"tl","tr","bl","br"} (referencias vivas)


# ------------------------------------------------------------------ imágenes
def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _png(width, height, rows):
    raw = b"".join(b"\x00" + bytes(v for px in row for v in px) for row in rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def corner_pixels(r, fill, border, bg, bw=1):
    """Cuadrante superior izquierdo r×r: borde (opcional) + relleno + fondo, con antialias."""
    f, b, g = _hex(fill), _hex(border) if border else None, _hex(bg)
    rows = []
    for y in range(r):
        row = []
        for x in range(r):
            d = math.hypot(x + 0.5 - r, y + 0.5 - r)
            a_out = min(1.0, max(0.0, r - d + 0.5))
            if b:
                a_in = min(1.0, max(0.0, (r - bw) - d + 0.5))
                inner = _mix(b, f, a_in)
            else:
                inner = f
            row.append(_mix(g, inner, a_out))
        rows.append(row)
    return rows


def corner_images(r, fill, border, bg, bw=1):
    key = (r, fill, border, bg, bw)
    if key not in _CACHE:
        tl = corner_pixels(r, fill, border, bg, bw)
        variants = {"tl": tl, "tr": [row[::-1] for row in tl], "bl": tl[::-1], "br": [row[::-1] for row in tl[::-1]]}
        _CACHE[key] = {k: tk.PhotoImage(data=base64.b64encode(_png(r, r, v))) for k, v in variants.items()}
    return _CACHE[key]


def draw_round_rect(canvas, w, h, r, fill, border, bg, bw=1, tag="shape"):
    """Dibuja un rectángulo redondeado en el canvas (relleno + borde de 1 px + 4 esquinas)."""
    r = max(1, min(r, w // 2, h // 2))
    imgs = corner_images(r, fill, border, bg, bw)
    canvas.create_rectangle(r, 0, w - r + 1, h, fill=fill, outline="", tags=tag)
    canvas.create_rectangle(0, r, w + 1, h - r + 1, fill=fill, outline="", tags=tag)
    if border:
        for x0, y0, x1, y1 in ((r, 0, w - r, 0), (r, h - 1, w - r, h - 1), (0, r, 0, h - r), (w - 1, r, w - 1, h - r)):
            canvas.create_line(x0, y0, x1 + (1 if x0 != x1 else 0), y1 + (1 if y0 != y1 else 0),
                               fill=border, tags=tag)
    canvas.create_image(0, 0, anchor="nw", image=imgs["tl"], tags=tag)
    canvas.create_image(w - r, 0, anchor="nw", image=imgs["tr"], tags=tag)
    canvas.create_image(0, h - r, anchor="nw", image=imgs["bl"], tags=tag)
    canvas.create_image(w - r, h - r, anchor="nw", image=imgs["br"], tags=tag)


# ------------------------------------------------------------------ panel
class RoundedPanel(tk.Frame):
    """Contenedor de esquinas redondeadas. Los hijos van en `.body`."""

    def __init__(self, parent, fill=T.CARD, border=T.LINE, radius=14, parent_bg=None):
        self.parent_bg = parent_bg or parent.cget("bg")
        super().__init__(parent, bg=self.parent_bg, bd=0, highlightthickness=0)
        self.fill, self.border, self.radius = fill, border, radius
        self._bg = tk.Canvas(self, bg=self.parent_bg, bd=0, highlightthickness=0)
        self._bg.place(x=0, y=0, relwidth=1, relheight=1)
        inset = max(3, int(radius * 0.36) + 1)
        self.body = tk.Frame(self, bg=fill, bd=0, highlightthickness=0)
        self.body.pack(fill="both", expand=True, padx=inset, pady=inset)
        self.bind("<Configure>", self._redraw, add="+")

    def _redraw(self, _e=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        self._bg.delete("shape")
        draw_round_rect(self._bg, w, h, self.radius, self.fill, self.border, self.parent_bg)


# ------------------------------------------------------------------ botón
class RoundedButton(tk.Canvas):
    """Botón redondeado. API compatible con lo usado de tk.Button: configure(text/state/fg/bg/command)."""
    STYLES = {
        "primary": dict(bg=T.ACCENT, hover=T.ACCENT_HOVER, fg=T.ACCENT_INK, hover_fg=T.ACCENT_INK,
                        off_bg="#3a3340", off_fg="#7a5a40", border=None),
        "ghost": dict(bg=T.CARD_2, hover=T.LINE, fg=T.INK_SOFT, hover_fg=T.INK,
                      off_bg=T.CARD, off_fg=T.INK_MUTED, border=T.LINE),
    }

    def __init__(self, parent, text, command=None, style="ghost", font=None, padx=14, pady=7,
                 width_px=None, radius=11, state="normal", **kw):
        self.parent_bg = parent.cget("bg")
        super().__init__(parent, bg=self.parent_bg, bd=0, highlightthickness=0, cursor="hand2")
        self.s = dict(self.STYLES[style])
        self.s.update({k: v for k, v in kw.items() if k in self.s})
        self._text, self._command, self._state = text, command, state
        self._font = font or (T.FONT, 9, "bold")
        self._padx, self._pady, self._width_px, self._radius = padx, pady, width_px, radius
        self._hover = False
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._release)
        self._resize()

    # --- tamaño y dibujo
    def _resize(self):
        f = tkfont.Font(font=self._font)
        w = self._width_px or f.measure(self._text) + 2 * self._padx
        h = f.metrics("linespace") + 2 * self._pady + 2
        super().configure(width=w, height=h)
        self._bw, self._bh = w, h
        self._draw()

    def _colors(self):
        s = self.s
        if self._state == "disabled":
            return s["off_bg"], s["off_fg"], s["border"] and s["off_bg"]
        if self._hover:
            return s["hover"], s["hover_fg"], s["border"]
        return s["bg"], s["fg"], s["border"]

    def _draw(self):
        self.delete("all")
        fill, fg, border = self._colors()
        draw_round_rect(self, self._bw, self._bh, self._radius, fill, border, self.parent_bg)
        self.create_text(self._bw / 2, self._bh / 2, text=self._text, fill=fg, font=self._font)

    # --- eventos
    def _set_hover(self, flag):
        self._hover = flag and self._state != "disabled"
        self._draw()

    def _release(self, e):
        if self._state != "disabled" and 0 <= e.x <= self._bw and 0 <= e.y <= self._bh and self._command:
            self._command()

    def invoke(self):
        if self._state != "disabled" and self._command:
            self._command()

    # --- API tipo tk.Button
    def configure(self, cnf=None, **kw):
        kw = {**(cnf or {}), **kw}
        need_resize = False
        for k in list(kw):
            v = kw[k]
            if k == "text":
                self._text, need_resize = v, True
            elif k == "state":
                self._state = v
                super().configure(cursor="hand2" if v != "disabled" else "arrow")
            elif k == "command":
                self._command = v
            elif k in ("fg", "bg", "hover", "off_bg", "off_fg", "border"):
                self.s[k] = v
            elif k == "activeforeground":
                self.s["hover_fg"] = v
            elif k == "activebackground":
                self.s["hover"] = v
            elif k == "disabledforeground":
                self.s["off_fg"] = v
            elif k == "font":
                self._font, need_resize = v, True
            elif k in ("padx", "pady", "radius"):
                setattr(self, "_" + k, v)
                need_resize = True
            elif k in ("width", "height", "cursor"):
                super().configure(**{k: v})
                continue
            else:
                continue            # relief, bd, highlight*: no aplican
            kw.pop(k, None)
        if need_resize:
            self._resize()
        else:
            self._draw()

    config = configure

    def cget(self, key):
        return self._state if key == "state" else self._text if key == "text" else super().cget(key)


# ------------------------------------------------------------------ progreso
class RoundedProgress(tk.Canvas):
    """Barra de progreso tipo píldora. Imita lo usado de ttk.Progressbar: ['value'], mode, start(), stop()."""

    def __init__(self, parent, color=T.ACCENT, trough=T.CARD_2, height=12):
        super().__init__(parent, height=height, bg=parent.cget("bg"), bd=0, highlightthickness=0)
        self.color, self.trough, self.h = color, trough, height
        self.value, self.mode, self._job, self._pos, self._dir = 0.0, "determinate", None, 0.0, 1
        self.bind("<Configure>", lambda e: self._draw())

    def __setitem__(self, key, val):
        if key == "value":
            self.value = float(val)
            self._draw()

    def __getitem__(self, key):
        return self.value if key == "value" else 100

    def configure(self, cnf=None, **kw):
        kw = {**(cnf or {}), **kw}
        if "mode" in kw:
            self.mode = kw.pop("mode")
        for k in ("maximum", "style"):
            kw.pop(k, None)
        if kw:
            super().configure(**kw)
        self._draw()

    config = configure

    def start(self, interval=14):
        if self._job is None:
            self._step()

    def stop(self):
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def _step(self):
        w = max(self.winfo_width(), 2)
        self._pos += self._dir * w * 0.014
        if self._pos > 1 or self._pos < 0:
            self._dir *= -1
            self._pos = min(1.0, max(0.0, self._pos))
        self._draw()
        self._job = self.after(30, self._step)

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.h
        if w < h + 2:
            return
        y, r = h / 2, h / 2
        self.create_line(r, y, w - r, y, width=h, capstyle="round", fill=self.trough)
        span = w - 2 * r
        if self.mode == "indeterminate":
            seg = span * 0.28
            x0 = r + (span - seg) * self._pos
            self.create_line(x0, y, x0 + seg, y, width=h, capstyle="round", fill=self.color)
        elif self.value > 0:
            x1 = r + span * min(self.value, 100) / 100
            self.create_line(r, y, max(x1, r + 0.5), y, width=h, capstyle="round", fill=self.color)
