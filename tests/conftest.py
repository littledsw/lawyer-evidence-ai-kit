import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageDraw  # noqa: E402


@pytest.fixture
def mk_jpeg(tmp_path):
    """造一张带细字的 JPEG（用来验字节直通与清晰度检查）。"""
    def _mk(name: str, w: int = 1170, h: int = 2532) -> Path:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        im = Image.new("RGB", (w, h), (240, 240, 240))
        d = ImageDraw.Draw(im)
        for y in range(40, h, 60):
            d.line([20, y, w - 20, y], fill=(30, 30, 30), width=2)
        im.save(p, "JPEG", quality=95, subsampling=0)
        return p
    return _mk


@pytest.fixture
def mk_pdf(tmp_path):
    def _mk(name: str, pages: int = 2) -> Path:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        c = canvas.Canvas(str(p), pagesize=A4)
        for i in range(pages):
            c.drawString(60, 780, f"page {i + 1}")
            c.showPage()
        c.save()
        return p
    return _mk
