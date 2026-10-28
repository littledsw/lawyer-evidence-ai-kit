"""本地 OCR（macOS Vision，离线，不联网）与材料文字索引。

用途与边界：
- **目录识别**：把拍照/截图/扫描的《证据目录》变成结构化行（`table.py` 负责重建表格）；
- **材料索引**：给每个文件（含扫描件 PDF 逐页）留一份可检索的文字，用于
  ① 文件名没信息时（`IMG_0001.jpg`）把它归到正确的证据；② 聊天截图按时间排序。
文字索引只是**线索**，不是结论：OCR 会错字，凡据此改变排版的都要进待复核清单。

依赖：`pyobjc-framework-Vision` + `pyobjc-framework-Quartz`。
识别级别**必须 0（accurate）**：传 1（fast）中文会整段乱码，但数字仍对，极易被误判成成功。
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

LEVEL_ACCURATE = 0
LANGUAGES = ("zh-Hans", "en-US")


@dataclass
class OcrLine:
    text: str
    confidence: float
    x: float      # 左上原点，单位=像素
    y: float
    w: float
    h: float
    chars: list | None = None     # [(字符, x, y, w, h)]，仅在 with_chars=True 时给出

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h


def available() -> tuple[bool, str]:
    try:
        import Quartz  # noqa: F401
        import Vision  # noqa: F401
    except ImportError as exc:
        return False, (f"缺少 pyobjc Vision（{exc.name}）：uv pip install "
                       f"pyobjc-framework-Vision pyobjc-framework-Quartz")
    return True, "ok"


def ocr_image(path: Path | str, with_chars: bool = False) -> tuple[int, int, list[OcrLine]]:
    """图片 → (宽, 高, 文字行)。图片不出本机。

    `with_chars=True` 时附带**逐字符坐标**：OCR 会把同一视觉行里相邻两格的文字读成一行
    （实测：`中国石化加油IC卡台账 作管理和安排。`），要按列边界切开就必须有字符级坐标。
    """
    ok, msg = available()
    if not ok:
        raise RuntimeError(msg)
    import Quartz
    import Vision
    from Foundation import NSURL

    p = Path(path).expanduser().resolve()
    url = NSURL.fileURLWithPath_(str(p))
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if src is None:
        raise RuntimeError(f"读不了图片：{p}")
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    w = int(Quartz.CGImageGetWidth(cg))
    h = int(Quartz.CGImageGetHeight(cg))

    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(LEVEL_ACCURATE)
    req.setRecognitionLanguages_(list(LANGUAGES))
    req.setUsesLanguageCorrection_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    ok2, err = handler.performRequests_error_([req], None)
    if not ok2:
        raise RuntimeError(f"OCR 失败：{err}")

    lines: list[OcrLine] = []
    for obs in (req.results() or []):
        cand = obs.topCandidates_(1)
        if not cand:
            continue
        best = cand[0]
        bb = obs.boundingBox()          # 归一化，左下原点
        line = OcrLine(
            text=str(best.string()),
            confidence=float(best.confidence()),
            x=float(bb.origin.x) * w,
            y=(1.0 - float(bb.origin.y) - float(bb.size.height)) * h,
            w=float(bb.size.width) * w,
            h=float(bb.size.height) * h,
        )
        if with_chars:
            line.chars = _char_boxes(best, line.text, w, h)
        lines.append(line)
    return w, h, lines


def _char_boxes(cand, text: str, w: int, h: int) -> list:
    """逐个字符的包围盒（像素、左上原点）。取不到就返回空表（调用方退回整行处理）。"""
    out = []
    for i, ch in enumerate(text):
        try:
            res = cand.boundingBoxForRange_error_((i, 1), None)
            box = res[0] if isinstance(res, tuple) else res
            if box is None:
                return []
            if hasattr(box, "boundingBox"):        # 返回的是 VNRectangleObservation
                box = box.boundingBox()
            ox, oy = float(box.origin.x), float(box.origin.y)
            sw, sh = float(box.size.width), float(box.size.height)
        except Exception:                                        # noqa: BLE001
            return []
        out.append((ch, ox * w, (1.0 - oy - sh) * h, sw * w, sh * h))
    return out


def _largest_image(page) -> bytes | None:
    """取 PDF 页里最大的那张位图（扫描件的整页图像）。"""
    try:
        xo = (page.get("/Resources") or {}).get("/XObject") or {}
    except Exception:                                            # noqa: BLE001
        return None
    best, best_area = None, 0
    for k in xo:
        try:
            o = xo[k].get_object()
            if o.get("/Subtype") != "/Image":
                continue
            area = float(o.get("/Width") or 0) * float(o.get("/Height") or 0)
            if area > best_area:
                best, best_area = o, area
        except Exception:                                        # noqa: BLE001
            continue
    if best is None:
        return None
    try:
        return best.get_data()
    except Exception:                                            # noqa: BLE001
        return None


def ocr_pdf_pages(path: Path, max_pages: int | None = None) -> list[dict]:
    """PDF 每页的文字：有文字层直接用（更快更准），没有就 OCR 页内位图。"""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    out: list[dict] = []
    for i, page in enumerate(reader.pages):
        if max_pages is not None and i >= max_pages:
            break
        try:
            text = (page.extract_text() or "").strip()
        except Exception:                                        # noqa: BLE001
            text = ""
        if len(text) >= 20:
            out.append({"page": i + 1, "source": "text", "text": text, "lines": []})
            continue
        raw = _largest_image(page)
        if not raw:
            out.append({"page": i + 1, "source": "none", "text": "", "lines": []})
            continue
        tmp = Path("/tmp") / f"evkit-ocr-{path.stem}-{i}.png"
        try:
            from PIL import Image
            Image.open(io.BytesIO(raw)).convert("RGB").save(tmp, "PNG")
            _w, _h, lines = ocr_image(tmp)
        except Exception as e:                                   # noqa: BLE001
            out.append({"page": i + 1, "source": "error", "text": f"OCR 失败：{e}", "lines": []})
            continue
        finally:
            tmp.unlink(missing_ok=True)
        lines_sorted = sorted(lines, key=lambda l: (round(l.cy / 8), l.x))
        out.append({"page": i + 1, "source": "ocr", "text": "\n".join(l.text for l in lines_sorted),
                    "lines": [{"t": l.text, "c": round(l.confidence, 3)} for l in lines_sorted]})
    return out
