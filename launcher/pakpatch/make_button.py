"""Draw the login button that replaces TapTap's: 370x70, "💗 MIKURO LOGIN 💗".

Writes login_button.png next to this script; build.py turns it into the game's DDS format.
Flat colours on purpose: the DDS is stored zlib-compressed in DATA.PAK and has to fit in the
space of the original (about 8 KB), so gradients and noise are out.

Usage: python make_button.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "login_button.png"
W, H = 370, 70
SCALE = 4                                   # draw large, downsample for clean edges

FILL = (225, 60, 135, 255)                  # deep pink
EDGE = (150, 25, 90, 255)                   # darker rim
TEXT = (255, 255, 255, 255)
SHADOW = (120, 15, 70, 255)
FONT_TEXT = Path("C:/Windows/Fonts/seguibl.ttf")    # Segoe UI Black
FONT_EMOJI = Path("C:/Windows/Fonts/seguiemj.ttf")  # Segoe UI Emoji (colour)
LABEL = "MIKURO LOGIN"
HEART = "\U0001F497"


def main() -> int:
    s = SCALE
    img = Image.new("RGBA", (W * s, H * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, W * s - 1, H * s - 1], radius=12 * s, fill=EDGE)
    d.rounded_rectangle([3 * s, 3 * s, W * s - 1 - 3 * s, H * s - 1 - 3 * s], radius=10 * s, fill=FILL)

    font = ImageFont.truetype(str(FONT_TEXT), 30 * s)
    emoji = ImageFont.truetype(str(FONT_EMOJI), 34 * s)
    tw = d.textlength(LABEL, font=font)
    ew = d.textlength(HEART, font=emoji)
    gap = 10 * s
    total = ew + gap + tw + gap + ew
    x = (W * s - total) / 2
    cy = H * s / 2

    d.text((x, cy), HEART, font=emoji, anchor="lm", embedded_color=True)
    tx = x + ew + gap
    d.text((tx + 2 * s, cy + 2 * s), LABEL, font=font, fill=SHADOW, anchor="lm")
    d.text((tx, cy), LABEL, font=font, fill=TEXT, anchor="lm")
    d.text((tx + tw + gap, cy), HEART, font=emoji, anchor="lm", embedded_color=True)

    img.resize((W, H), Image.LANCZOS).save(OUT)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
