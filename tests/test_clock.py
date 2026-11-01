"""Relógio local, conversão de fuso e contrato sem acesso a banco ou provedor."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from app.schemas.clock import CurrentDateTime
from app.services.clock import ClockService
from app.tools import build_tools


@pytest.mark.parametrize(
    "instant,date,time,offset,weekday,is_dst",
    [
        ("2026-11-02T02:30:00+00:00", "2026-11-01", "22:30:00", "-04:00", "domingo", False),
        ("2026-01-02T12:00:00+00:00", "2026-01-02", "08:00:00", "-04:00", "sexta-feira", False),
        ("2026-07-01T12:00:00+00:00", "2026-07-01", "08:00:00", "-04:00", "quarta-feira", False),
        ("2018-12-01T12:00:00+00:00", "2018-12-01", "09:00:00", "-03:00", "sábado", True),
    ],
)
def test_store_clock_respects_local_date_and_iana_rules(
    instant, date, time, offset, weekday, is_dst
):
    clock = ClockService(now=lambda: datetime.fromisoformat(instant))
    result = clock.get_current_datetime(CurrentDateTime())
    assert result["date"] == date
    assert result["time"] == time
    assert result["utc_offset"] == offset
    assert result["weekday"] == weekday
    assert result["is_daylight_saving_time"] is is_dst
    assert result["timezone"] == "America/Campo_Grande"
    assert datetime.fromisoformat(result["datetime"]) == datetime.fromisoformat(instant)
    assert result["utc_datetime"] == instant
    assert result["holiday_calendar_available"] is False
    assert "is_open" not in result


def test_clock_samples_one_instant_even_when_source_uses_another_timezone():
    calls = []

    def now():
        calls.append(True)
        return datetime(2026, 11, 2, 10, 0, tzinfo=timezone(timedelta(hours=9)))

    result = ClockService(now).get_current_datetime(CurrentDateTime())
    assert len(calls) == 1
    assert result["datetime"] == "2026-11-01T21:00:00-04:00"
    assert result["utc_datetime"] == "2026-11-02T01:00:00+00:00"
    assert result["utc_offset_seconds"] == -14400
    assert result["weekday_iso"] == 7


def test_naive_injected_clock_is_rejected():
    with pytest.raises(ValueError, match="com fuso"):
        ClockService(lambda: datetime(2026, 11, 1)).get_current_datetime(CurrentDateTime())


def test_clock_tool_works_without_database_or_api_key():
    tool = next(t for t in build_tools(sessionmaker()) if t.name == "get_current_datetime")
    before = datetime.now(UTC).replace(microsecond=0)
    result = tool.invoke({})
    after = datetime.now(UTC)
    assert result["status"] == "ok"
    assert before <= datetime.fromisoformat(result["datetime"]) <= after
    assert result["timezone"] == "America/Campo_Grande"
    assert result["holiday_calendar_available"] is False


@pytest.mark.parametrize("arguments", [{"timezone": "UTC"}, {"date": "2000-01-01"}])
def test_model_cannot_override_store_clock_or_timezone(arguments):
    tool = next(t for t in build_tools(sessionmaker()) if t.name == "get_current_datetime")
    assert tool.invoke(arguments)["status"] == "invalid_input"
