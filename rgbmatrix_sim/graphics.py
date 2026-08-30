# rgbmatrix_sim/graphics.py
from .bdf import BDFFont

class Color:
    def __init__(self, red=0, green=0, blue=0):
        self.red = red
        self.green = green
        self.blue = blue

class Font:
    def __init__(self):
        self._bdf = None

    def LoadFont(self, path):
        self._bdf = BDFFont()
        self._bdf.load(path)

    @property
    def height(self):
        return self._bdf.height

    @property
    def baseline(self):
        return self._bdf.baseline


def DrawText(canvas, font: Font, x, y, color: Color, text):
    """Matches rgbmatrix's convention: (x, y) is the text baseline's left edge."""
    if font._bdf is None:
        raise ValueError("Font not loaded — call font.LoadFont(path) first")

    bdf = font._bdf
    cursor_x = x

    for ch in text:
        glyph = bdf.get_glyph(ch)
        if glyph is None:
            cursor_x += bdf.bbox_w  # fallback advance for missing glyphs
            continue

        glyph_top_y = y - glyph.bb_yoff - glyph.bb_h + 1

        for row_idx, (row_bits, total_bits) in enumerate(glyph.bitmap_rows):
            for bit_idx in range(glyph.bb_w):
                shift = total_bits - 1 - bit_idx
                if (row_bits >> shift) & 1:
                    px = cursor_x + glyph.bb_xoff + bit_idx
                    py = glyph_top_y + row_idx
                    canvas.SetPixel(px, py, color.red, color.green, color.blue)

        cursor_x += glyph.dwx

    return cursor_x - x  # total width advanced, matches real DrawText's return


def DrawLine(canvas, x0, y0, x1, y1, color: Color):
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        canvas.SetPixel(x, y, color.red, color.green, color.blue)
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy


def DrawCircle(canvas, x0, y0, r, color: Color):
    x, y = r, 0
    err = 0
    while x >= y:
        for dx, dy in [(x, y), (y, x), (-y, x), (-x, y),
                       (-x, -y), (-y, -x), (y, -x), (x, -y)]:
            canvas.SetPixel(x0 + dx, y0 + dy, color.red, color.green, color.blue)
        y += 1
        if err <= 0:
            err += 2 * y + 1
        if err > 0:
            x -= 1
            err -= 2 * x + 1