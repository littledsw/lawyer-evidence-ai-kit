"""OCR 目录识别基准：拿「已知真值」的目录图跑一遍，报准确率。

用法（真值来自同一份 Word/结构化目录）：
    python examples/ocr_benchmark.py --docx 崔海涛证据目录.docx [--image 另一张图]

不带 --image 时用 macOS Quick Look 把 docx 渲染成 PNG 当输入（不装 Office 也能跑），
再派生出「缩到 60%」「JPEG 低质量 + 轻微旋转」两种劣化版本，看准确率怎么掉。
"""
from __future__ import annotations

import argparse
import difflib
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from evidence_kit.docx_catalog import read_catalog_docx
from evidence_kit.table import catalog_from_image


def quicklook_png(docx: Path) -> Path:
    out = Path(tempfile.mkdtemp(prefix="evkit-ql-"))
    subprocess.run(["qlmanage", "-t", "-s", "2400", "-o", str(out), str(docx)],
                   capture_output=True, check=True)
    pngs = list(out.glob("*.png"))
    if not pngs:
        raise RuntimeError("Quick Look 没能渲染出图片")
    return pngs[0]


def degrade(src: Path, scale: float, jpeg_q: int | None, rotate: float) -> Path:
    im = Image.open(src).convert("RGB")
    if scale != 1.0:
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
    if rotate:
        im = im.rotate(rotate, resample=Image.BICUBIC, expand=True, fillcolor="white")
    out = src.with_name(f"{src.stem}_s{int(scale * 100)}_q{jpeg_q or 'p'}_r{rotate}.png")
    if jpeg_q:
        tmp = out.with_suffix(".jpg")
        im.save(tmp, "JPEG", quality=jpeg_q)
        im = Image.open(tmp).convert("RGB")
    im.save(out, "PNG")
    return out


def norm(s: str) -> str:
    return (s.replace("：", ":").replace("；", ";").replace("\n", "").replace(" ", "")
            .replace("\u3000", ""))


def score(gt, cat) -> dict:
    g_names = [r.name for g in gt.groups for r in g.rows]
    o_names = [r.name for g in cat.groups for r in g.rows]
    g_proves = [g.proves for g in gt.groups]
    o_proves = [g.proves for g in cat.groups]
    name_hit = sum(1 for a, b in zip(g_names, o_names) if norm(a) == norm(b))
    pr_sim = [difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()
              for a, b in zip(g_proves, o_proves)] or [0.0]
    return {
        "组数一致": len(gt.groups) == len(cat.groups),
        "行数一致": len(g_names) == len(o_names),
        "名称完全命中": f"{name_hit}/{len(g_names)}",
        "证明目的平均相似": round(sum(pr_sim) / len(pr_sim), 3),
        "落款": f"{cat.case.submitter}/{cat.case.agent}" == f"{gt.case.submitter}/{gt.case.agent}",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docx", help="真值来源：Word 版证据目录")
    ap.add_argument("--image", help="只测图片（此时没有真值，只打印识别结果）")
    a = ap.parse_args()

    if a.image and not a.docx:
        cat, warn, rows = catalog_from_image(Path(a.image))
        for w in warn:
            print("!", w)
        for g in cat.groups:
            print(f"证据{g.no_label}: {' / '.join(r.name for r in g.rows)}\n   {g.proves}")
        return

    gt = read_catalog_docx(Path(a.docx))
    base = quicklook_png(Path(a.docx)) if not a.image else Path(a.image)
    cases = [("原图", base)]
    if not a.image:
        cases += [
            ("缩到 60%（手机拍打印件）", degrade(base, 0.6, None, 0)),
            ("缩到 60% + JPEG q45 + 转 1.5°", degrade(base, 0.6, 45, 1.5)),
        ]
    print(f"真值：{len(gt.groups)} 组 / {sum(len(g.rows) for g in gt.groups)} 行\n")
    for label, img in cases:
        print(f"—— {label}（{img.name}）")
        try:
            cat, warn, _rows = catalog_from_image(img)
            for k, v in score(gt, cat).items():
                print(f"   {k}: {v}")
            for w in warn:
                print(f"   ! {w}")
        except Exception as e:                                   # noqa: BLE001
            print(f"   失败：{e}")
        print()


if __name__ == "__main__":
    main()
