"""PNG/ICO-varianten van static/favicon.svg (optie B) tekenen met Pillow: python dev/favicon.py sqlledger_web/static"""
import sys
from PIL import Image, ImageDraw
out = sys.argv[1]
K = 16; S = 64 * K
BLUE, GOLD, WHITE = (31, 78, 121, 255), (242, 193, 78, 255), (255, 255, 255, 255)
def big(transparent_corners=True):
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if transparent_corners:
        d.rounded_rectangle([0, 0, S - 1, S - 1], radius=14 * K, fill=BLUE)
    else:
        d.rectangle([0, 0, S - 1, S - 1], fill=BLUE)
    for x, col in ((12, WHITE), (38, GOLD)):
        for y in (20, 28):
            d.rounded_rectangle([x * K, y * K, (x + 14) * K, (y + 6) * K], radius=2 * K, fill=col)
    d.rounded_rectangle([6 * K, 36 * K, 58 * K, 40 * K], radius=2 * K, fill=WHITE)
    d.polygon([(32 * K, 40 * K), (23 * K, 54 * K), (41 * K, 54 * K)], fill=WHITE)
    return im
b = big()
b.resize((32, 32), Image.LANCZOS).save(f"{out}/favicon-32.png")
b.save(f"{out}/favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
big(False).resize((180, 180), Image.LANCZOS).save(f"{out}/apple-touch-icon.png")   # iOS rondt zelf af
