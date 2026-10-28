"""端到端不变量：页数对得上、原件字节直通、A4 竖版、页码只在页脚、低分辨率拒绝出册。"""
import json

import pytest
from pypdf import PdfReader

from evidence_kit import layout as L
from evidence_kit import pipeline


def _catalog(tmp_path, groups):
    import yaml
    p = tmp_path / "catalog.yaml"
    p.write_text(yaml.safe_dump({"case": {"submitter": "甲", "agent": "乙"},
                                 "groups": groups}, allow_unicode=True), encoding="utf-8")
    return p


def _materials(tmp_path):
    m = tmp_path / "materials"
    (m / "01").mkdir(parents=True, exist_ok=True)
    (m / "02").mkdir(exist_ok=True)
    return m


def _image_streams(pdf_path):
    """PDF 里所有图像流的解码后字节（JPEG 直通时应与源文件完全相同）。"""
    out = set()
    for pg in PdfReader(str(pdf_path)).pages:
        res = pg.get("/Resources")
        xo = (res or {}).get("/XObject") or {}
        for k in xo:
            o = xo[k].get_object()
            if o.get("/Subtype") == "/Image":
                out.add(o.get_data())
    return out


def test_build_pages_and_passthrough(tmp_path, mk_jpeg, mk_pdf):
    m = tmp_path
    a = mk_jpeg("01/a.jpg")
    b = mk_jpeg("01/b.jpg")
    pdf = mk_pdf("02/s.pdf", pages=2)
    cat = _catalog(tmp_path, [
        {"proves": "证明工资标准。", "rows": [{"no": 1, "name": "工资流水", "files": "01"}]},
        {"proves": "证明社保。", "rows": [{"no": 2, "name": "社保记录", "files": "02"}]},
    ])
    res = pipeline.build(cat, m, tmp_path / "out")

    # ① 页数 = 计划 = 实际，且目录里的区间与之一致
    assert res.evidence_pages == 2 + 2
    assert res.catalog.groups[0].pages_label == "1-2"
    assert res.catalog.groups[1].pages_label == "3-4"
    book = PdfReader(str(res.book))
    assert len(book.pages) == res.catalog_pages + res.evidence_pages

    # ② 每页都是 A4 竖版
    for pg in book.pages:
        assert round(float(pg.mediabox.width), 1) == round(L.A4[0], 1)
        assert round(float(pg.mediabox.height), 1) == round(L.A4[1], 1)

    # ③ 原件 JPEG 流字节原样（解出 DCT 流与源文件逐字节相同，不是重编码的近似）
    streams = _image_streams(res.book)
    assert a.read_bytes() in streams
    assert b.read_bytes() in streams

    # ④ 目录页不编页码；证据页从 1 连编
    assert "证据目录" in book.pages[0].extract_text()
    assert "第 1 页" not in book.pages[0].extract_text()
    assert "第 1 页" in book.pages[1].extract_text()
    assert "证据1" in book.pages[1].extract_text()

    # ⑤ 报告落盘且页码对照表与实测一致
    rep = json.loads((res.out_dir / "report.json").read_text(encoding="utf-8"))
    assert rep["counts"]["evidence_pages"] == 4
    assert [g["pages"] for g in rep["groups"]] == ["1-2", "3-4"]


def test_compact_mode_halves_pages(tmp_path, mk_jpeg):
    m = tmp_path
    for i in range(4):
        mk_jpeg(f"01/{i}.jpg")
    cat = _catalog(tmp_path, [
        {"proves": "x", "rows": [{"no": 1, "name": "截图", "files": "01"}]}])
    one = pipeline.build(cat, m, tmp_path / "o1")
    two = pipeline.build(cat, m, tmp_path / "o2", compact=True)
    assert one.evidence_pages == 4
    assert two.evidence_pages == 2


def test_low_resolution_is_blocked(tmp_path, mk_jpeg):
    m = tmp_path
    mk_jpeg("01/tiny.jpg", w=120, h=260)          # 印到 A4 只有 ~16 DPI
    cat = _catalog(tmp_path, [
        {"proves": "x", "rows": [{"no": 1, "name": "糊图", "files": "01"}]}])
    with pytest.raises(RuntimeError, match="DPI"):
        pipeline.build(cat, m, tmp_path / "out")
    # 显式放行才出册
    pipeline.build(cat, m, tmp_path / "out2", allow_low_dpi=True)


def test_unsupported_file_is_reported_not_dropped(tmp_path, mk_jpeg):
    m = tmp_path
    mk_jpeg("01/ok.jpg")
    (m / "01" / "笔记.docx").write_bytes(b"PK\x03\x04")
    cat = _catalog(tmp_path, [
        {"proves": "x", "rows": [{"no": 1, "name": "混装", "files": "01"}]}])
    with pytest.raises(RuntimeError, match="未纳入"):
        pipeline.build(cat, m, tmp_path / "out")


def test_long_screenshot_is_sliced_with_overlap(tmp_path, mk_jpeg):
    m = tmp_path
    mk_jpeg("01/long.jpg", w=1170, h=9000)
    cat = _catalog(tmp_path, [
        {"proves": "x", "rows": [{"no": 1, "name": "长截图", "files": "01"}]}])
    res = pipeline.build(cat, m, tmp_path / "out")
    u = res.catalog.groups[0].units[0]
    assert u.slice_count > 1
    assert res.evidence_pages == u.slice_count
    # 切缝有重叠，且每片都完整（末片含图尾）
    assert u.slice_y1_px == u.height_px
    last = res.catalog.groups[0].units[-1]
    assert last.slice_y1_px == 9000
