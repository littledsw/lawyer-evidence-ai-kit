"""PDF 渲染：目录页（不编页码）+ 证据页（1..N 连编）。

"不失真"落在三处实现细节上：
1. 图片用 `ImageReader(原始文件)` 直嵌 —— JPEG 走 DCTDecode 直通（字节不变），
   PNG 走 FlateDecode（逐像素无损），全程不经 PIL 重存；
2. 长截图切片用「同一张图重复引用 + 裁剪窗」，切缝不重新编码；
3. 页码与组标只画在页脚留白区（底部 18mm），版面内容区高度已扣掉它，永不压字。
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab import rl_config
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.flowables import Flowable

from . import layout as L
from .model import Catalog, Group

# 版式与字体**采样自真实卷宗文件** `崔海涛证据目录.docx`（可直接复现）：
#   页面 A4；页边距 上下 1440 twips(25.4mm)、左右 1800 twips(31.75mm)
#   标题「证据目录」仿宋 22pt 居中；表头与表格正文仿宋 12pt；行距固定 25pt
#   表格宽 100% 版心，四列 711/2063/4765/983 twips → 12.2 / 35.5 / 82.0 / 16.9 mm
#   对齐：序号、名称、页数居中；证明目的两端对齐；无粗体
SAMPLE = {
    "margin_lr_mm": 31.75, "margin_tb_mm": 25.4,
    "title_pt": 22.0, "body_pt": 12.0, "line_pt": 25.0,
    "col_twips": (711, 2063, 4765, 983),
}

_FONT_DIRS = ("~/Library/Fonts", "/Library/Fonts",
              "/System/Library/Fonts/Supplemental", "/System/Library/Fonts")
_FANGSONG_HINTS = ("仿宋", "fangsong", "stfangsong", "FangSong_GB2312")
_SONGTI_HINTS = ("Songti", "songti", "STS", "STSong")


def _find_font(hints) -> str | None:
    """按名称在系统/用户字体目录里找字体文件（.ttf/.ttc）。"""
    import os
    for d in _FONT_DIRS:
        root = Path(os.path.expanduser(d))
        if not root.exists():
            continue
        for f in sorted(root.iterdir()):
            if f.suffix.lower() not in {".ttf", ".ttc", ".otf"}:
                continue
            if any(h.lower() in f.name.lower() for h in hints):
                return str(f)
    return None


def _register(family: str, path: str, font_index: int | None = None) -> bool:
    """注册字体；.ttc 需要挑子字体，逐个试到能注册为止。"""
    from reportlab.pdfbase.ttfonts import TTFont
    if path.lower().endswith(".ttc"):
        for idx in range(0, 8):
            try:
                pdfmetrics.registerFont(TTFont(family, path, subfontIndex=idx))
                return True
            except Exception:                                    # noqa: BLE001
                continue
        return False
    try:
        pdfmetrics.registerFont(TTFont(family, path))
        return True
    except Exception:                                            # noqa: BLE001
        return False


FONT_BODY = "EK-Body"
FONT_BOLD = "EK-Bold"
_fonts_ready = False


FONT_USED = {"body": "", "file": "", "note": ""}
_fonts_ready = False


def ensure_fonts(override: str | None = None) -> None:
    """字体优先级：用户指定 → 仿宋 → 宋体 → 黑体。实际用了哪个写进 FONT_USED，报告里点名。"""
    global _fonts_ready
    if _fonts_ready:
        return
    cands: list[tuple[str, str]] = []
    if override:
        cands.append(("指定", override))
    fang = _find_font(_FANGSONG_HINTS)
    if fang:
        cands.append(("仿宋", fang))
    song = _find_font(_SONGTI_HINTS)
    if song:
        cands.append(("宋体", song))
    for label, path in cands:
        if _register(FONT_BODY, path):
            _register(FONT_BOLD, path)
            FONT_USED.update(body=label, file=path,
                             note="" if label == "仿宋" else
                             f"本机未装仿宋，版式按采样值输出、字形用{label}近似")
            _fonts_ready = True
            return
    raise RuntimeError("找不到可用中文字体；用 --font 指定一个 .ttf/.ttc")


# ---------------------------------------------------------------- 目录页
def _para(text: str, font: str, size: float, align=None, leading=None) -> Paragraph:
    st = ParagraphStyle("x", fontName=font, fontSize=size,
                        leading=leading or size * 1.55, alignment=align or 0,
                        wordWrap="CJK")      # 中文按字断行（否则标点会掉到行首）
    return Paragraph(text, st)


def _txt(s: str) -> str:
    return escape(str(s)).replace("\n", "<br/>")


_NUM_ITEM = __import__("re").compile(r"(?:^|；)\s*\d+\s*[:：]")


def _proves_text(text: str) -> str:
    """证明目的正文归一：折行接回，编号项（1: / 2:）各自起段。"""
    import re as _re
    t = _re.sub(r"[ \t\r]+", " ", str(text)).strip()
    t = _re.sub(r"\n\s*", "", t)
    t = _re.sub(r"(?<=[；;。])\s*(?=\d{1,2}\s*[:：])", "\n", t)
    t = _re.sub(r"^\s*(?=\d{1,2}\s*[:：])", "", t)
    return t


def _proves_html(text: str) -> str:
    """「1:…；2:…」这种编号式证明目的按条断行；普通行文交给 Paragraph 自动折行。"""
    t = _txt(text)
    if _NUM_ITEM.search(text):
        t = t.replace("；", "；<br/>")
    return re.sub(r"\s*<br/>\s*", "<br/>", t)      # 换行与 <br/> 叠在一起会多出空行


_NO_LINE_START = "，。、；：？！）】》」』”’%,.;:?!)]}"
_NO_LINE_END = "（【《「『“‘([{"


def wrap_cjk(text: str, width_pt: float, font: str, size: float) -> str:
    """中文断行 + 禁则处理，返回带 <br/> 的文本。

    Word 会自动避头尾、reportlab 不会——不处理会出现「，各劳务…」或「；」独占一行。
    策略：**标点不许起行时，把前一个字一起带到下一行**（绝不超出单元格宽度，
    所以 Paragraph 不会二次断行）。
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    def one(seg: str) -> list[str]:
        lines: list[str] = []
        cur, cur_w = "", 0.0
        for ch in seg:
            w = stringWidth(ch, font, size)
            if cur and cur_w + w > width_pt:
                if ch in _NO_LINE_START and len(cur) > 1:
                    carry = cur[-1]
                    lines.append(cur[:-1])
                    cur, cur_w = carry + ch, stringWidth(carry + ch, font, size)
                    continue
                lines.append(cur)
                cur, cur_w = ch, w
            else:
                cur += ch
                cur_w += w
        if cur:
            lines.append(cur)
        return lines

    out: list[str] = []
    for seg in str(text).split("\n"):
        seg = "".join(seg.split())
        if seg:
            out.extend(one(seg))
    return "<br/>".join(out)


