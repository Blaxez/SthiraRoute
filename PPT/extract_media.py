"""Pull the SIH logo artwork out of the official template for reuse."""

import zipfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
SRC = HERE / "SIH2026-IDEA-Presentation-Format.pptx"
OUT = HERE / "assets"
OUT.mkdir(exist_ok=True)

with zipfile.ZipFile(SRC) as z:
    media = [n for n in z.namelist() if n.startswith("ppt/media/")]
    for name in media:
        data = z.read(name)
        dest = OUT / ("sih-" + Path(name).name)
        dest.write_bytes(data)
        try:
            with Image.open(dest) as im:
                print(f"{dest.name:24s} {im.size}  {len(data) / 1024:.0f} KB")
        except Exception:
            print(f"{dest.name:24s} (not an image) {len(data) / 1024:.0f} KB")
