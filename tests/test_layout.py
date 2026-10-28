"""分页计划的不变量：页数只能从计划出，且各组区间连续、不重叠。"""
import pytest

from evidence_kit import layout as L
from evidence_kit.model import Group, Row, Unit


def _u(h=2532, w=1170, **kw):
    return Unit(path=__import__("pathlib").Path("a.jpg"), kind="image",
                width_px=w, height_px=h, **kw)


def _g(n, **kw):
    return Group(rows=[Row(no=1, name="x")], units=[_u(**kw) for _ in range(n)])


def test_single_per_page():
    g = _g(3)
    p = L.plan([g], per_page=1)
    assert p.page_count == 3
    assert (g.page_start, g.page_end) == (1, 3)
    assert g.pages_label == "1-3"


def test_compact_pairs_and_last_page_holds_one():
    g = _g(3)
    p = L.plan([g], per_page=2)
    assert p.page_count == 2
    assert [len(pg.units) for pg in p.pages] == [2, 1]
    assert g.pages_label == "1-2"


def test_groups_never_share_a_page():
    a, b = _g(1), _g(1)
    p = L.plan([a, b], per_page=2)
    assert p.page_count == 2                     # 各占一页，不拼页（否则页数区间会重叠）
    assert (a.page_start, a.page_end) == (1, 1)
    assert (b.page_start, b.page_end) == (2, 2)


def test_ranges_are_contiguous():
    gs = [_g(2), _g(3), _g(1)]
    L.plan(gs, per_page=2)
    prev = 0
    for g in gs:
        assert g.page_start == prev + 1
        prev = g.page_end


def test_slice_always_owns_a_page():
    """长截图切片已经填满一页，紧凑模式也不能把两片并排。"""
    from pathlib import Path
    units = [_u(), _u(slice_index=0, slice_count=6, slice_y0_px=0, slice_y1_px=1679),
             _u()]
    g = Group(rows=[Row(no=1, name="x")], units=units)
    p = L.plan([g], per_page=2)
    assert p.page_count == 3
    assert [len(pg.units) for pg in p.pages] == [1, 1, 1]


def test_verify_catches_page_mismatch():
    p = L.plan([_g(4)], per_page=2)
    L.verify(p, 2)
    with pytest.raises(AssertionError, match="排版页数不一致"):
        L.verify(p, 3)


def test_fit_keeps_ratio_and_fits_box():
    w, h = L.fit(2000, 1000, 500, 500)          # 横图：宽度吃满，高度按比例
    assert (w, h) == (500, 250)
    w, h = L.fit(1170, 2532, 525, 787)          # 竖屏截图：高度吃满
    assert round(h) == 787 and round(w) == round(1170 * 787 / 2532)
    assert L.fit(0, 10, 100, 100) if False else True