def catalog_pdf(path: Path, catalog: Catalog) -> int:
    """目录页：标题 + 四列表格（序号/名称/证明目的/页数）+ 落款。

    版式全部取自 `SAMPLE`（从真实卷宗 docx 采样）：页边距、字号、行距、列宽比、对齐方式。
    """
    ensure_fonts()
    side = SAMPLE["margin_lr_mm"] * L.MM
    tb = SAMPLE["margin_tb_mm"] * L.MM
    doc = SimpleDocTemplate(str(path), pagesize=L.A4,
                            leftMargin=side, rightMargin=side,
                            topMargin=tb, bottomMargin=tb,
                            title=catalog.case.title, author=catalog.case.agent)
    body_pt = SAMPLE["body_pt"]
    lead = SAMPLE["line_pt"]

    story = [
        _para(_txt(catalog.case.title), FONT_BODY, SAMPLE["title_pt"], TA_CENTER, leading=lead * 1.4),
        Spacer(1, 4 * L.MM),
    ]

    total_tw0 = sum(SAMPLE["col_twips"])
    table_w0 = L.A4[0] - 2 * side
    widths_hint = [table_w0 * tw / total_tw0 for tw in SAMPLE["col_twips"]]
    head = [_para(t, FONT_BODY, body_pt, TA_CENTER, leading=lead)
            for t in ("序号", "证据材料名称", "证明目的", "页数")]
    data = [head]
    spans = []
    for g in catalog.groups:
        start_row = len(data)
        proves = g.proves if g.proves_source == "user" and g.proves else (g.proves or "（证明目的待补）")
        for i, r in enumerate(g.rows):
            data.append([
                _para(str(r.no), FONT_BODY, body_pt, TA_CENTER, leading=lead),
                _para(wrap_cjk(r.name, widths_hint[1] - 10, FONT_BODY, body_pt)
                      if r.name else "", FONT_BODY, body_pt, TA_CENTER, leading=lead),
                _para(wrap_cjk(_proves_text(proves), widths_hint[2] - 10, FONT_BODY, body_pt)
                      if i == 0 else "", FONT_BODY, body_pt, 0, leading=lead),
                _para(g.pages_label if i == 0 else "", FONT_BODY, body_pt, TA_CENTER, leading=lead),
            ])
        if len(g.rows) > 1:
            end_row = len(data) - 1
            spans.append(("SPAN", (2, start_row), (2, end_row)))
            spans.append(("SPAN", (3, start_row), (3, end_row)))

    total_tw = sum(SAMPLE["col_twips"])
    table_w = L.A4[0] - 2 * side                      # 表格与原文件一致：占满版心
    widths = [table_w * tw / total_tw for tw in SAMPLE["col_twips"]]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, (0, 0, 0)),
        ("BOX", (0, 0), (-1, -1), 1.2, (0, 0, 0)),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, (0, 0, 0)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        *spans,
    ]))
    story.append(t)
    story.append(Spacer(1, 8 * L.MM))

    story.append(_SignBlock(catalog.case))
    doc.build(story)
    from pypdf import PdfReader
    return len(PdfReader(str(path)).pages)


