"""Generate the PWA icons (a stylised business card) into app/static/icons/."""
from pathlib import Path
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"
OUT.mkdir(parents=True, exist_ok=True)

def icon(size: int) -> Image.Image:
    s = size / 512
    img = Image.new("RGB", (size, size), "#1f6f5c")
    d = ImageDraw.Draw(img)
    # card
    d.rounded_rectangle([96 * s, 150 * s, 416 * s, 362 * s], radius=22 * s, fill="#ffffff")
    # avatar circle and text lines
    d.ellipse([130 * s, 190 * s, 210 * s, 270 * s], fill="#5cc0a2")
    d.rounded_rectangle([232 * s, 200 * s, 380 * s, 222 * s], radius=11 * s, fill="#1f6f5c")
    d.rounded_rectangle([232 * s, 240 * s, 340 * s, 258 * s], radius=9 * s, fill="#9aa5a0")
    d.rounded_rectangle([130 * s, 300 * s, 380 * s, 316 * s], radius=8 * s, fill="#dde2df")
    return img

for size in (192, 512):
    icon(size).save(OUT / f"icon-{size}.png", optimize=True)
print("icons written to", OUT)
