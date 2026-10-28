"""一条命令：目录 + 材料 → 证据册 PDF（目录页 + 连编页码的证据页）+ 核对报告。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import layout as L
from . import match, render, sources
from .catalog import load_catalog
from .model import Catalog, natural_key

DPI_WARN = 150.0
DPI_FLOOR = 100.0


@dataclass
class BuildResult:
    out_dir: Path
    book: Path
    catalog_pdf: Path
    evidence_pdf: Path
    report_json: Path
    report_md: Path
    catalog: Catalog
    plan: L.Plan
    catalog_pages: int = 0
    evidence_pages: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)
    match_notes: list[str] = field(default_factory=list)


def _flat_match(catalog: Catalog, materials: Path, res: "BuildResult") -> list[str]:
    """平铺目录里的散文件 → 归入空着的序号行。歧义与零分进待确认清单，不猜。"""
    if not materials.is_dir():
        return []
    loose = sorted((f for f in materials.iterdir() if f.is_file()), key=natural_key)
    if not loose:
        return []
    eligible = {id(r) for g in catalog.groups for r in g.rows if not r.files}
    decisions = match.match(catalog, loose)
    n, notes = match.apply(catalog, decisions, eligible=eligible)
    res.warnings.append(f"平铺材料：{len(loose)} 个文件，自动归位 {n} 个")
    return notes


def build(catalog_path: Path | None, materials: Path, out_dir: Path,
          compact: bool = False, per_page: int | None = None,
          auto: bool = False, allow_low_dpi: bool = False,
          flat_match: bool = True) -> BuildResult:
    materials = Path(materials)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    catalog = load_catalog(catalog_path, materials, auto=auto)
    n_per = per_page or (2 if compact else 1)
    res = BuildResult(out_dir=out_dir, book=out_dir / "证据册.pdf",
                      catalog_pdf=out_dir / "证据目录.pdf",
                      evidence_pdf=out_dir / "证据页.pdf",
                      report_json=out_dir / "report.json",
                      report_md=out_dir / "README-核对报告.md",
                      catalog=catalog, plan=L.Plan(per_page=n_per))

    # ⓪ 平铺材料 → 证据组（文件名优先，OCR 只辅助；歧义交人工，不猜）
    if flat_match:
        res.match_notes = _flat_match(catalog, materials, res)

    # ① 材料 → 单元
    for g in catalog.groups:
        for row in g.rows:
            files = sources.discover(row.files, materials)
            if not files:
                res.errors.append(f"证据{g.no_label}「{row.name}」：没有匹配到任何文件")
                continue
            pr = sources.probe(files)
            g.units.extend(pr.units)
            res.warnings.extend(pr.warnings)
            for p, why in pr.skipped:
                res.errors.append(f"证据{g.no_label} {p.name}：{why}（未纳入，不会静默丢弃）")

    # ② 分页 —— 目录里的页数只能从这里出
    res.plan = L.plan(catalog.groups, per_page=n_per)
    for g in catalog.groups:
        if g.units and g.page_start == 0:
            res.errors.append(f"证据{g.no_label}：有材料但没排进页面")

    # ③ 分辨率硬检查（会不会糊）
    for g in catalog.groups:
        for u in g.units:
            if u.kind != "image":
                continue
            idx = 0
            dpi = sources.effective_dpi(u, _cell_w(n_per), L.CONTENT_H)
            if dpi < DPI_FLOOR and not allow_low_dpi:
                res.errors.append(
                    f"证据{g.no_label} {u.label}：印到纸上仅 {dpi:.0f} DPI（<{DPI_FLOOR:.0f}），"
                    f"会糊，已拒绝出册；如确需出册加 --allow-low-dpi")
            elif dpi < DPI_WARN:
                res.warnings.append(
                    f"证据{g.no_label} {u.label}：印到纸上 {dpi:.0f} DPI（<{DPI_WARN:.0f}），可读但偏糊")

    # ④ 待复核清单：平铺匹配里没定案的文件
    res.review.extend(res.match_notes)

    # ⑤ 待复核清单：机器补的证明目的
    for g in catalog.groups:
        if g.proves_source != "user" or not g.proves.strip():
            res.review.append(
                f"证据{g.no_label}「{'、'.join(r.name for r in g.rows)}」：证明目的为机器留空/补写，"
                f"需人工确认（排版所用材料：{', '.join(r.name for r in g.rows)}）")

    if res.errors:
        _write_report(res)
        raise RuntimeError("出册前检查未通过：\n- " + "\n- ".join(res.errors))

    # ⑥ 渲染
    res.catalog_pages = render.catalog_pdf(res.catalog_pdf, catalog)
    render.evidence_pdf(res.evidence_pdf, res.plan, catalog)
    from pypdf import PdfReader
    res.evidence_pages = len(PdfReader(str(res.evidence_pdf)).pages)
    L.verify(res.plan, res.evidence_pages)          # 页面数与计划不符 → 直接抛
    render.merge([res.catalog_pdf, res.evidence_pdf], res.book)
    total = len(PdfReader(str(res.book)).pages)
    if total != res.catalog_pages + res.evidence_pages:
        raise AssertionError(f"合并后页数不符：{total} != {res.catalog_pages}+{res.evidence_pages}")
    _write_report(res)
    return res


def _cell_w(per_page: int) -> float:
    if per_page <= 1:
        return L.CONTENT_W
    return (L.CONTENT_W - L.GAP * (per_page - 1)) / per_page


def _write_report(res: BuildResult) -> None:
    cat = res.catalog
    pm = []
    for g in cat.groups:
        pm.append({
            "no": g.no_label,
            "name": " / ".join(r.name for r in g.rows),
            "proves_source": g.proves_source,
            "pages": g.pages_label,
            "files": [_unit_info(u) for u in g.units],
        })
    data = {
        "catalog_source": cat.source,
        "layout": {"per_page": res.plan.per_page, "page_size": "A4 竖版",
                   "content_mm": [round(L.CONTENT_W / L.MM, 1), round(L.CONTENT_H / L.MM, 1)],
                   "slice_aspect_threshold": L.SLICE_ASPECT},
        "counts": {"catalog_pages": res.catalog_pages,
                   "evidence_pages": res.evidence_pages,
                   "total_pages": res.catalog_pages + res.evidence_pages,
                   "groups": len(cat.groups),
                   "units": cat.total_units},
        "groups": pm,
        "warnings": res.warnings,
        "errors": res.errors,
        "needs_review": res.review,
    }
    res.report_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# 证据册核对报告", "",
             f"- 目录来源：`{cat.source}`",
             f"- 版面：A4 竖版，每页 {res.plan.per_page} 张（版心 "
             f"{L.CONTENT_W / L.MM:.0f}×{L.CONTENT_H / L.MM:.0f}mm，页脚 18mm 留白）",
             f"- 页数：目录 {res.catalog_pages} 页（不编页码）+ 证据 {res.evidence_pages} 页"
             f"（第 1–{res.evidence_pages} 页）= 共 {res.catalog_pages + res.evidence_pages} 页",
             "",
             "## 页码对照（目录里的页数 = 此处实测）", "",
             "| 序号 | 证据名称 | 页数 | 材料 |", "| --- | --- | --- | --- |"]
    for g in cat.groups:
        files = "、".join(u.label for u in g.units) or "—"
        lines.append(f"| {g.no_label} | {' / '.join(r.name for r in g.rows)} | {g.pages_label} | {files} |")
    if res.review:
        lines += ["", "## 需人工确认（机器未替你做的实体判断）", ""] + [f"- {x}" for x in res.review]
    if res.warnings:
        lines += ["", "## 提示", ""] + [f"- {x}" for x in res.warnings]
    if res.errors:
        lines += ["", "## 阻断项", ""] + [f"- {x}" for x in res.errors]
    res.report_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _unit_info(u) -> dict:
    d = {"file": u.label, "kind": u.kind, "page": u.page + 1,
         "px": [u.width_px, u.height_px]}
    if u.slice_count > 1:
        d["slice"] = f"{u.slice_index + 1}/{u.slice_count}"
    return d
