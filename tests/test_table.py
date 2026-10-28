"""目录表格重建（纯几何，不依赖 Vision）：用合成文字行覆盖真实踩过的坑。"""
import pytest

from evidence_kit.ocr import OcrLine
from evidence_kit.table import catalog_from_image, reconstruct, to_catalog

CW = 20.0          # 字宽
LH = 30.0          # 行高
COL = {"no": 100.0, "name": 200.0, "proves": 400.0, "pages": 700.0}


def line(text: str, x: float, y: float, with_chars: bool = True) -> OcrLine:
    chars = [(ch, x + i * CW, y, CW, LH * 0.7) for i, ch in enumerate(text)] if with_chars else None
    return OcrLine(text=text, confidence=0.99, x=x, y=y, w=len(text) * CW, h=LH * 0.7, chars=chars)


def header(y: float = 100.0):
    return [line("序号", COL["no"], y), line("证据材料名称", COL["name"], y),
            line("证明目的", COL["proves"], y), line("页数", COL["pages"], y)]


def merged_line(part1: str, x1: float, part2: str, x2: float, y: float) -> OcrLine:
    """一条横跨两格的 OCR 行：名称在左、证明目的在右，中间那条缝没有任何字符。"""
    chars = [(ch, x1 + i * CW, y, CW, LH * 0.7) for i, ch in enumerate(part1)]
    chars += [(" ", x1 + len(part1) * CW, y, CW, LH * 0.7)]
    chars += [(ch, x2 + i * CW, y, CW, LH * 0.7) for i, ch in enumerate(part2)]
    return OcrLine(text=part1 + " " + part2, confidence=0.99, x=x1, y=y,
                   w=x2 + len(part2) * CW - x1, h=LH * 0.7, chars=chars)


def test_split_by_bounds_cuts_merged_line():
    """OCR 把相邻两格读成一行时，按列边界切开（用字符级坐标）。"""
    from evidence_kit.table import _split_by_bounds

    m = merged_line("加油台账", 200, "作管理和安排。", 400, 210)
    parts = _split_by_bounds(m, [300.0])
    assert [p.text for p in parts] == ["加油台账", "作管理和安排。"]
    assert parts[1].x >= 400 - 1


def test_join_keeps_wrap_but_breaks_at_items():
    from evidence_kit.table import _join

    lines = [line("证明申请人月工资标准为", 400, 200), line("4500元；", 400, 240),
             line("2：证明工资由公司发放", 400, 280)]
    got = _join(lines)
    assert "标准为4500元；" in got          # 折行接回，不插换行
    assert "\n2：证明工资由公司发放" in got  # 编号另起一行


def test_merge_anchors_prefers_digit():
    from evidence_kit.table import _merge_anchors

    digits = [line("4", 100, 200)]
    names = [line("加油台账", 200, 202)]
    merged = _merge_anchors(digits, names, LH)
    assert len(merged) == 1 and merged[0].text == "4"


def test_name_anchor_recovers_missing_number():
    """OCR 漏掉某个序号（实测缩到 60% 时「1」消失）→ 名称列补一个锚点。"""
    from evidence_kit.table import _column_bounds, _name_anchors

    body = [line("银行流水", 200, 200), line("证明申请人月工资标准为4500元", 400, 200),
            line("2", 100, 300), line("社保记录", 200, 300),
            line("证明双方存在劳动关系及缴费情况", 400, 300)]
    bounds = _column_bounds(body)
    col_of = lambda l: ["no", "name", "proves"][
        sum(1 for b in bounds if l.cx > b)]
    anchors = _name_anchors(body, col_of, LH)
    assert len(anchors) == 2                              # 两行都有名称锚点


def test_to_catalog_merges_empty_proves_into_group():
    from evidence_kit.table import OcrRow, to_catalog

    cat = to_catalog([OcrRow(4, "加油台账", "证明接受管理。", [1.0]),
                      OcrRow(5, "停车缴费", "", [1.0])], {}, "t")
    assert len(cat.groups) == 1
    assert [r.no for r in cat.groups[0].rows] == [4, 5]
    assert cat.groups[0].pages_label == "待排"


def test_not_a_catalog_raises():
    lines = [line("随便一段话", 100, 100), line("没有任何表头", 100, 200)]
    with pytest.raises(ValueError, match="表头"):
        reconstruct(lines)
