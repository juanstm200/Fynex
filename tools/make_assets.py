"""Genera los recursos gráficos de Fynex (marca, tamaños PNG e icono .ico).
Requiere Pillow SOLO para regenerar los recursos:  pip install pillow  &&  python tools/make_assets.py
La aplicación en sí no necesita Pillow: usa los PNG/ICO ya incluidos en assets/.

Para usar el logo oficial en lugar de esta marca, reemplaza assets/fynex_mark_<tamaño>.png y assets/fynex.ico."""
import math
import os

from PIL import Image, ImageDraw

NAVY = (12, 21, 38)          # #0c1526, fondo de la app
WHITE = (255, 255, 255)
SS = 8                       # supersampling para bordes suaves

# Marca: remolino de brazos en espiral afinados en las puntas (arcos blancos separados por huecos).
ARMS = 7                     # número de brazos
SWEEP_DEG = 235.0            # recorrido angular de cada brazo
R_IN, R_OUT = 0.16, 1.0      # radios interior y exterior (unidades del círculo de la marca)
WIDTH = 0.28                 # ancho máximo de cada brazo respecto al hueco entre brazos (< 1 deja separación)
START_DEG = 168.0            # ángulo donde empieza el primer brazo


def _arm_polygon(k, big, cx, cy, radius, arms, width):
    """Polígono de un brazo: línea espiral con ancho que crece hacia fuera y se afila en ambas puntas."""
    n = 140
    pts_l, pts_r = [], []
    sweep = math.radians(SWEEP_DEG)
    base = math.radians(START_DEG) + k * (2 * math.pi / arms)
    # hueco angular entre brazos vecinos, medido como longitud de arco en cada radio
    for j in range(n + 1):
        t = j / n
        th = base + t * sweep
        r = R_IN + (R_OUT - R_IN) * (t ** 0.85)
        # ancho: proporcional al radio (los brazos se ven parejos) y afilado en las puntas
        taper = math.sin(math.pi * t) ** 0.75
        half = 0.5 * width * (2 * math.pi / arms) * r * 0.62 * taper
        nx, ny = math.cos(th), -math.sin(th)          # normal (radial) en pantalla (y hacia abajo)
        # dirección tangente (derivada) -> la normal al brazo es perpendicular a la tangente
        dr = (R_OUT - R_IN) * 0.85 * (max(t, 1e-3) ** -0.15) / sweep
        tx = dr * math.cos(th) - r * math.sin(th)
        ty = -(dr * math.sin(th) + r * math.cos(th))
        L = math.hypot(tx, ty) or 1
        ox, oy = -ty / L, tx / L
        x, y = cx + r * radius * math.cos(th), cy - r * radius * math.sin(th)
        pts_l.append((x + ox * half * radius, y + oy * half * radius))
        pts_r.append((x - ox * half * radius, y - oy * half * radius))
    return pts_l + pts_r[::-1]


def variant_for(size: int):
    """A tamaños pequeños se simplifica (menos brazos, más gruesos) para que no se vuelva un borrón."""
    if size <= 28:
        return 4, 0.46
    if size <= 48:
        return 5, 0.36
    return ARMS, WIDTH


def mark_mask(size: int) -> Image.Image:
    """Máscara L (255 = blanco) de la marca, ajustada al tamaño pedido con un pequeño margen."""
    arms, width = variant_for(size)
    big = size * SS
    radius = big * 0.46
    cx = cy = big / 2
    mask = Image.new("L", (big, big), 0)
    d = ImageDraw.Draw(mask)
    for k in range(arms):
        d.polygon(_arm_polygon(k, big, cx, cy, radius, arms, width), fill=255)
    return mask.resize((size, size), Image.LANCZOS)


def mark_png(size: int, bg=None) -> Image.Image:
    m = mark_mask(size)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0) if bg is None else bg + (255,))
    img.paste(Image.new("RGBA", (size, size), WHITE + (255,)), mask=m)
    return img


def icon_image(size: int = 256) -> Image.Image:
    """Icono de aplicación: cuadrado redondeado azul marino con la marca blanca."""
    ss = size * 4
    base = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    ImageDraw.Draw(base).rounded_rectangle([0, 0, ss - 1, ss - 1], radius=int(ss * 0.22), fill=NAVY + (255,))
    base = base.resize((size, size), Image.LANCZOS)
    m = mark_png(int(size * 0.80))
    base.alpha_composite(m, ((size - m.width) // 2, (size - m.height) // 2))
    return base


def write_ico(path, sizes=(16, 24, 32, 48, 64, 128, 256)):
    """ICO con una imagen propia por tamaño (PNG incrustado), para que 16/24/32 px queden nítidos."""
    import io
    import struct
    blobs = []
    for sz in sizes:
        buf = io.BytesIO()
        icon_image(sz).save(buf, format="PNG")
        blobs.append(buf.getvalue())
    offset = 6 + 16 * len(sizes)
    head = struct.pack("<HHH", 0, 1, len(sizes))
    entries = b""
    for sz, blob in zip(sizes, blobs):
        entries += struct.pack("<BBBBHHII", sz % 256, sz % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    with open(path, "wb") as f:
        f.write(head + entries + b"".join(blobs))


def main(out="assets"):
    os.makedirs(out, exist_ok=True)
    for s in (16, 24, 36, 54, 72, 108):
        mark_png(s).save(os.path.join(out, f"fynex_mark_{s}.png"))
    write_ico(os.path.join(out, "fynex.ico"))
    icon_image(256).save(os.path.join(out, "fynex_icon_256.png"))
    print("Recursos generados en", out)


if __name__ == "__main__":
    main()
