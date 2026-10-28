"""组内按时间排序：微信聊天截图、分次扫描的同一份材料、按日拍摄的照片。

判断次序固定，且**每条结论都带出处**：
1. 文件名里的日期（`20250902`、`2025-09-02`、`微信图片_20250902131400.jpg`）——最稳，不改原件；
2. 图内文字里的日期时间（`2025年9月2日 21:14`、`2025-09-02 21:14:33`）——靠本地 OCR；
3. 什么都没有 → **不猜**，保持原顺序并在核对表里标「未识别到时间」。

排序只改页序，不改原件；核对表逐条列出「文件 → 识别到的时间 → 依据」，排错了改一行就行。
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

from .model import Unit

# (正则, 需要哪些组, 置信度)
_PATTERNS: list[tuple[re.Pattern, str, float]] = [
    (re.compile(r"(20\d{2})\s*[年\-/.](\d{1,2})\s*[月\-/.](\d{1,2})\s*日?\s*(\d{1,2})\s*[:：](\d{2})"),
     "ymdh", 0.95),
    (re.compile(r"(20\d{2})(\d{2})(\d{2})[_\-\s]?(\d{2})(\d{2})(\d{2})"), "ymdhms", 0.9),
    (re.compile(r"(20\d{2})-(\d{2})-(\d{2})[_\- ](\d{2})[-:：](\d{2})"), "ymdh", 0.9),
    (re.compile(r"(20\d{2})\s*[年\-/.](\d{1,2})\s*[月\-/.](\d{1,2})"), "ymd", 0.75),
    (re.compile(r"(20\d{2})(\d{2})(\d{2})(?!\d)"), "ymd", 0.7),
    (re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(\d{1,2})\s*[:：](\d{2})"), "mdh", 0.6),
]
_REL = {"今天": 0, "昨天": 1, "前天": 2}


@dataclass
class Found:
    when: dt.datetime | None
    source: str          # 文件名 / 图内文字 / 未识别
    conf: float = 0.0
    evidence: str = ""   # 命中的原文片段


def _mk(a: int, b: int, c: int, hh: int = 0, mm: int = 0) -> dt.datetime | None:
    try:
        return dt.datetime(a, b, c, hh, mm)
    except ValueError:
        return None


def detect(text: str, *, ref_year: int | None = None) -> Found:
    """从一段文字（文件名或 OCR 文本）里找最早出现的日期时间。"""
    t = text or ""
    ref_year = ref_year or dt.date.today().year
    for pat, kind, conf in _PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        g = m.groups()
        if kind == "ymdhms":
            when = _mk(int(g[0]), int(g[1]), int(g[2]), int(g[3]) % 24, int(g[4]) % 60)
        elif kind == "ymdh":
            when = _mk(int(g[0]), int(g[1]), int(g[2]), int(g[3]) % 24, int(g[4]) % 60)
        elif kind == "ymd":
            when = _mk(int(g[0]), int(g[1]), int(g[2]))
        elif kind == "mdh":
            when = _mk(ref_year, int(g[0]), int(g[1]), int(g[2]) % 24, int(g[3]) % 60)
        else:
            when = None
        if when:
            return Found(when, "", conf, m.group(0))
    for word, back in _REL.items():
        if word in t:
            day = dt.date.today() - dt.timedelta(days=back)
            return Found(dt.datetime(day.year, day.month, day.day), "", 0.4, word)
    return Found(None, "", 0.0, "")


def time_of(unit: Unit, text: str = "") -> Found:
    """先看文件名，再看图内文字（文件名置信度高，且不依赖 OCR）。"""
    f = detect(Path(unit.path).stem)
    if f.when:
        f.source = "文件名"
        return f
    f2 = detect(text)
    if f2.when:
        f2.source = "图内文字"
        return f2
    return Found(None, "未识别", 0.0, "")


def order(units: list[Unit], texts: dict[int, str] | None = None,
          ref_year: int | None = None) -> tuple[list[Unit], list[dict]]:
    """组内重排：只把**识别到时间的那几张**互相对调，其余单元位置不动。

    核对表按**排完之后的顺序**输出，逐条给时间与依据。
    """
    found = [time_of(u, (texts or {}).get(id(u), "")) for u in units]
    timed_idx = [i for i, f in enumerate(found) if f.when]
    new = list(units)
    if len(timed_idx) >= 2:
        ordered = sorted(timed_idx, key=lambda i: (found[i].when, i))
        for slot, src in zip(timed_idx, ordered):
            new[slot] = units[src]

    rows: list[dict] = []
    for pos, u in enumerate(new):
        src = next((i for i in range(len(units)) if units[i] is u), pos)
        f = found[src]
        rows.append({"file": u.label,
                     "when": f.when.strftime("%Y-%m-%d %H:%M") if f.when else "",
                     "source": f.source, "conf": round(f.conf, 2), "hit": f.evidence,
                     "slot": pos + 1})
    return new, rows


def describe(rows: list[dict]) -> list[str]:
    """核对表的人话版本（进报告）。"""
    if not rows:
        return []
    lines = []
    for i, r in enumerate(rows, start=1):
        if r["when"]:
            lines.append(f"{i}. {r['file']} → {r['when']}（{r['source']}「{r['hit']}」"
                         f"{'' if r['conf'] >= 0.9 else '，低置信'}）")
        else:
            lines.append(f"{i}. {r['file']} → 未识别到时间，保持原位置")
    return lines
