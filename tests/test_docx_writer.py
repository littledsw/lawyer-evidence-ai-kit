"""导出的 Word 必须能被自己的解析器原样读回（防止合并单元格/字体处理把结构写坏）。"""
from pathlib import Path

from evidence_kit.catalog import parse_dict
from evidence_kit.docx_catalog import read_catalog_docx
from evidence_kit.docx_writer import write_catalog_docx


def _cat():
    return parse_dict({
        "case": {"submitter": "崔某某", "agent": "王某某"},
        "groups": [
            {"proves": "1:证明工资标准；2:证明由公司发放；",
             "rows": [{"no": 1, "name": "银行工资流水", "files": []}]},
            {"proves": "证明其接受工作管理。",
             "rows": [{"no": 4, "name": "加油台账", "files": []},
                      {"no": 5, "name": "停车缴费截图", "files": []}]},
        ]}, source="t")


def test_roundtrip_keeps_structure(tmp_path):
    cat = _cat()
    for g in cat.groups:                     # 给页数一个具体值，验证 Word 里也写上
        g.page_start, g.page_end = (1, 5) if g.rows[0].no == 1 else (6, 9)
    out = tmp_path / "证据目录.docx"
    write_catalog_docx(cat, out)
    assert out.exists() and out.stat().st_size > 0

    back = read_catalog_docx(out)
    assert [r.name for g in back.groups for r in g.rows] == \
           [r.name for g in cat.groups for r in g.rows]
    assert [g.proves for g in back.groups] == [g.proves for g in cat.groups]
    assert [len(g.rows) for g in back.groups] == [1, 2]      # 合并单元格没丢
    assert back.case.submitter == "崔某某" and back.case.agent == "王某某"
