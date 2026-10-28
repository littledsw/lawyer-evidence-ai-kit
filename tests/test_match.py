"""平铺材料归位：文件名优先、OCR 只辅助、歧义绝不猜。"""
from pathlib import Path

from evidence_kit import match
from evidence_kit.catalog import parse_dict


def _cat(*names, proves="x"):
    return parse_dict({"case": {}, "groups": [
        {"proves": proves, "rows": [{"no": i + 1, "name": n, "files": []}]}
        for i, n in enumerate(names)]}, source="t")


def _p(name: str) -> Path:
    return Path("/m") / name


def _one(cat, fname, text_index=None):
    d = match.match(cat, [_p(fname)], text_index)[0]
    return d.status, (d.best.row.no if d.best else None)


def test_leading_number_wins():
    cat = _cat("中国石化加油IC卡台账对账单", "停车缴费截图")
    assert _one(cat, "4_停车缴费.jpg") == ("matched", 2)
    assert _one(cat, "02 社保缴费.pdf") == ("matched", 2)


def test_unique_word_matches_without_number():
    cat = _cat("银行工资流水", "社保缴费记录")
    assert _one(cat, "流水_扫描件.jpg") == ("matched", 1)


def test_two_unique_words_from_different_rows_is_ambiguous():
    """文件名同时沾两个证据的专属词 → 不猜，交人工。"""
    cat = _cat("加油台账", "停车缴费截图")
    assert _one(cat, "台账截图.jpg") == ("ambiguous", 1)


def test_unknown_file_is_not_forced():
    cat = _cat("银行工资流水", "社保缴费记录")
    assert _one(cat, "微信_1.jpg") == ("unmatched", None)


def test_ocr_text_only_assists():
    """文件名毫无信息时，正文识别结果可以把它救回来。"""
    cat = _cat("银行工资流水", "社保缴费记录")
    d = match.match(cat, [_p("IMG_0001.jpg")],
                    {"IMG_0001.jpg": "社会保险 缴费记录 2025-01"})[0]
    assert d.status == "matched" and d.best.row.no == 2
    assert any("正文命中" in r for r in d.best.reasons)


def test_filename_beats_ocr():
    """文件名说 A、正文里提了 B：文件名优先，且不会把文件归到 B。"""
    cat = _cat("银行工资流水", "社保缴费记录")
    d = match.match(cat, [_p("1_工资流水.jpg")],
                    {"1_工资流水.jpg": "社会保险 缴费记录"})[0]
    assert d.best.row.no == 1


def test_eligible_rows_are_not_overwritten():
    cat = _cat("银行工资流水", "社保缴费记录")
    cat.groups[0].rows[0].files = [Path("已指定.jpg")]
    decisions = match.match(cat, [_p("流水_1.jpg")])
    eligible = {id(r) for g in cat.groups for r in g.rows if not r.files}
    match.apply(cat, decisions, eligible=eligible)
    assert cat.groups[0].rows[0].files == [Path("已指定.jpg")]


def test_missing_row_is_reported():
    cat = _cat("银行工资流水", "社保缴费记录")
    decisions = match.match(cat, [_p("流水_1.jpg")])
    _, notes = match.apply(cat, decisions)
    assert any("没有任何文件匹配上" in n for n in notes)
