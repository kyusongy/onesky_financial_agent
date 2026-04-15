"""Generate a minimal OneSky .icns icon.

Produces `scripts/icon.icns`. Re-run whenever you want to refresh branding.
Requires Pillow. Uses macOS `iconutil` to pack the iconset.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "icon.icns"
ICONSET = ROOT / "icon.iconset"


def _make_master(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (30, 58, 95, 255))  # OneSky navy
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/SFNS.ttf", int(size * 0.55))
    except OSError:
        font = ImageFont.truetype(
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf", int(size * 0.55)
        )
    text = "OS"
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        ((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1]),
        text,
        fill=(255, 255, 255, 255),
        font=font,
    )
    return img


def main() -> None:
    if ICONSET.exists():
        shutil.rmtree(ICONSET)
    ICONSET.mkdir()
    master = _make_master()
    # iconutil expects these exact filenames.
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    for px in sizes:
        img = master.resize((px, px), Image.LANCZOS)
        img.save(ICONSET / f"icon_{px}x{px}.png")
        if px * 2 in sizes or px == 512:
            img2 = master.resize((px * 2, px * 2), Image.LANCZOS)
            img2.save(ICONSET / f"icon_{px}x{px}@2x.png")
    subprocess.check_call(["iconutil", "-c", "icns", str(ICONSET), "-o", str(OUT)])
    shutil.rmtree(ICONSET)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
