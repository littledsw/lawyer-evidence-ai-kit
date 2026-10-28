"""从 OCR 结果重建《证据目录》表格。

思路（不依赖检测表格线，拍照/截图/扫描都能用）：
1. 先切掉表下方的「提交人 / 代理人 / 提交日期」落款（它是表格结束的可靠标志）；
2. 找**表头行**：同一水平带上出现 ≥3 个表头词（序号/证据材料名称/证明目的/页数）；
3. 表头各词的 x 区间 ⇒ 列边界（相邻两格间隙的中点）；
4. **OCR 会把同一视觉行里相邻两格的文字读成一条**（实测：`中国石化加油IC卡台账 作管理和安排。`），
   所以先按列边界把跨列的行**用字符级坐标切开**，再做后续判断；
5. **序号列的数字就是行锚点**；行带宽 = 相邻两序号的中点之间；
6. 证明目的按**行块**分配：合并单元格的文字块跨越多行时，整块归它起始的那一行，
   其余行留空 ⇒ 落到 `to_catalog` 里就是「合并续行」（等价于 Word 的 vMerge continue）。

不猜、不补：识别不到的格子留空、低置信度行单独报出来，让人扫一眼就能改。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .ocr import OcrLine

HEADER_WORDS = {
    "no": ("序号", "编号", "序"),
    "name": ("证据材料名称", "证据名称", "材料名称", "名称"),
    "proves": ("证明目的", "证明对象", "证明内容", "拟证明"),
    "pages": ("页数", "页码"),
}
_ROW_NO = re.compile(r"^[0-9OoIl|]{1,3}$")
_SIGN = re.compile(r"^(提交人|代理人|提交日期|提交时间)[:：]?(.*)$")
_LEAD_JUNK = re.compile(r"^[\\|丨、,，.。·•\-\s]+")


def _norm(s: str) -> str:
    return re.sub(r"\s", "", s)


def _clean(s: str) -> str:
    return _LEAD_JUNK.sub("", s).strip()


def bands(lines: list[OcrLine], tol: float) -> list[list[OcrLine]]:
    """按纵坐标把文字行聚成水平带（同一条视觉行）。"""
    out: list[list[OcrLine]] = []
    for l in sorted(lines, key=lambda l: l.cy):
        if out and abs(l.cy - out[-1][0].cy) <= tol:
            out[-1].append(l)
        else:
            out.append([l])
    for b in out:
        b.sort(key=lambda l: l.x)
    return out


def _char_w(lines: list[OcrLine]) -> float:
    ws = [c[3] for l in lines if l.chars for c in l.chars if c[3] > 0]
    if not ws:
        return 8.0
    ws.sort()
    return ws[len(ws) // 2]


def gutter_candidates(lines: list[OcrLine]) -> list[tuple[float, int, float]]:
    """列间隙候选：同一行里相邻两字符之间的空白，超过半个字宽就可能是「单元格边界」。

    为什么要靠字符间隙：表头文字在各自列里**居中**，用表头词中点推列边界会偏移
    （实测把「证明目的」列的左边界推到了列内 100px 处）。而 OCR 把相邻两格读成一行时，
    恰恰在那条缝上留下一个明显的空白 —— 那是最可靠的边界证据。
    """
    cw = _char_w(lines)
    hits: list[tuple[float, float]] = []
    for l in lines:
        if not l.chars:
            continue
        cs = sorted((c[1], c[1] + c[3]) for c in l.chars if c[3] > 0)
        for (x0, x1), (n0, _n1) in zip(cs, cs[1:]):
            if n0 - x1 > cw * 0.55:
                hits.append(((x1 + n0) / 2, n0 - x1))
    clusters: list[list] = []
    for x, gap in sorted(hits):
        if clusters and x - clusters[-1][-1][0] <= 14:
            clusters[-1].append((x, gap))
        else:
            clusters.append([(x, gap)])
    out = []
    for c in clusters:
        xs = [x for x, _ in c]
        out.append((sum(xs) / len(xs), len(c), max(g for _, g in c)))
    return sorted(out)


def _left_mode(lines: list[OcrLine], lo_len: int | None = None,
               hi_len: int | None = None) -> float | None:
    """一组文字行**左边缘 x 的众数** —— 列内文字都从列左边距起排，这个值就是列左边界。"""
    from collections import Counter
    vals = []
    for l in lines:
        n = len(_norm(l.text))
        if lo_len is not None and n < lo_len:
            continue
        if hi_len is not None and n > hi_len:
            continue
        vals.append(round(l.x / 8) * 8)
    if not vals:
        return None
    return float(Counter(vals).most_common(1)[0][0])


_NOISH = re.compile(r"^[0-9OoIl|]{1,3}$")


def _column_bounds(body: list[OcrLine], fallback: list[float] | None = None) -> list[float]:
    """定出「序号 | 名称 | 证明目的」之间的两条列边界。

    不用表头词的中点：表头在各自列里居中，宽列会把边界推偏（实测偏进列内 100px）。
    改用**内容左边缘**：证明目的列是左对齐长文，它的左边缘众数就是该列左边界；
    名称列在其左侧，取「序号数字最右边缘」与「名称列最左边缘」的中点。
    """
    from collections import Counter

    cw = _char_w(body)
    longs: list[OcrLine] = []
    for minlen in (20, 12, 8):
        longs = [l for l in body if len(_norm(l.text)) >= minlen]
        if longs:
            break
    if not longs:
        return list(fallback or [])        # 全是很短的格子时退回表头中点，总比整张表挤成一列强
    # 跨格行（被 OCR 并成一条）除了整行起点，再投一票给它后半段的起点
    starts: list[int] = []
    for l in longs:
        starts.append(round(l.x / 4) * 4)
        seg = _last_segment_start(l, cw)
        if abs(seg - l.x) > 1:
            starts.append(round(seg / 4) * 4)
    counts = Counter(starts)
    proves_left = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
    b2 = proves_left - max(4.0, cw * 0.3)

    no_right = max((l.right for l in body if _NOISH.match(_norm(l.text))), default=None)
    lefts = [l.x for l in body if l.x < b2 and not _NOISH.match(_norm(l.text))]
    b1 = ((no_right + min(lefts)) / 2) if (no_right and lefts) else None
    return [b for b in (b1, b2) if b is not None]


def median_h(lines: list[OcrLine]) -> float:
    hs = sorted(l.h for l in lines) or [10.0]
    return hs[len(hs) // 2]


@dataclass
class OcrRow:
    no: int | None
    name: str
    proves: str
    conf: list[float] = field(default_factory=list)


def _split_by_bounds(line: OcrLine, bounds: list[float]) -> list[OcrLine]:
    """把跨列的一行按列边界切开（需要字符级坐标）。"""
    if not line.chars or not bounds:
        return [line]
    if not any(line.x < b < line.right for b in bounds):
        return [line]

    def col_idx(cx: float) -> int:
        i = 0
        while i < len(bounds) and cx > bounds[i]:
            i += 1
        return i

    segs: list[tuple[int, list]] = []
    for ch, x, y, w, h in line.chars:
        cx = x + w / 2 if w > 0 else x
        ci = col_idx(cx)
        if not segs or segs[-1][0] != ci:
            segs.append((ci, []))
        segs[-1][1].append((ch, x, y, w, h))

    out: list[OcrLine] = []
    for _ci, chars in segs:
        text = "".join(c[0] for c in chars).strip()
        if not text:
            continue
        x0 = min(c[1] for c in chars)
        y0 = min(c[2] for c in chars)
        x1 = max(c[1] + c[3] for c in chars)
        y1 = max(c[2] + c[4] for c in chars)
        out.append(OcrLine(text=text, confidence=line.confidence,
                           x=x0, y=y0, w=x1 - x0, h=y1 - y0))
    return out or [line]


def reconstruct(lines: list[OcrLine], bounds_hint=None) -> tuple[list[OcrRow], dict, list[str]]:
    """返回（行列表, 落款字典, 警告）。"""
    warn: list[str] = []
    if not lines:
        raise ValueError("这张图没识别出任何文字")
    lines = sorted(lines, key=lambda l: (l.cy, l.x))

    # ① 落款把表格与下文分开（落款一定在表格之后）
    sig_at = next((i for i, l in enumerate(lines) if _SIGN.match(_norm(l.text))), None)
    body = lines[:sig_at] if sig_at is not None else lines
    tail = lines[sig_at:] if sig_at is not None else []
    if not body:
        raise ValueError("除了落款没有别的内容")

    h = median_h(body)
    tol = max(6.0, h * 0.7)

    # ② 表头
    all_bands = bands(body, tol)
    hdr_i, hdr_cols = None, {}
    for i, b in enumerate(all_bands):
        hit = {}
        for key, words in HEADER_WORDS.items():
            for l in b:
                t = _norm(l.text)
                if any(w in t for w in words):
                    hit[key] = l
                    break
        if len(hit) >= 3:
            hdr_i, hdr_cols = i, hit
            break
    if hdr_i is None:
        raise ValueError("没找到表头（序号 / 证据材料名称 / 证明目的 / 页数）——这张图可能不是证据目录")
    hdr_band = all_bands[hdr_i]
    hdr_bottom = max(l.bottom for l in hdr_band)

    below = [l for l in body if l.cy >= hdr_bottom - 2]      # 表体（表头之上是标题，不能参与定列）
    cols = sorted(hdr_cols.items(), key=lambda kv: kv[1].x)
    keys = ["no", "name", "proves"]
    fallback = [(l1.right + l2.x) / 2 for (_k1, l1), (_k2, l2) in zip(cols, cols[1:])
                if _k1 == "no" or _k1 == "name"]
    bounds = _column_bounds(below, fallback) or (bounds_hint or [])
    reconstruct._last_bounds = bounds
    if not bounds:
        warn.append("列边界没定出来（材料里可能只有一列文字），整行按一列处理")
    missing = set(HEADER_WORDS) - set(keys)
    if missing:
        warn.append("表头没认全（缺 " + "、".join(sorted(missing)) + "），按认到的列处理")

    def col_of(l: OcrLine) -> str:
        i = 0
        while i < len(bounds) and l.cx > bounds[i]:
            i += 1
        k = keys[min(i, len(keys) - 1)]
        t = _norm(l.text)
        # 兜底纠偏：页数格里放得下长句、序号格里放不下长词 —— 说明边界偏了，按内容归还
        if k == "pages" and len(t) > 4 and "proves" in keys:
            return "proves"
        if k == "no" and "name" in keys and not _NOISH.match(t):
            return "name"
        return k

    # ③ 切开跨列的行，再取表体
    body = [s for l in below for s in _split_by_bounds(l, bounds)]
    if not body:
        raise ValueError("表头下面没有内容")

    # ④ 行锚点 = 序号列里的数字
    digit_anchors = [l for l in body if col_of(l) == "no" and _ROW_NO.match(_norm(l.text))]
    anchors = _merge_anchors(digit_anchors, _name_anchors(body, col_of, h), h)
    if not anchors:
        raise ValueError("序号列没识别出数字、名称列也没识别出行块，无法切分证据行")
    cuts = [hdr_bottom]
    for a, b in zip(anchors, anchors[1:]):
        cuts.append((a.bottom + b.y) / 2)
    cuts.append(max(l.bottom for l in body) + 1)

    def row_of(l: OcrLine) -> int:
        for i in range(len(anchors)):
            if cuts[i] <= l.cy < cuts[i + 1]:
                return i
        return len(anchors) - 1

    # ⑤ 证明目的：逐行按行带归属。
    # 纵向合并的格子会把文字落在**起始那一行**的带内（下面的行没有该格文字）→ 自然形成「续行」。
    proves_lines: dict[int, list[OcrLine]] = {}
    for l in body:
        if col_of(l) == "proves":
            proves_lines.setdefault(row_of(l), []).append(l)
    cross = sum(1 for r in rows_probe(anchors, body, col_of, row_of) if r > 1)

    rows: list[OcrRow] = []
    for i, a in enumerate(anchors):
        band = [l for l in body if cuts[i] <= l.cy < cuts[i + 1]]
        no_txt = re.sub(r"[^0-9]", "", _norm(a.text).translate(
            str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1"})))
        no = int(no_txt) if no_txt else None
        names = [l for l in band if col_of(l) == "name"]
        conf = [l.confidence for l in names] + [l.confidence for l in band
                                                if col_of(l) == "proves"]
        rows.append(OcrRow(no=no, name=_join(names),
                           proves=_join(proves_lines.get(i, [])), conf=conf))
        if not names:
            warn.append(f"序号 {no}：证据名称没识别出来")

    if cross:
        warn.append(f"{cross} 处证明目的跨越多行，已按「整块归起始行、其余行留空」处理"
                    f"（等同纵向合并），请核对")

    # ⑥ 落款
    case = {"submitter": "", "agent": "", "date": "", "title": "证据目录"}
    for l in tail:
        m = _SIGN.match(_norm(l.text))
        if not m:
            continue
        val = m.group(2).strip(":： ")
        if m.group(1) == "提交人":
            case["submitter"] = val
        elif m.group(1) == "代理人":
            case["agent"] = val
        else:
            case["date"] = "" if re.fullmatch(r"年?月?日?", val) else val
    if not case["submitter"] and not case["agent"]:
        warn.append("表下方没读到「提交人 / 代理人」落款")
    tops = [l.text.strip() for l in body if l.cy < hdr_band[0].cy and "目录" in l.text]
    if tops:
        case["title"] = tops[0]
    return rows, case, warn


def _last_segment_start(line: OcrLine, char_w: float) -> float:
    """一条文字行里最后一段（跨越空白之后）的起点 x。没有空白就返回行起点。"""
    if not line.chars:
        return line.x
    boxes = sorted((c[1], c[1] + c[3]) for c in line.chars if c[3] > 0)
    for (x0, x1), (n0, _n1) in zip(boxes, boxes[1:]):
        if n0 - x1 > char_w * 1.5:
            return n0
    return line.x


def _name_anchors(body: list[OcrLine], col_of, line_h: float) -> list[OcrLine]:
    """名称列的行块中心。

    为什么要它：OCR 漏掉某个序号数字（实测缩小到 60% 时「1」直接没识别出来）就会把两行证据
    并成一行、内容张冠李戴。名称每行一条，是稳定的备用锚点。
    """
    names = [l for l in body if col_of(l) == "name"]
    blocks: list[list[OcrLine]] = []
    for l in names:
        if blocks and (l.cy - max(x.cy for x in blocks[-1])) <= line_h * 1.1:
            blocks[-1].append(l)
        else:
            blocks.append([l])
    out = []
    for b in blocks:
        top = min(x.y for x in b)
        bot = max(x.bottom for x in b)
        out.append(OcrLine(text="", confidence=max(x.confidence for x in b),
                           x=min(x.x for x in b), y=top, w=0.0, h=bot - top))
    return out


def _merge_anchors(digits: list[OcrLine], names: list[OcrLine], line_h: float) -> list[OcrLine]:
    """两路锚点合并：同一行取带数字的那个（数字才是序号真值）。"""
    merged: list[OcrLine] = []
    for l in sorted(digits + names, key=lambda l: l.cy):
        if merged and abs(l.cy - merged[-1].cy) <= line_h * 0.9:
            if _ROW_NO.match(_norm(l.text)) and not _ROW_NO.match(_norm(merged[-1].text)):
                merged[-1] = l
            continue
        merged.append(l)
    return merged


def rows_probe(anchors, body, col_of, row_of) -> list[int]:
    """每一行带里「证明目的」的文字行数，用于发现跨行合并（仅供参考，不改写内容）。"""
    cnt = [0] * len(anchors)
    for l in body:
        if col_of(l) == "proves":
            cnt[row_of(l)] += 1
    return cnt


_SENT_END = "；;。：:！？!?"
_ITEM_START = re.compile(r"^\s*\d+\s*[:：.、]")


def _join(lines: list[OcrLine]) -> str:
    """同一格内的多行文字合并。

    **折行不是换行**：扫描/OCR 把一句话折成两行，直接连起来才是原文；
    只有上一行以句读收尾、或下一行以「1:」「2:」这类编号开头，才保留换行。
    """
    lines = sorted(lines, key=lambda l: (l.cy, l.x))
    out: list[str] = []
    last_cy, last_h = None, 0.0
    for l in lines:
        text = _clean(_LEAD_JUNK.sub("", l.text)) if not l.text.strip().isdigit() else l.text.strip()
        if last_cy is not None and abs(l.cy - last_cy) <= max(6.0, last_h * 0.6):
            out[-1] += text                                  # 同一条视觉行：直接拼
        elif out and not out[-1].endswith(tuple(_SENT_END)) and not _ITEM_START.match(text):
            out[-1] += text                                  # 折行：接上，不换行
        else:
            out.append(text)
        last_cy, last_h = l.cy, l.h or last_h
    return "\n".join(o for o in out if o).strip()


def to_catalog(rows: list[OcrRow], case: dict, source: str):
    """OCR 行 → Catalog。空白证明目的 = 合并续行（与 Word 的 vMerge continue 同义）。"""
    from .model import CaseInfo, Catalog, Group, Row

    groups: list[Group] = []
    seq = 0
    for r in rows:
        seq += 1
        row = Row(no=r.no or seq, name=r.name)
        if not r.proves and groups:
            groups[-1].rows.append(row)              # 续行：共用上一行的证明目的与页数
        else:
            groups.append(Group(rows=[row], proves=r.proves,
                                proves_source="user" if r.proves else "auto"))
    ci = CaseInfo(submitter=case.get("submitter", ""), agent=case.get("agent", ""),
                  date=case.get("date", ""), title=case.get("title") or "证据目录")
    return Catalog(case=ci, groups=groups, source=source)


def _rows_from(path, bounds_hint=None):
    """OCR + 重建。识别不出表格时，若歪斜明显就先矫正再重试一次。"""
    from . import ocr, preprocess

    _w, _h, lines = ocr.ocr_image(path, with_chars=True)
    try:
        return reconstruct(lines, bounds_hint=bounds_hint), 0.0
    except ValueError:
        angle = preprocess.estimate_angle(Path(path))
        if abs(angle) < preprocess.MIN_ANGLE:
            raise
        fixed, _a = preprocess.deskew(Path(path))
        _w, _h, lines = ocr.ocr_image(fixed, with_chars=True)
        return reconstruct(lines, bounds_hint=bounds_hint), angle


def catalog_from_image(path, deskew: bool = True) -> tuple:
    """单张图片 → (Catalog, 警告, 行)。

    歪斜策略（实测得出）：**先直接识别**；识别不出来再看歪斜角度、超过 2.5° 才校正重试。
    小角度（≤2°）校正会因插值损失反而降低准确率，所以不做。
    """
    from . import preprocess

    angle = 0.0
    if deskew:
        a = preprocess.estimate_angle(Path(path))
        if abs(a) >= preprocess.OCR_MIN_ANGLE:
            use, angle = preprocess.deskew(Path(path))
        else:
            use = Path(path)
    else:
        use = Path(path)
    (rows, case, warn), retry_angle = _rows_from(use)
    if retry_angle:
        warn.append(f"直接识别失败；检测到歪斜 {retry_angle:+.1f}°，矫正后重试成功")
    elif angle and abs(angle) >= preprocess.MIN_ANGLE:
        warn.append(f"图片歪斜 {angle:+.1f}°，已自动矫正后再识别")
    low = [r for r in rows if r.conf and min(r.conf) < 0.5]
    if low:
        warn.append("低置信度行（务必人工核对）：" + "、".join(str(r.no or "?") for r in low))
    return to_catalog(rows, case, str(path)), warn, rows


def catalog_from_images(paths, deskew: bool = True) -> tuple:
    """多张图片 = 一份跨页目录：逐页识别后按序号拼接成一整张表。

    跨页时第 2 页往往重复表头、或者干脆没有表头——没有表头的页沿用上一页的列边界，
    序号继续往下接；证明目的空白仍然按「合并续行」处理。
    """
    from . import ocr, preprocess

    all_rows: list[OcrRow] = []
    case: dict = {}
    warn: list[str] = []
    bounds_hint = None
    for i, p in enumerate(paths, start=1):
        use = Path(p)
        angle = 0.0
        if deskew:
            use, angle = preprocess.deskew(Path(p))
        if angle and abs(angle) >= preprocess.MIN_ANGLE:
            warn.append(f"第 {i} 页歪斜 {angle:+.1f}°，已自动矫正")
        _w, _h, lines = ocr.ocr_image(use, with_chars=True)
        try:
            rows, cs, w2 = reconstruct(lines, bounds_hint=bounds_hint)
        except ValueError as e:
            if "表头" in str(e) and i > 1:
                warn.append(f"第 {i} 页没找到表头，且无法沿用上一页的列边界 —— 该页跳过")
                continue
            raise
        bounds_hint = getattr(reconstruct, "_last_bounds", None)
        warn.extend(f"第 {i} 页：{x}" for x in w2)
        all_rows.extend(rows)
        case = case or cs
        if i > 1 and rows and all_rows and rows[0].no and all_rows[0].no:
            warn.append(f"第 {i} 页序号从 {rows[0].no} 开始，已与前一页拼接")
    if not all_rows:
        raise ValueError("这些图片里都没识别出证据行")
    low = [r for r in all_rows if r.conf and min(r.conf) < 0.5]
    if low:
        warn.append("低置信度行（务必人工核对）：" + "、".join(str(r.no or "?") for r in low))
    return to_catalog(all_rows, case, " + ".join(str(p) for p in paths)), warn, all_rows