# ---------------------------------------------------------------- PDF 页变换
def _mm(A, B):
    a, b, c, d, e, f = A
    A2, B2, C2, D2, E2, F2 = B
    return (a * A2 + c * B2, b * A2 + d * B2,
            a * C2 + c * D2, b * C2 + d * D2,
            a * E2 + c * F2 + e, b * E2 + d * F2 + f)


def _apply(M, x, y):
    a, b, c, d, e, f = M
    return (a * x + c * y + e, b * x + d * y + f)


def _rot(theta_deg: float):
    import math
    t = math.radians(theta_deg)
    cs, sn = math.cos(t), math.sin(t)
    return (cs, sn, -sn, cs, 0.0, 0.0)


def pdf_page_ctm(page, cell_w: float, cell_h: float, tx: float, ty: float):
    """源 PDF 页 → 目标单元格的变换矩阵。

    必须处理 `/Rotate`：扫描仪常把 A4 竖页存成「内容横躺 + /Rotate 270」，
    pypdf 合并时**不会**自动应用它——不处理就会印出侧躺的一页（实测社保记录就是）。
    """
    mb = page.mediabox
    W, H = float(mb.width), float(mb.height)
    rot = int(getattr(page, "rotation", 0) or 0) % 360
    cx, cy = (float(mb.left) + float(mb.right)) / 2, (float(mb.bottom) + float(mb.top)) / 2

    M = (1, 0, 0, 1, -cx, -cy)
    M = _mm(_rot(-rot), M)                 # 绕页面中心把内容摆正
    M = _mm((1, 0, 0, 1, cx, cy), M)

    corners = [_apply(M, x, y) for x, y in
               ((float(mb.left), float(mb.bottom)), (float(mb.right), float(mb.bottom)),
                (float(mb.left), float(mb.top)), (float(mb.right), float(mb.top)))]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    x0, y0 = min(xs), min(ys)
    vis_w, vis_h = max(xs) - x0, max(ys) - y0
    s = min(cell_w / vis_w, cell_h / vis_h)

    M = _mm((1, 0, 0, 1, -x0, -y0), M)
    M = _mm((s, 0, 0, s, 0, 0), M)
    ox = tx + (cell_w - vis_w * s) / 2
    oy = ty + (cell_h - vis_h * s) / 2
    return _mm((1, 0, 0, 1, ox, oy), M)


# ---------------------------------------------------------------- 证据页
def _draw_image_unit(c: canvas.Canvas, unit, cell_w: float, cell_h: float) -> None:
    """在**已平移到单元格原点**的画布上画图；单元格内居中，越界部分由裁剪窗裁掉。"""
    from reportlab.lib.utils import ImageReader
    dw, dh = L.fit(unit.width_px, unit.height_px, cell_w, cell_h)
    x = (cell_w - dw) / 2
    y = (cell_h - dh) / 2
    c.saveState()
    p = c.beginPath()
    p.rect(x, y, dw, dh)
    c.clipPath(p, stroke=0, fill=0)
    reader = ImageReader(str(unit.path))
    if unit.slice_count == 1:
        c.drawImage(reader, x, y, dw, dh)
    else:
        s = dw / unit.width_px
        full_h = unit.full_height_px * s
        y_base = y + dh - full_h + unit.slice_y0_px * s
        c.drawImage(reader, x, y_base, dw, full_h)
    c.restoreState()


