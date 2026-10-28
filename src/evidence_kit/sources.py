"""原始材料 → 排版单元。

铁律：**原件字节不动**。图片以原始文件直接嵌入 PDF（JPEG 走 DCTDecode 直通，PNG 走
FlateDecode 逐像素无损），不裁切、不锐化、不重压缩、不旋转。长截图靠「同一张图重复引用 +
不同裁剪窗」切成多页，因此切片也不会重新编码。
"""
from __future__ import annotations

import glob
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import layout as L
from .model import Unit, natural_key

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".gif"}
PDF_EXT = {".pdf"}
# 明确的「暂不支持」类型：报错而不是静默跳过（静默跳过 = 证据悄悄丢了）
UNSUPPORTED = {".doc", ".docx", ".xlsx", ".xls", ".txt", ".md", ".m4a", ".mp3", ".wav"}


@dataclass
class ProbeResult:
    units: list[Unit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)


def discover(spec, base: Path) -> list[Path]:
    """把目录 / glob / 单文件 / 列表统一成文件列表（自然序）。"""
    if spec is None:
        return []
    out: list[Path] = []
    items = spec if isinstance(spec, (list, tuple)) else [spec]
    for it in items:
        p = Path(it)
        if not p.is_absolute():
            p = base / p
        if any(ch in str(it) for ch in "*?["):
            out.extend(Path(m) for m in sorted(glob.glob(str(p)), key=natural_key))
        elif p.is_dir():
            out.extend(sorted((c for c in p.iterdir() if c.is_file()), key=natural_key))
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(f"找不到材料：{p}")
    # 去重但保序
    seen, uniq = set(), []
    for p in out:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(p)
    return uniq


def _sips_dims(path: Path) -> tuple[int, int]:
    r = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
                       capture_output=True, text=True)
    w = h = 0
    for line in r.stdout.splitlines():
        if "pixelWidth" in line:
            w = int(line.split(":")[1])
        elif "pixelHeight" in line:
            h = int(line.split(":")[1])
    return w, h


def image_dims(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        w, h = _sips_dims(path)
        if not (w and h):
            raise ValueError(f"读不出图片尺寸：{path}")
        return w, h


def _to_jpeg(path: Path) -> Path:
    """HEIC/TIFF 等 → 临时 JPEG（一次性有损，需在报告里点名）。"""
    tmp = Path(tempfile.mkdtemp(prefix="evkit-")) / (path.stem + ".jpg")
    subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "100",
                    str(path), "--out", str(tmp)], capture_output=True, check=True)
    return tmp


def _slice_units(path: Path, w: int, h: int) -> list[Unit]:
    """超长截图切片：宽度吃满版心，纵向按页高切，切缝留重叠。"""
    if h / w <= L.SLICE_ASPECT:
        return [Unit(path=path, kind="image", width_px=w, height_px=h,
                     slice_y1_px=h, full_height_px=h)]
    s = L.CONTENT_W / w                       # pt / px
    vis = L.CONTENT_H / s                     # 每片可见像素高
    step = (L.CONTENT_H - L.SLICE_OVERLAP) / s
    n = 1 + int(-(-(h - vis) // step)) if h > vis else 1
    n = max(1, n)
    units = []
    for i in range(n):
        y0 = int(round(i * step))
        y1 = min(h, int(round(y0 + vis)))
        units.append(Unit(path=path, kind="image", width_px=w, height_px=y1 - y0,
                          slice_index=i, slice_count=n, slice_y0_px=y0, slice_y1_px=y1,
                          full_height_px=h))
    return units


def probe(paths: list[Path]) -> ProbeResult:
    res = ProbeResult()
    for p in paths:
        ext = p.suffix.lower()
        if ext in PDF_EXT:
            try:
                from pypdf import PdfReader
                r = PdfReader(str(p))
                for i, pg in enumerate(r.pages):
                    mb = pg.mediabox
                    res.units.append(Unit(path=p, kind="pdf", page=i,
                                          width_px=float(mb.width), height_px=float(mb.height)))
                if not r.pages:
                    res.warnings.append(f"{p.name}：PDF 无页面")
            except Exception as e:                                   # noqa: BLE001
                res.skipped.append((p, f"PDF 解析失败：{e}"))
        elif ext in IMAGE_EXT:
            src = p
            if ext in {".heic", ".heif", ".tif", ".tiff", ".bmp"}:
                try:
                    src = _to_jpeg(p)
                    res.warnings.append(f"{p.name}：{ext} 已转 JPEG(q100) 后嵌入（一次性有损）")
                except Exception as e:                               # noqa: BLE001
                    res.skipped.append((p, f"转码失败：{e}"))
                    continue
            try:
                w, h = image_dims(src)
            except Exception as e:                                   # noqa: BLE001
                res.skipped.append((p, f"读不出尺寸：{e}"))
                continue
            units = _slice_units(src, w, h)
            if units[0].slice_count > 1:
                res.warnings.append(
                    f"{p.name}：{w}×{h}px 超长图，已切成 {units[0].slice_count} 页"
                    f"（切缝重叠 {L.SLICE_OVERLAP / L.MM:.0f}mm，原图不缩放不重编码）")
            res.units.extend(units)
        else:
            res.skipped.append((p, f"暂不支持的类型（{ext or '无扩展名'}）"))
    return res


def effective_dpi(unit: Unit, cell_w: float = L.CONTENT_W, cell_h: float = L.CONTENT_H) -> float:
    """该单元印到纸上的实际分辨率，用于「会不会糊」的硬判断。"""
    dw, _dh = L.fit(unit.width_px, unit.height_px, cell_w, cell_h)
    return unit.width_px / (dw / 72.0) if dw else 0.0
