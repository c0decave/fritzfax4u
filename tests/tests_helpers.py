"""Small builders shared by tests."""
from pathlib import Path

from PIL import Image, ImageDraw


def one_page_pdf(path: Path, pages: int = 1, text: str = "Testfax") -> Path:
    imgs = []
    for n in range(pages):
        img = Image.new("RGB", (1240, 1754), "white")  # A4 at 150 dpi
        d = ImageDraw.Draw(img)
        d.rectangle([118, 118, 413, 236], fill="black")
        d.text((118, 300), f"{text} {n + 1}/{pages}", fill="black")
        imgs.append(img)
    imgs[0].save(path, save_all=True, append_images=imgs[1:], resolution=150)
    return path
