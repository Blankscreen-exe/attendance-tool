"""Draws the app icons used when the site is installed on a phone.

Only needed if you want to change the icon. Requires Pillow, which the app
itself does not use:

    pip install pillow
    python assets/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "attendance" / "static" / "attendance" / "icons"
BACKGROUND = "#0f172a"
FACE = "#ffffff"
ACCENT = "#10b981"
OVERSAMPLE = 4  # draw large, then shrink, for smooth edges


def clock_icon(size, glyph=0.66, rounded=True):
    """A clock on a dark tile. `glyph` is the clock's width as a share of the tile."""
    big = size * OVERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if rounded:
        draw.rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 0.22, fill=BACKGROUND)
    else:
        draw.rectangle((0, 0, big, big), fill=BACKGROUND)

    centre = big / 2
    radius = big * glyph / 2
    stroke = max(2, round(big * glyph * 0.11))
    draw.ellipse((centre - radius, centre - radius, centre + radius, centre + radius), outline=FACE, width=stroke)

    def hand(dx, dy):
        end = (centre + dx * radius, centre + dy * radius)
        draw.line((centre, centre, *end), fill=FACE, width=stroke)
        cap = stroke / 2
        draw.ellipse((end[0] - cap, end[1] - cap, end[0] + cap, end[1] + cap), fill=FACE)

    hand(0, -0.58)  # minute hand, straight up
    hand(0.42, 0)  # hour hand, pointing right
    pivot = stroke * 0.8
    draw.ellipse((centre - pivot, centre - pivot, centre + pivot, centre + pivot), fill=ACCENT)
    return image.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    clock_icon(192).save(OUT / "icon-192.png")
    clock_icon(512).save(OUT / "icon-512.png")
    # "Maskable" icons get cropped to a circle or squircle by the phone, so the
    # tile fills the square and the clock stays inside the safe middle.
    clock_icon(512, glyph=0.5, rounded=False).save(OUT / "icon-maskable-512.png")
    clock_icon(180, glyph=0.6, rounded=False).save(OUT / "apple-touch-icon.png")
    clock_icon(32, glyph=0.72).save(OUT / "favicon-32.png")
    print(f"Icons written to {OUT}")


if __name__ == "__main__":
    main()
