# rgbmatrix_sim/bdf.py
class Glyph:
    def __init__(self):
        self.encoding = None
        self.dwx = 0        # advance width
        self.bb_w = 0
        self.bb_h = 0
        self.bb_xoff = 0
        self.bb_yoff = 0
        self.bitmap_rows = []  # list of ints, one per row, MSB-aligned


class BDFFont:
    def __init__(self):
        self.glyphs = {}          # encoding -> Glyph
        self.font_ascent = 0
        self.font_descent = 0
        self.bbox_w = 0
        self.bbox_h = 0

    @property
    def height(self):
        return self.bbox_h

    @property
    def baseline(self):
        return self.font_ascent

    def load(self, path):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        i = 0
        glyph = None
        reading_bitmap = False
        bitmap_bytes_per_row = 0

        while i < len(lines):
            line = lines[i].rstrip("\n")
            parts = line.split()
            if not parts:
                i += 1
                continue
            key = parts[0]

            if key == "FONTBOUNDINGBOX":
                self.bbox_w = int(parts[1])
                self.bbox_h = int(parts[2])

            elif key == "FONT_ASCENT":
                self.font_ascent = int(parts[1])

            elif key == "FONT_DESCENT":
                self.font_descent = int(parts[1])

            elif key == "STARTCHAR":
                glyph = Glyph()
                reading_bitmap = False

            elif key == "ENCODING":
                glyph.encoding = int(parts[1])

            elif key == "DWIDTH":
                glyph.dwx = int(parts[1])

            elif key == "BBX":
                glyph.bb_w = int(parts[1])
                glyph.bb_h = int(parts[2])
                glyph.bb_xoff = int(parts[3])
                glyph.bb_yoff = int(parts[4])
                bitmap_bytes_per_row = (glyph.bb_w + 7) // 8

            elif key == "BITMAP":
                reading_bitmap = True
                i += 1
                for _ in range(glyph.bb_h):
                    hex_row = lines[i].strip()
                    row_int = int(hex_row, 16) if hex_row else 0
                    total_bits = bitmap_bytes_per_row * 8
                    glyph.bitmap_rows.append((row_int, total_bits))
                    i += 1
                reading_bitmap = False
                continue

            elif key == "ENDCHAR":
                if glyph.encoding is not None and glyph.encoding >= 0:
                    self.glyphs[glyph.encoding] = glyph
                glyph = None

            i += 1

        if self.font_ascent == 0 and self.font_descent == 0:
            # Some BDFs omit these; approximate from bounding box.
            self.font_ascent = self.bbox_h + min(g.bb_yoff for g in self.glyphs.values()) if self.glyphs else self.bbox_h
            self.font_descent = self.bbox_h - self.font_ascent

    def get_glyph(self, ch):
        return self.glyphs.get(ord(ch))