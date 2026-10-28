"""目录的读写：yaml/json（人工填好的），以及「把材料文件夹当目录」的兜底模式。

目录内容属于**实体判断**，本模块只搬运、不改写：
- 用户在 yaml 里填的证明目的 → `proves_source="user"`
- 从文件夹自动生成的空证明目的 → `proves_source="auto"`，渲染时显式标「待补」并进待复核清单
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .model import CaseInfo, Catalog, Group, Row, natural_key

_LEAD_NUM = re.compile(r"^\s*\d+\s*[._\-、\s]*")


def load_catalog(path: Path | None, materials: Path, auto: bool = False) -> Catalog:
    if path is None or (auto and not Path(path).exists()):
        return auto_catalog(materials)
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in {".json"}:
        data = json.loads(text)
    else:
        import yaml
        data = yaml.safe_load(text)
    return parse_dict(data, source=str(p))


def parse_dict(data: dict, source: str = "") -> Catalog:
    cs = data.get("case") or {}
    case = CaseInfo(
        submitter=str(cs.get("submitter") or ""),
        agent=str(cs.get("agent") or ""),
        date=str(cs.get("date") or ""),
        title=str(cs.get("title") or "证据目录"),
    )
    groups: list[Group] = []
    next_no = 1
    for gi, g in enumerate(data.get("groups") or [], start=1):
        rows = []
        for ri, r in enumerate(g.get("rows") or [], start=1):
            files = r.get("files") or []
            if isinstance(files, str):
                files = [files]
            no = int(r.get("no") or next_no)
            next_no = no + 1
            rows.append(Row(no=no, name=str(r.get("name") or "").strip(),
                            files=[Path(f) for f in files]))
        if not rows:
            raise ValueError(f"{source}: 第 {gi} 组没有任何行")
        proves = str(g.get("proves") or "").strip()
        groups.append(Group(rows=rows, proves=proves,
                            proves_source=str(g.get("proves_source") or ("user" if proves else "auto"))))
    if not groups:
        raise ValueError(f"{source}: 没有任何证据组")
    return Catalog(case=case, groups=groups, source=source)


def auto_catalog(materials: Path) -> Catalog:
    """材料文件夹即目录：每个子文件夹 = 一个证据组，按名字自然序。"""
    materials = Path(materials)
    subs = sorted((d for d in materials.iterdir() if d.is_dir()), key=natural_key)
    groups: list[Group] = []
    for i, d in enumerate(subs, start=1):
        name = _LEAD_NUM.sub("", d.name) or d.name
        groups.append(Group(rows=[Row(no=i, name=name, files=[d])],
                            proves="", proves_source="auto"))
    if not groups:                        # 没有子目录 → 整目录算一组
        files = sorted((f for f in materials.iterdir() if f.is_file()), key=natural_key)
        if files:
            groups.append(Group(rows=[Row(no=1, name=materials.name, files=files)],
                                proves="", proves_source="auto"))
    if not groups:
        raise ValueError(f"{materials} 里没有材料")
    return Catalog(case=CaseInfo(), groups=groups, source="auto:" + str(materials))


def dump_yaml(catalog: Catalog, path: Path) -> None:
    """把解析结果写成可编辑的 yaml：让用户改字，而不是让机器猜。"""
    import yaml
    data = {
        "case": {"title": catalog.case.title, "submitter": catalog.case.submitter,
                 "agent": catalog.case.agent, "date": catalog.case.date},
        "groups": [
            {"proves": g.proves,
             "rows": [{"no": r.no, "name": r.name,
                       "files": [str(f) for f in r.files]} for r in g.rows]}
            for g in catalog.groups
        ],
    }
    Path(path).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                                         default_flow_style=False, width=120), encoding="utf-8")
