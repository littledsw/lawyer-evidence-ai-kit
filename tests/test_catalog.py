"""目录解析：序号跨组连续、合并组保留、空证明目的不得被当成已确认。"""
from pathlib import Path

from evidence_kit.catalog import auto_catalog, dump_yaml, parse_dict


def _data():
    return {
        "case": {"submitter": "崔某某", "agent": "王某某"},
        "groups": [
            {"proves": "1:证明工资 4500 元；", "rows": [
                {"no": 1, "name": "银行工资流水", "files": "01_流水"}]},
            {"proves": "证明其接受工作管理。", "rows": [
                {"no": 3, "name": "加油台账", "files": ["a.pdf"]},
                {"no": 4, "name": "停车缴费", "files": ["b.jpg"]}]},
        ],
    }


def test_parse_keeps_merged_group_and_running_numbers():
    c = parse_dict(_data(), source="t")
    assert len(c.groups) == 2
    g2 = c.groups[1]
    assert [r.no for r in g2.rows] == [3, 4]      # 同组两行共用证明目的与页数
    assert g2.proves_source == "user"


def test_missing_numbers_are_continued_not_restarted():
    d = _data()
    for r in d["groups"][1]["rows"]:
        r.pop("no")
    c = parse_dict(d, source="t")
    assert [r.no for g in c.groups for r in g.rows] == [1, 2, 3]


def test_empty_proves_marked_auto():
    d = _data()
    d["groups"][0]["proves"] = ""
    c = parse_dict(d, source="t")
    assert c.groups[0].proves_source == "auto"


def test_auto_catalog_from_subdirs(tmp_path):
    (tmp_path / "01_银行流水").mkdir()
    (tmp_path / "02_社保").mkdir()
    (tmp_path / "01_银行流水" / "a.jpg").write_bytes(b"x")
    c = auto_catalog(tmp_path)
    assert [r.name for g in c.groups for r in g.rows] == ["银行流水", "社保"]
    assert all(g.proves_source == "auto" for g in c.groups)


def test_dump_yaml_roundtrip(tmp_path):
    c = parse_dict(_data(), source="t")
    out = tmp_path / "c.yaml"
    dump_yaml(c, out)
    c2 = parse_dict(__import__("yaml").safe_load(out.read_text(encoding="utf-8")), "t")
    assert [(r.no, r.name) for g in c2.groups for r in g.rows] == \
           [(r.no, r.name) for g in c.groups for r in g.rows]
    assert Path("01_流水") == c2.groups[0].rows[0].files[0]
