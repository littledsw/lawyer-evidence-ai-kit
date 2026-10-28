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
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.flowables import Flowable

from . import layout as L
from .model import Catalog, Group

_SONGTI = "/System/Library/Fonts/Supplemental/Songti.ttc"
_STHEITI = "/System/Library/Fonts/STHeiti Light.ttc"
_STHEITI_M = "/System/Library/Fonts/STHeiti Medium.ttc"

# 关键：不让 reportlab 用 ASCII85 包一层（那会让 JPEG 流在文件里变成文本，体积 +25%）
rl_config.useA85 = 0

FONT_BODY = "EK-Body"
FONT_BOLD = "EK-Bold"
_fonts_ready = False


def ensure_fonts() -> None:
    global _fonts_ready
    if _fonts_ready:
        return
    ok = {FONT_BODY: False, FONT_BOLD: False}
    if Path(_SONGTI).exists():
        pdfmetrics.registerFont(TTFont(FONT_BOLD, _SONGTI, subfontIndex=1))   # Songti SC Bold
        pdfmetrics.registerFont(TTFont(FONT_BODY, _SONGTI, subfontIndex=4))   # STSong Regular
        ok = {FONT_BODY: True, FONT_BOLD: True}
    elif Path(_STHEITI).exists():
        pdfmetrics.registerFont(TTFont(FONT_BODY, _STHEITI, subfontIndex=0))
        pdfmetrics.registerFont(TTFont(FONT_BOLD, _STHEITI_M, subfontIndex=0))
        ok = {FONT_BODY: True, FONT_BOLD: True}
    if not all(ok.values()):
        raise RuntimeError("找不到可用的中文字体（Songti.ttc / STHeiti）——请用 --font 指定")
    _fonts_ready = True


# ---------------------------------------------------------------- 目录页
def _para(text: str, font: str, size: float, align=None, leading=None) -> Paragraph:
    st = ParagraphStyle("x", fontName=font, fontSize=size,
                        leading=leading or size * 1.55, alignment=align or 0)
    return Paragraph(text, st)


def _txt(s: str) -> str:
    return escape(str(s)).replace("\n", "<br/>")


_NUM_ITEM = __import__("re").compile(r"(?:^|；)\s*\d+\s*[:：]")


def _proves_html(text: str) -> str:
    """「1:…；2:…」这种编号式证明目的按条断行；普通行文交给 Paragraph 自动折行。"""
    t = _txt(text)
    if _NUM_ITEM.search(text):
        t = t.replace("；", "；<br/>")
    return re.sub(r"\s*<br/>\s*", "<br/>", t)      # 换行与 <br/> 叠在一起会多出空行


def catalog_pdf(path: Path, catalog: Catalog) -> int:
    """目录页：标题 + 四列表格（序号/名称/证明目的/页数）+ 落款。返回页数。"""
    ensure_fonts()
    doc = SimpleDocTemplate(str(path), pagesize=L.A4,
                            leftMargin=15 * L.MM, rightMargin=15 * L.MM,
                            topMargin=15 * L.MM, bottomMargin=15 * L.MM,
                            title=catalog.case.title, author=catalog.case.agent)
    story = [
        _para(_txt(catalog.case.title), FONT_BOLD, 22, TA_CENTER, leading=30 * L.MM / 2.4),
        Spacer(1, 5 * L.MM),
    ]

    head = [_para("序号", FONT_BOLD, 12.5, TA_CENTER), _para("证据材料名称", FONT_BOLD, 12.5, TA_CENTER),
            _para("证明目的", FONT_BOLD, 12.5, TA_CENTER), _para("页数", FONT_BOLD, 12.5, TA_CENTER)]
    data = [head]
    spans = []
    for g in catalog.groups:
        start_row = len(data)
        proves = g.proves if g.proves_source == "user" and g.proves else (g.proves or "（证明目的待补）")
        for i, r in enumerate(g.rows):
            data.append([
                _para(str(r.no), FONT_BODY, 11.5, TA_CENTER),
                _para(_txt(r.name), FONT_BODY, 11.5),
                _para(_proves_html(proves) if i == 0 else "", FONT_BODY, 11),
                _para(g.pages_label if i == 0 else "", FONT_BODY, 11.5, TA_CENTER),
            ])
        if len(g.rows) > 1:
            end_row = len(data) - 1
            spans.append(("SPAN", (2, start_row), (2, end_row)))
            spans.append(("SPAN", (3, start_row), (3, end_row)))

    widths = [22 * L.MM, 46 * L.MM, 90 * L.MM, 22 * L.MM]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.7, (0, 0, 0)),
        ("BOX", (0, 0), (-1, -1), 1.4, (0, 0, 0)),
        ("LINEBELOW", (0, 0), (-1, 0), 1.4, (0, 0, 0)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        *spans,
    ]))
    story.append(t)
    story.append(Spacer(1, 10 * L.MM))

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
        self.canv.setFont(FONT_BODY, 12.5)
        self.canv.setFillGray(0)
        top = self.height - 4 * L.MM
        self.canv.drawString(0, top, f"提交人：{c.submitter}")
        self.canv.drawString(0, top - self.LINE, f"代理人：{c.agent}")
        y = top - 2 * self.LINE
        self.canv.drawString(0, y, "提交日期：")
        x0 = self.canv.stringWidth("提交日期：", FONT_BODY, 12.5) + 2 * L.MM
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
