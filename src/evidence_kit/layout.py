"""版面常量与分页计划。

页数**只从分页计划出**：目录里那一格的 `1-5`/`6-9` 必须等于渲染后该组实际占用的页区间。
`evidence.pdf` 的实际页数与计划不符即视为排版错误（`verify()` 会抛）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .model import Group, Unit

MM = 72.0 / 25.4          # 1mm = 2.8346 pt

A4 = (210 * MM, 297 * MM)          # 竖版，永不横向
MARGIN_L = 12 * MM
MARGIN_R = 12 * MM
MARGIN_T = 12 * MM
MARGIN_B = 18 * MM                 # 页脚留白：页码绝不压内容
CONTENT_W = A4[0] - MARGIN_L - MARGIN_R
CONTENT_H = A4[1] - MARGIN_T - MARGIN_B

GAP = 5 * MM                       # 一页多图时图与图的间距
SLICE_OVERLAP = 8 * MM             # 长截图切片重叠，避免切掉一行字
SLICE_ASPECT = 2.6                 # 高/宽 超过此值才切片（手机整屏截图约 2.16，不切）


@dataclass
class PagePlan:
    units: list[Unit]
    group_index: int
    cell_w: float = CONTENT_W
    cell_h: float = CONTENT_H

    @property
    def is_single(self) -> bool:
        return len(self.units) == 1


@dataclass
class Plan:
    pages: list[PagePlan] = field(default_factory=list)
    per_page: int = 1

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def page_no_of(self, unit: Unit) -> int:
        for i, p in enumerate(self.pages, start=1):
            if unit in p.units:
                return i
        raise KeyError(unit.label)


def plan(groups: Iterable[Group], per_page: int = 1) -> Plan:
    """按组切页：组是页面的最小归属单位，一组不跨页混排（否则页数区间会重叠）。

    例外：长截图切片**永远独占一页**——切片本身已经把一页填满了，
    再两图并排只会把字缩到一半，得不偿失。
    """
    if per_page < 1:
        raise ValueError("per_page 必须 >= 1")
    p = Plan(per_page=per_page)
    for gi, g in enumerate(groups):
        units = list(g.units)
        if not units:
            g.page_start = g.page_end = 0
            continue
        g.page_start = p.page_count + 1
        cell_w = (CONTENT_W - GAP * (per_page - 1)) / per_page if per_page > 1 else CONTENT_W
        i = 0
        while i < len(units):
            if units[i].slice_count > 1:
                p.pages.append(PagePlan(units=[units[i]], group_index=gi,
                                        cell_w=CONTENT_W, cell_h=CONTENT_H))
                i += 1
                continue
            chunk: list[Unit] = []
            while i < len(units) and len(chunk) < per_page and units[i].slice_count == 1:
                chunk.append(units[i])
                i += 1
            p.pages.append(PagePlan(units=chunk, group_index=gi, cell_w=cell_w, cell_h=CONTENT_H))
        g.page_end = p.page_count
    return p


def cell_origin(cell_index: int, per_page: int) -> tuple[float, float]:
    """第 cell_index 个单元格（左起）的左下角坐标。"""
    if per_page == 1:
        return MARGIN_L, MARGIN_B
    w = (CONTENT_W - GAP * (per_page - 1)) / per_page
    x = MARGIN_L + cell_index * (w + GAP)
    return x, MARGIN_B


def fit(w_px: float, h_px: float, box_w: float, box_h: float) -> tuple[float, float]:
    """等比缩放到框内（只缩不放），返回显示宽高（pt）。"""
    if w_px <= 0 or h_px <= 0:
        raise ValueError("宽高必须为正")
    s = min(box_w / w_px, box_h / h_px)
    return w_px * s, h_px * s


def verify(plan_obj: Plan, rendered_pages: int) -> None:
    """页数自证：目录里的区间必须等于实际渲染页数，否则整册作废。"""
    if rendered_pages != plan_obj.page_count:
        raise AssertionError(
            f"排版页数不一致：计划 {plan_obj.page_count} 页，实际渲染 {rendered_pages} 页"
        )
