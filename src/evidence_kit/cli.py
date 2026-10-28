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
                             allow_low_dpi=a.allow_low_dpi,
                             flat_match=not a.no_flat)
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


def _parse(a: argparse.Namespace) -> int:
    from pathlib import Path as _P
    from .docx_catalog import read_catalog_docx
    src = _P(a.docx or a.image)
    if src.suffix.lower() in {".png", ".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff"}:
        from .table import catalog_from_image
        cat, warns, rows = catalog_from_image(src)
        for w in warns:
            print(f"  ! {w}")
    else:
        cat = read_catalog_docx(src)
    dump_yaml(cat, _P(a.out))
    print(f"读到 {len(cat.groups)} 组 / {sum(len(g.rows) for g in cat.groups)} 行 → {a.out}")
    for g in cat.groups:
        print(f"  证据{g.no_label:>6}  {' / '.join(r.name for r in g.rows)}"
              f"  证明目的 {len(g.proves)} 字")
    return 0


def _ocr(a: argparse.Namespace) -> int:
    import json
    from pathlib import Path as _P
    from . import ocr as ocr_mod
    from .match import is_junk
    mat = _P(a.materials)
    files = [f for f in sorted(mat.iterdir()) if f.is_file() and not is_junk(f.name)]
    index: dict[str, dict] = {}
    for f in files:
        ext = f.suffix.lower()
        try:
            if ext == ".pdf":
                pages = ocr_mod.ocr_pdf_pages(f, max_pages=a.max_pages)
                index[f.name] = {"pages": pages,
                                 "chars": sum(len(p["text"]) for p in pages),
                                 "ocr_pages": sum(1 for p in pages if p["source"] == "ocr")}
            elif ext in {".png", ".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff"}:
                _w, _h, lines = ocr_mod.ocr_image(f)
                text = "\n".join(l.text for l in sorted(lines, key=lambda l: (round(l.cy / 8), l.x)))
                index[f.name] = {"pages": [{"page": 1, "source": "ocr", "text": text,
                                            "lines": [{"t": l.text, "c": round(l.confidence, 3)}
                                                      for l in lines]}],
                                 "chars": len(text), "ocr_pages": 1}
            else:
                index[f.name] = {"pages": [], "chars": 0, "ocr_pages": 0}
        except Exception as e:                                   # noqa: BLE001
            index[f.name] = {"pages": [], "chars": 0, "ocr_pages": 0, "error": str(e)}
        print(f"  {f.name}: {index[f.name].get('chars', 0)} 字"
              f"{'（OCR）' if index[f.name].get('ocr_pages') else ''}")
    _P(a.out).write_text(json.dumps(index, ensure_ascii=False, indent=1)[:0] or
                         json.dumps(index, ensure_ascii=False), encoding="utf-8")
    print(f"文字索引：{a.out}")
    return 0


def _match(a: argparse.Namespace) -> int:
    from pathlib import Path as _P
    from . import match as m
    from .catalog import load_catalog
    cat = load_catalog(_P(a.catalog), _P(a.materials))
    mat = _P(a.materials)
    files = sorted([f for f in mat.iterdir() if f.is_file()], key=lambda p: p.name)
    decisions = m.match(cat, files)
    tag = {"matched": "归位", "ambiguous": "歧义", "weak": "弱匹配", "unmatched": "认不出"}
    for d in decisions:
        b = d.best
        tail = f"→ 证据{b.row.no}「{b.row.name}」{b.score:.0f}分（{'；'.join(b.reasons)}）" if b else "→ 无候选"
        print(f"[{tag[d.status]}] {d.path.name}  {tail}")
    eligible = {id(r) for g in cat.groups for r in g.rows if not r.files}
    n, notes = m.apply(cat, decisions, eligible=eligible)
    print(f"\n共 {len(files)} 个文件，自动归位 {n} 个，需人工 {len(notes)} 条")
    for x in notes:
        print(f"  ? {x}")
    if a.out:
        dump_yaml(cat, _P(a.out))
        print(f"归位后的目录：{a.out}")
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
    b.add_argument("--no-flat", action="store_true", help="不做平铺自动归位（材料已按子目录分好时用）")
    b.set_defaults(func=_build)

    pz = sub.add_parser("parse", help="读《证据目录》（Word 或 拍照/截图）→ yaml 草稿")
    pz.add_argument("--docx", help="Word 版目录")
    pz.add_argument("--image", help="拍照/截图版目录（本地 OCR 识别）")
    pz.add_argument("--out", required=True)
    pz.set_defaults(func=_parse)

    oc = sub.add_parser("ocr", help="给材料建文字索引（扫描件逐页 OCR，本地，不联网）")
    oc.add_argument("--materials", required=True)
    oc.add_argument("--out", required=True)
    oc.add_argument("--max-pages", type=int, default=None, help="每份 PDF 最多识别几页（试跑用）")
    oc.set_defaults(func=_ocr)

    m = sub.add_parser("match", help="预览：平铺材料会被归到哪一项证据（不排版，只出对照）")
    m.add_argument("--catalog", required=True)
    m.add_argument("--materials", required=True)
    m.add_argument("--out", help="把归位结果写成 yaml（可选）")
    m.set_defaults(func=_match)

    d = sub.add_parser("dump", help="把材料文件夹生成目录草稿 yaml（给律师改字用）")
    d.add_argument("--materials", required=True)
    d.add_argument("--out", required=True)
    d.set_defaults(func=_dump)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
