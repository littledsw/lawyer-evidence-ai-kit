"""读 Word 表格形式的《证据目录》（纯标准库：zipfile + ElementTree）。

真实目录的形态（见 `崔海涛证据目录.docx`）：
- 一张 4 列表：序号 / 证据材料名称 / 证明目的 / 页数；
- **序号 4、5 的「证明目的」是同一个纵向合并单元格**（`vMerge restart` → `vMerge continue`），
  这种行必须归成**同一个证据组**（共用证明目的、页数连排），不能拆成两条独立证据；
- 落款「提交人 / 代理人 / 提交日期」在表格外的段落里；
- 页数一列通常留空 —— 它由排版实测得出，不读文档里的值。

只认结构，不改写文字：读到什么写什么，读不到就留空（并在报告里标待补）。
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from .model import CaseInfo, Catalog, Group, Row

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_HEADER_NO = ("序号", "编号", "序 号")
_HEADER_NAME = ("证据材料名称", "证据名称", "材料名称", "证据名称")
_HEADER_PROVES = ("证明目的", "证明对象", "证明内容", "拟证明")


def _text(node) -> str:
    out = []
    for p in node.iter(f"{W}p"):
        seg = "".join(t.text or "" for t in p.iter(f"{W}t"))
        if seg.strip():
            out.append(seg.strip())
    return "\n".join(out) if out else "".join(t.text or "" for t in node.iter(f"{W}t")).strip()


def _cells(tr) -> list[tuple[str, int, str]]:
    """返回 [(文本, 横向合并宽度, 纵向合并状态)]，vMerge: restart / continue / ''。"""
    out = []
    for tc in tr.findall(f"{W}tc"):
        pr = tc.find(f"{W}tcPr")
        span, vm = 1, ""
        if pr is not None:
            gs = pr.find(f"{W}gridSpan")
            if gs is not None:
                span = int(gs.get(f"{W}val") or 1)
            v = pr.find(f"{W}vMerge")
            if v is not None:
                vm = v.get(f"{W}val") or "continue"
        out.append((_text(tc).strip(), span, vm))
    return out


def _pick(header: list[str], names: tuple[str, ...]) -> int | None:
    for i, h in enumerate(header):
        h = re.sub(r"\s", "", h)
        if any(n.replace(" ", "") in h for n in names):
            return i
    return None


def read_catalog_docx(path: Path) -> Catalog:
    z = zipfile.ZipFile(str(path))
    root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(f"{W}body")

    title = "证据目录"
    submitter = agent = date = ""
    for p in body.iter(f"{W}p"):
        t = "".join(x.text or "" for x in p.iter(f"{W}t")).strip()
        if not t:
            continue
        m = re.match(r"^(提交人|代理人|提交日期)\s*[:：]?\s*(.*)$", t)
        if m:
            val = m.group(2).strip()
            if m.group(1) == "提交人":
                submitter = val
            elif m.group(1) == "代理人":
                agent = val
            else:
                date = re.sub(r"\s+", "", val) if val.replace("年", "").replace("月", "").replace(
                    "日", "").strip() else ""
        elif "证据" in t and "目录" in t and len(t) <= 12 and title == "证据目录":
            title = t

    table = None
    for tbl in body.iter(f"{W}tbl"):
        first = _cells(tbl.findall(f"{W}tr")[0]) if tbl.findall(f"{W}tr") else []
        header = [c[0] for c in first]
        if _pick(header, _HEADER_NO) is not None and _pick(header, _HEADER_NAME) is not None:
            table = tbl
            break
    if table is None:
        raise ValueError(f"{path.name}：没找到含「序号 / 证据材料名称」的表格")

    rows = table.findall(f"{W}tr")
    header = _cells(rows[0])
    header_txt = [c[0] for c in header]
    i_no = _pick(header_txt, _HEADER_NO)
    i_name = _pick(header_txt, _HEADER_NAME)
    i_proves = _pick(header_txt, _HEADER_PROVES)

    groups: list[Group] = []
    cur: Group | None = None
    guess_no = 0
    for tr in rows[1:]:
        cells = _cells(tr)
        # 展平 gridSpan：合并列会让后续列索引偏移
        flat: list[tuple[str, str]] = []
        for text, span, vm in cells:
            for k in range(span):
                flat.append((text if k == 0 else "", vm if k == 0 else ""))
        if len(flat) <= max(i_no, i_name):
            continue

        no_txt = re.sub(r"\D", "", flat[i_no][0])
        name = flat[i_name][0].strip()
        proves = flat[i_proves][0].strip() if i_proves is not None and i_proves < len(flat) else ""
        vm = flat[i_proves][1] if i_proves is not None and i_proves < len(flat) else ""

        if not no_txt and not name:
            continue
        guess_no = int(no_txt) if no_txt else guess_no + 1
        row = Row(no=guess_no, name=name)

        if vm == "restart" or (proves and vm != "continue"):
            cur = Group(rows=[row], proves=proves,
                        proves_source="user" if proves else "auto")
            groups.append(cur)
        elif vm == "continue" and cur is not None:
            cur.rows.append(row)                     # 共用上一格的证明目的与页数
        else:
            cur = Group(rows=[row], proves=proves,
                        proves_source="user" if proves else "auto")
            groups.append(cur)

    if not groups:
        raise ValueError(f"{path.name}：表格里没有读到任何证据行")

    case = CaseInfo(submitter=submitter, agent=agent, date=date, title=title)
    return Catalog(case=case, groups=groups, source=str(path))
