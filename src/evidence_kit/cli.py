"""命令行入口：脚本可跑，不依赖任何 LLM 调用。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import pipeline
from .catalog import auto_catalog, dump_yaml


def _build(a: argparse.Namespace) -> int:
    try:
        res = pipeline.build(catalog_path=a.catalog, materials=Path(a.materials),
                             out_dir=Path(a.out), compact=a.compact,
                             per_page=a.per_page, auto=a.auto,
                             allow_low_dpi=a.allow_low_dpi)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"证据册：{res.book}")
    print(f"目录页：{res.catalog_pages} 页（不编页码）；证据页：{res.evidence_pages} 页（第 1–{res.evidence_pages} 页）")
    for g in res.catalog.groups:
        print(f"  证据{g.no_label}  {g.pages_label:>7}  {' / '.join(r.name for r in g.rows)}")
    for w in res.warnings:
        print(f"  ! {w}")
    for r in res.review:
        print(f"  ? {r}")
    print(f"报告：{res.report_md}")
    return 0


def _dump(a: argparse.Namespace) -> int:
    cat = auto_catalog(Path(a.materials))
    out = Path(a.out)
    dump_yaml(cat, out)
    print(f"目录草稿：{out}（补好证明目的后用它 build）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evidence-kit", description="证据目录 + 证据册 PDF 生成器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="目录 + 材料 → 证据册 PDF")
    b.add_argument("--catalog", help="目录 yaml/json；不给则按 materials 的子目录自动生成")
    b.add_argument("--materials", required=True, help="材料根目录")
    b.add_argument("--out", required=True, help="产物目录")
    b.add_argument("--compact", action="store_true", help="紧凑模式：一页多张（默认 2 张/页）")
    b.add_argument("--per-page", type=int, default=None, help="一页几张（显式指定，优先于 --compact）")
    b.add_argument("--auto", action="store_true", help="忽略目录文件，按子目录自动分组")
    b.add_argument("--allow-low-dpi", action="store_true", help="允许 <100 DPI 的材料出册（会糊）")
    b.set_defaults(func=_build)

    d = sub.add_parser("dump", help="把材料文件夹生成目录草稿 yaml（给律师改字用）")
    d.add_argument("--materials", required=True)
    d.add_argument("--out", required=True)
    d.set_defaults(func=_dump)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
