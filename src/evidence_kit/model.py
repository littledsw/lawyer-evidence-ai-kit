"""数据模型：证据目录 → 证据组 → 排版单元 → 分页计划。

三层刻意分开，因为三层的输入来源不同：
- `Catalog`  来自用户给的目录（截图 / docx / xlsx / yaml），是**实体判断**，不经机器改写；
- `Unit`     来自原始材料文件（图片一页 = 1 个单元，PDF 一页 = 1 个单元，长截图切片 = N 个单元）；
- `PagePlan` 来自排版计算，**页数只能从这里出**，不允许外部传入或估算。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Unit:
    """一个可排版单元 = 原始材料里的一页（或长截图的其中一段）。"""

    path: Path
    kind: str = "image"          # image | pdf
    page: int = 0                # PDF 页序号（0 基）；图片恒 0
    width_px: float = 0          # 图片像素宽；PDF 单元为 mediabox 宽（pt）
    height_px: float = 0
    slice_index: int = 0         # 长截图切片序号（0 基；非切片恒 0）
    slice_count: int = 1
    slice_y0_px: int = 0         # 切片在源图中的起始行（0 基，从上往下）
    slice_y1_px: int = 0         # 切片结束行（不含）
    full_height_px: float = 0    # 源图总高（切片定位用）
    origin_name: str = ""        # 原始文件名（HEIC 转码后仍显示原名）

    @property
    def label(self) -> str:
        base = self.origin_name or self.path.name
        parts = []
        if self.kind == "pdf":
            parts.append(f"p{self.page + 1}")
        if self.slice_count > 1:
            parts.append(f"片{self.slice_index + 1}/{self.slice_count}")
        return base + ("（" + " ".join(parts) + "）" if parts else "")


@dataclass
class Row:
    """目录里的一行（一个序号）。"""

    no: int
    name: str
    files: list[Path] = field(default_factory=list)


@dataclass
class Group:
    """一个证据组 = 目录里共享「证明目的 / 页数」的一个或多个序号行。

    截图里序号 4、5 共用一格证明目的、共用一格页数 —— 那就是一个组两个行。
    组是排版与编号的原子：页数按组分段连排，组与组之间不共享页面。
    """

    rows: list[Row]
    proves: str = ""
    proves_source: str = "user"        # user | auto（机器补写，需复核）
    units: list[Unit] = field(default_factory=list)
    page_start: int = 0                # 1 基，含
    page_end: int = 0                  # 1 基，含

    @property
    def no_label(self) -> str:
        return "、".join(str(r.no) for r in self.rows)

    @property
    def name(self) -> str:
        return "\n".join(r.name for r in self.rows)

    @property
    def pages_label(self) -> str:
        if not self.page_start:
            return "待排"
        if self.page_start == self.page_end:
            return str(self.page_start)
        return f"{self.page_start}-{self.page_end}"


@dataclass
class CaseInfo:
    submitter: str = ""
    agent: str = ""
    date: str = ""                     # 留空 → 打印成「  年  月  日」
    title: str = "证据目录"


@dataclass
class Catalog:
    case: CaseInfo
    groups: list[Group]
    source: str = ""                   # 目录来源（文件路径或 auto）

    @property
    def total_units(self) -> int:
        return sum(len(g.units) for g in self.groups)


_NAT = re.compile(r"(\d+)")


def natural_key(p: Path | str) -> tuple:
    """文件/目录名自然序：4.png < 10.png，01_流水 < 02_社保。"""
    s = Path(p).name if isinstance(p, str) else p.name
    return tuple(int(t) if t.isdigit() else t for t in _NAT.split(s))
