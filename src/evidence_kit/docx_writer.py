"""把目录导出成**可编辑的 Word**（仲裁委常要求改字、补章、加列）。

为什么要它：PDF 是提交件，但律师改字、法院/仲裁委要求填表时得给 Word。
版式与 PDF 页同源（`render.SAMPLE`：A4、左右 31.75mm、仿宋 22pt 标题 / 12pt 正文、
四列 711:2063:4765:983 twips），保证两份长得一样。

**自验证**：写完用本仓库的 `read_catalog_docx` 读回来，结构与文字必须一致
（`tests/test_docx_writer.py`），防止「导出的 Word 打不开 / 合并单元格丢了」。
"""
from __future__ import annotations

from pathlib import Path

from .model import Catalog

FONT_NAME = "仿宋"


def _twips_to_emu(tw: int):
    from docx.shared import Emu
    return Emu(int(tw * 635))     # 1 twip = 635 EMU


def _set_font(run, size_pt: float, bold: bool = False) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt

    run.font.name = FONT_NAME
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attr), FONT_NAME)


def _set_borders(table) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "8")               # 0.5pt 左右，与 PDF 版一致
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "000000")
        borders.append(el)
    tbl_pr.append(borders)


def _set_table_width(table, pct: int) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tbl_pr = table._tbl.tblPr
    for old in tbl_pr.findall(qn("w:tblW")):
        tbl_pr.remove(old)
    w = OxmlElement("w:tblW")
    w.set(qn("w:w"), str(pct * 50))            # 5000 = 100%
    w.set(qn("w:type"), "pct")
    tbl_pr.append(w)


def write_catalog_docx(catalog: Catalog, path: Path, sample: dict | None = None) -> Path:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Mm

    from .render import SAMPLE

    fmt = sample or SAMPLE
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.left_margin = sec.right_margin = Mm(fmt["margin_lr_mm"])
    sec.top_margin = sec.bottom_margin = Mm(fmt["margin_tb_mm"])

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_font(title.add_run(catalog.case.title), fmt["title_pt"])
    doc.add_paragraph()

    cols = ("序号", "证据材料名称", "证明目的", "页数")
    total = sum(fmt["col_twips"])
    content_tw = int((210 - 2 * fmt["margin_lr_mm"]) / 25.4 * 1440)
    widths = [_twips_to_emu(int(content_tw * tw / total)) for tw in fmt["col_twips"]]

    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    _set_borders(table)                       # 不能只靠 TableGrid 样式：QuickLook/预览里常丢边框
    _set_table_width(table, 100)
    for i, name in enumerate(cols):
        table.columns[i].width = widths[i]    # 列宽必须写到 tblGrid，否则各列会等宽
        cell = table.rows[0].cells[i]
        cell.width = widths[i]
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_font(p.add_run(name), fmt["body_pt"])

    for g in catalog.groups:
        start = len(table.rows)
        for i, r in enumerate(g.rows):
            row = table.add_row()
            for j in range(4):
                row.cells[j].width = widths[j]
            cells = row.cells
            p0 = cells[0].paragraphs[0]
            p0.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _set_font(p0.add_run(str(r.no)), fmt["body_pt"])
            p1 = cells[1].paragraphs[0]
            p1.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _set_font(p1.add_run(r.name), fmt["body_pt"])
            p2 = cells[2].paragraphs[0]
            _set_font(p2.add_run((g.proves or "（证明目的待补）") if i == 0 else ""),
                      fmt["body_pt"])
            p3 = cells[3].paragraphs[0]
            p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
            _set_font(p3.add_run(g.pages_label if i == 0 else ""), fmt["body_pt"])
        if len(g.rows) > 1:
            table.cell(start, 2).merge(table.cell(start + len(g.rows) - 1, 2))
            table.cell(start, 3).merge(table.cell(start + len(g.rows) - 1, 3))

    doc.add_paragraph()
    c = catalog.case
    for text in (f"提交人：{c.submitter}", f"代理人：{c.agent}",
                 f"提交日期：{c.date or '    年    月    日'}"):
        p = doc.add_paragraph()
        _set_font(p.add_run(text), fmt["body_pt"])

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return Path(path)