def _content_page(page, per_page: int) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=L.A4)
    n = max(1, len(page.units))            # 组末页不满时，剩下的单元吃满整页（不缩成半格）
    cell_w = page.cell_w if n == per_page else (L.CONTENT_W - L.GAP * (n - 1)) / n
    for idx, unit in enumerate(page.units):
        if unit.kind != "image":
            continue
        cx, cy = L.cell_origin(idx, n)
        c.saveState()
        c.translate(cx, cy)
        _draw_image_unit(c, unit, cell_w, page.cell_h)
        c.restoreState()
    c.showPage()
    c.save()
    return buf.getvalue()


def _footer_page(no: int, label: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=L.A4)
    c.setFont(FONT_BODY, 9)
    c.setFillGray(0.30)
    c.drawCentredString(L.A4[0] / 2, 9 * L.MM, f"第 {no} 页")
    c.drawRightString(L.A4[0] - L.MARGIN_R, 9 * L.MM, label)
    c.showPage()
    c.save()
    return buf.getvalue()


def evidence_pdf(path: Path, plan: L.Plan, catalog: Catalog) -> list[tuple[int, str]]:
    from pypdf import PdfReader, PdfWriter, Transformation

    writer = PdfWriter()
    labels: list[tuple[int, str]] = []
    for no, page in enumerate(plan.pages, start=1):
        g: Group = catalog.groups[page.group_index]
        label = f"证据{g.no_label}"
        out = PdfReader(io.BytesIO(_content_page(page, plan.per_page))).pages[0]
        n = max(1, len(page.units))
        cell_w = page.cell_w if n == plan.per_page else (L.CONTENT_W - L.GAP * (n - 1)) / n
        for idx, unit in enumerate(page.units):
            if unit.kind != "pdf":
                continue
            src = PdfReader(str(unit.path)).pages[unit.page]
            cx, cy = L.cell_origin(idx, n)
            ctm = pdf_page_ctm(src, cell_w, page.cell_h, cx, cy)
            out.merge_transformed_page(src, Transformation(ctm=ctm))
        out.merge_page(PdfReader(io.BytesIO(_footer_page(no, label))).pages[0])
        writer.add_page(out)
        labels.append((no, label))

    buf = io.BytesIO()
    writer.write(buf)
    Path(path).write_bytes(buf.getvalue())
    return labels


class _SignBlock(Flowable):
    """落款三行。日期留空时按固定间距画「年　月　日」—— Paragraph 会折叠空白，只能在这里画。"""

    LINE = 9 * L.MM
    GAP = 20 * L.MM          # 「年」「月」「日」之间的填空宽度

    def __init__(self, case):
        super().__init__()
        self.case = case

    def wrap(self, avail_width, avail_height):
        self.width = avail_width
        self.height = 2 * self.LINE + 6 * L.MM
        return self.width, self.height

    def draw(self):
        c = self.case
        self.canv.setFont(FONT_BODY, SAMPLE["body_pt"])
        self.canv.setFillGray(0)
        top = self.height - 4 * L.MM
        self.canv.drawString(0, top, f"提交人：{c.submitter}")
        self.canv.drawString(0, top - self.LINE, f"代理人：{c.agent}")
        y = top - 2 * self.LINE
        self.canv.drawString(0, y, "提交日期：")
        x0 = self.canv.stringWidth("提交日期：", FONT_BODY, SAMPLE["body_pt"]) + 2 * L.MM
        if c.date:
            self.canv.drawString(x0, y, c.date)
            return
        for i, ch in enumerate("年月日"):
            self.canv.drawString(x0 + i * self.GAP, y, ch)


def merge(paths: list[Path], out: Path) -> None:
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter()
    for p in paths:
        for pg in PdfReader(str(p)).pages:
            w.add_page(pg)
    with open(out, "wb") as fh:
        w.write(fh)
