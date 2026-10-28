"""拍照件的图像校正：歪斜矫正（deskew）。

扫描件通常是正的，手机拍的纸件平均歪 1–3°，这会让 OCR 的行基线倾斜、行切分错位
（实测：歪 2° 时同一行的字被切成两行，表格重建就散了）。

做法是最经典的**投影profile**：把图转灰度二值化，按候选角度旋转后统计每行黑像素数，
真实水平角度会让「行缝」最清晰（行和的方差最大）。先缩到 800px 粗估，再在原图上转。
"""
from __future__ import annotations

from pathlib import Path

MAX_ANGLE = 4.0
STEP = 0.5
MIN_ANGLE = 0.4          # 小于这个角度不值得转（旋转本身会插值损失）
OCR_MIN_ANGLE = 2.5      # 供 OCR 用的阈值：实测 2° 校正反而掉准确率（插值损失），3.5° 不校正直接失败


def _score(gray, angle: float) -> float:
    """某个角度下的「行投影方差」，越大说明行越平。"""
    from PIL import Image
    im = gray.rotate(angle, resample=Image.BILINEAR, fillcolor=255)
    px = im.load()
    w, h = im.size
    step_y = max(1, h // 400)
    vals = []
    for y in range(0, h, step_y):
        s = 0
        for x in range(0, w, 3):
            s += 255 - px[x, y]
        vals.append(s)
    if not vals:
        return 0.0
    mean = sum(vals) / len(vals)
    return sum((v - mean) ** 2 for v in vals) / len(vals)


def estimate_angle(path: Path, max_angle: float = MAX_ANGLE, step: float = STEP) -> float:
    """估计**歪斜角度**（度；逆时针为正）。矫正时反向旋转 −angle 即可摆正。"""
    from PIL import Image
    with Image.open(path) as im:
        gray = im.convert("L")
        if gray.width > 800:
            gray = gray.resize((800, int(gray.height * 800 / gray.width)), Image.LANCZOS)
        best_a, best_s = 0.0, -1.0
        a = -max_angle
        while a <= max_angle + 1e-9:
            s = _score(gray, a)
            if s > best_s:
                best_a, best_s = a, s
            a += step
    # best_a 是「能把行摆平」的矫正角度，歪斜方向与之相反
    return round(-best_a, 2)


def deskew(path: Path, out: Path | None = None, force: bool = False) -> tuple[Path, float]:
    """矫正歪斜，返回（可用路径, 角度）。角度太小或无需矫正时原样返回。"""
    from PIL import Image
    angle = estimate_angle(path)
    if not force and abs(angle) < MIN_ANGLE:
        return path, angle
    with Image.open(path) as im:
        fixed = im.convert("RGB").rotate(-angle, resample=Image.BICUBIC, expand=True,
                                         fillcolor="white")
    target = out or path.with_name(f"{path.stem}_deskew.png")
    fixed.save(target, "PNG")
    return target, angle
