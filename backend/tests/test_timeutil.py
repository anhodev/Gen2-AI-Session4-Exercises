"""Test tiện ích múi giờ dùng chung."""

from datetime import datetime, time, timedelta, timezone

import pytest

from app.core.timeutil import get_tz, gmt_label, parse_hhmm, to_app_tz

VN = get_tz("Asia/Ho_Chi_Minh")


def test_time_without_timezone_gets_app_timezone():
    # Email chỉ viết "9 giờ" -> hiểu là 9 giờ GMT+7
    value = to_app_tz(datetime(2026, 10, 21, 9, 0), VN)
    assert value.utcoffset() == timedelta(hours=7)
    assert value.hour == 9


def test_other_timezone_is_converted_not_relabeled():
    value = to_app_tz(datetime(2026, 10, 21, 2, 0, tzinfo=timezone.utc), VN)
    assert value.hour == 9  # cùng thời điểm, đổi sang giờ kho


@pytest.mark.parametrize("offset, label", [(7, "GMT+7"), (0, "GMT+0"), (-5, "GMT-5"), (5.5, "GMT+5:30")])
def test_gmt_label(offset, label):
    tz = timezone(timedelta(hours=offset))
    assert gmt_label(datetime(2026, 10, 21, 9, 0, tzinfo=tz)) == label


def test_parse_hhmm():
    assert parse_hhmm("08:00") == time(8, 0)
    with pytest.raises(ValueError):
        parse_hhmm("8h")
