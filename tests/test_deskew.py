"""拍照件矫正：歪斜要能被估出来，正图不能被瞎转。"""
import pytest
from PIL import Image, ImageDraw

from evidence_kit import preprocess


def _page(tmp_path, angle=0.0, name="p.png"):
    im = Image.new("L", (900, 1200), 255)
    d = ImageDraw.Draw(im)
    for y in range(120, 1100, 46):
        d.rectangle([120, y, 780, y + 14], fill=0)
    if angle:
        im = im.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=255)
    p = tmp_path / name
    im.convert("RGB").save(p)
    return p


def test_estimate_angle_detects_rotation(tmp_path):
    p = _page(tmp_path, angle=2.0)
    assert abs(preprocess.estimate_angle(p) - 2.0) <= 0.75


def test_estimate_angle_zero_for_straight(tmp_path):
    p = _page(tmp_path)
    assert abs(preprocess.estimate_angle(p)) < preprocess.MIN_ANGLE + 0.3


def test_deskew_returns_original_when_straight(tmp_path):
    p = _page(tmp_path)
    out, angle = preprocess.deskew(p)
    assert out == p and abs(angle) < 1.0


def test_deskew_writes_new_file_when_skewed(tmp_path):
    p = _page(tmp_path, angle=3.0)
    out, angle = preprocess.deskew(p)
    assert out != p and out.exists() and abs(angle) >= preprocess.MIN_ANGLE
