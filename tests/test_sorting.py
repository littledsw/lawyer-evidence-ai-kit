"""组内按时间排序：文件名优先、图内文字兜底、识别不到就不动。"""
import datetime as dt
from pathlib import Path

from evidence_kit import sorting
from evidence_kit.model import Unit


def _u(name: str, w=1170, h=2532) -> Unit:
    return Unit(path=Path("/m") / name, kind="image", width_px=w, height_px=h)


def test_detect_full_datetime():
    f = sorting.detect("2025年9月2日 21:14 王经理")
    assert f.when == dt.datetime(2025, 9, 2, 21, 14) and f.conf >= 0.9


def test_detect_from_filename_stamp():
    f = sorting.detect("微信图片_20250902131400")
    assert f.when == dt.datetime(2025, 9, 2, 13, 14)


def test_detect_date_only_and_month_day():
    assert sorting.detect("2025-08-28").when == dt.datetime(2025, 8, 28)
    assert sorting.detect("8月28日 19:40", ref_year=2025).when == dt.datetime(2025, 8, 28, 19, 40)


def test_filename_beats_ocr_text():
    u = _u("20250903_0800.jpg")
    f = sorting.time_of(u, "2025年1月1日 09:00")
    assert f.source == "文件名" and f.when.day == 3


def test_order_sorts_timed_in_place_and_leaves_untimed():
    """只对调识别到时间的那几张，没时间的（如整份台账）留在原位置。"""
    us = [_u("ledger.pdf"), _u("c.jpg"), _u("a.jpg"), _u("b.jpg")]
    texts = {id(us[1]): "2025年9月9日 06:12", id(us[2]): "2025年8月30日 07:05"}
    new, rows = sorting.order(us, texts)
    assert [x.path.name for x in new] == ["ledger.pdf", "a.jpg", "c.jpg", "b.jpg"]
    assert [r["source"] for r in rows][0] == "未识别"


def test_order_does_nothing_with_single_timestamp():
    us = [_u("a.jpg"), _u("b.jpg")]
    new, _rows = sorting.order(us, {id(us[0]): "2025年9月9日 06:12"})
    assert [x.path.name for x in new] == ["a.jpg", "b.jpg"]


def test_describe_flags_low_confidence():
    us = [_u("a.jpg"), _u("b.jpg")]
    _, rows = sorting.order(us, {id(us[0]): "9月2日 21:14", id(us[1]): "9月3日 08:00"})
    lines = sorting.describe(rows)
    assert any("低置信" in x for x in lines)
