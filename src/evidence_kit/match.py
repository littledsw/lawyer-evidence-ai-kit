"""平铺材料 → 证据组的匹配。

用户投递的是**一个平铺的文件夹**，不是分好的子目录，所以「哪个文件属于哪一项证据」必须
自动判出来。判断次序固定为：

1. **文件名里的序号前缀**（`04_`、`4-`、`第4`…）—— 最硬，直接对应目录序号；
2. **文件名与证据名称的共有词**（2/3 字滑窗）；
3. **OCR 文本**（可选）—— 只作辅助加分，绝不单独定案（`--no-ocr` 也能跑）。

歧义（两个候选分数接近）与零分一律**不猜**：进「待人工确认」清单。
猜错一个文件 = 证据张冠李戴，比多花两分钟复核严重得多。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .lexicon import concepts_of, label_of
from .model import Catalog, Row, natural_key

_PREFIX = re.compile(r"^\s*(?:第)?0*(\d{1,3})\s*[._\-、\s]")
_CJK = re.compile(r"[\u4e00-\u9fff]")
JUNK = {".ds_store", "thumbs.db", ".gitkeep", "desktop.ini"}


def is_junk(name: str) -> bool:
    """系统垃圾文件：不该出现在证据册里，也不该出现在「认不出」清单里。"""
    n = name.lower()
    return n in JUNK or n.startswith("._") or n.startswith("~$") or n.endswith(".tmp")


def _clean(s: str) -> str:
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s)


def grams(text: str, sizes=(2, 3)) -> set[str]:
    s = _clean(text)
    out: set[str] = set()
    for n in sizes:
        for i in range(len(s) - n + 1):
            g = s[i:i + n]
            if _CJK.search(g):
                out.add(g)
    return out


def leading_no(name: str) -> int | None:
    m = _PREFIX.match(name)
    return int(m.group(1)) if m else None


@dataclass
class Candidate:
    row: Row
    group_index: int
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class Decision:
    path: Path
    candidates: list[Candidate]

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def runner_up(self) -> Candidate | None:
        return self.candidates[1] if len(self.candidates) > 1 else None

    @property
    def status(self) -> str:
        b = self.best
        if b is None or b.score <= 0:
            return "unmatched"
        if b.score < MIN_SCORE:
            return "weak"
        r = self.runner_up
        if r and b.score - r.score < MIN_MARGIN:
            return "ambiguous"
        return "matched"


MIN_SCORE = 18.0
MIN_MARGIN = 6.0


def match(catalog: Catalog, files: list[Path],
          text_index: dict[str, str] | None = None) -> list[Decision]:
    rows = [(gi, r) for gi, g in enumerate(catalog.groups) for r in g.rows]
    # 只在唯一一行名称里出现的词，判别力最强
    gram_rows: dict[str, set[int]] = {}
    for gi, r in rows:
        for g in grams(r.name):
            gram_rows.setdefault(g, set()).add(gi)
    unique_grams = {gi: {g for g, owners in gram_rows.items() if owners == {gi}}
                    for gi, _ in rows}

    decisions: list[Decision] = []
    for p in files:
        if is_junk(p.name):
            continue
        cands: list[Candidate] = []
        stem = p.stem
        no = leading_no(p.name)
        f_con = concepts_of(p.stem)
        for gi, r in rows:
            r_con = concepts_of(r.name)
            # 类型冲突否决：材料类型与这一项的对应物类型完全不同 → 直接不是候选。
            # 真实案例：银行「查询账户明细（客户台账）」与「加油IC卡台账」字面都有「台账」。
            if f_con and r_con and not (f_con & r_con):
                continue
            score = 0.0
            why: list[str] = []
            shared = f_con & r_con
            if shared:
                score += 22.0 * len(shared)
                why.append("类型 " + "、".join(label_of(c) for c in sorted(shared)))
            if no is not None and no == r.no:
                score += 50
                why.append(f"文件名序号 {no} = 目录序号 {r.no}")
            hits = grams(stem) & grams(r.name)
            strong = hits & unique_grams.get(gi, set())
            if strong:
                score += 25 * min(1, len(strong) / 1)
                why.append("专属词 " + "、".join(sorted(strong, key=len, reverse=True)[:3]))
            if hits:
                score += min(18.0, 6.0 * len(hits))
                why.append(f"共有词 {len(hits)} 个")
            txt = (text_index or {}).get(str(p)) or (text_index or {}).get(p.name) or ""
            if txt:
                thits = grams(txt) & grams(r.name)
                if thits:
                    score += min(24.0, 8.0 * len(thits))
                    why.append(f"正文命中 {len(thits)} 个（仅辅助）")
            if score > 0:
                cands.append(Candidate(row=r, group_index=gi, score=score, reasons=why))
        cands.sort(key=lambda c: (-c.score, c.row.no))
        decisions.append(Decision(path=p, candidates=cands))
    return decisions


def apply(catalog: Catalog, decisions: list[Decision],
          eligible: set[int] | None = None) -> tuple[int, list[str]]:
    """把已定案的匹配写进目录；返回（写入数，待确认说明）。

    `eligible` 给定时，只往这些行（按 id(row)）里塞文件——已经有明确文件的行走原路，不被覆盖。
    """
    written = 0
    notes: list[str] = []
    by_row: dict[int, list[Path]] = {}
    for d in decisions:
        if d.status == "matched" and (eligible is None or id(d.best.row) in eligible):
            by_row.setdefault(id(d.best.row), []).append(d.path)
            written += 1
        elif d.status == "ambiguous":
            alt = "、".join(f"证据{c.row.no}（{c.score:.0f}）" for c in d.candidates[:3])
            notes.append(f"{d.path.name}：候选分数接近，未自动归入 —— {alt}；请人工指定")
        elif d.status == "weak":
            b = d.best
            notes.append(f"{d.path.name}：只弱匹配到证据{b.row.no}（{b.score:.0f} 分："
                         f"{'；'.join(b.reasons)}），未自动归入")
        else:
            con = concepts_of(d.path.stem)
            if con:
                notes.append(f"{d.path.name}：像「{'、'.join(label_of(c) for c in sorted(con))}」，"
                             f"但目录里没有对应的一项 —— 是漏列？还是本案不需要（如裁决书）？")
            else:
                notes.append(f"{d.path.name}：认不出属于哪项证据，未自动归入")
    for g in catalog.groups:
        for r in g.rows:
            got = sorted(by_row.get(id(r), []), key=natural_key)
            if got:
                r.files = list(r.files) + got
    for g in catalog.groups:
        for r in g.rows:
            if not r.files:
                notes.append(f"证据{r.no}「{r.name}」：没有任何文件匹配上（材料缺失？名称需改？）")
    return written, notes
