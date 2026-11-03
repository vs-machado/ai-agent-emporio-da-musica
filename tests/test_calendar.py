"""Calendário real holidays, sem banco, chaves ou consultas externas."""

from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from app.schemas.calendar import HolidayLookup
from app.services.calendar import CalendarService
from app.tools import build_tools


@pytest.mark.parametrize(
    "target_date,expected_name",
    [
        ("2026-11-02", "Finados"),
        ("2027-10-11", "Criação do Estado"),
        ("2026-04-03", "Sexta-feira Santa"),
        ("2027-03-26", "Sexta-feira Santa"),
        ("2026-11-20", "Consciência Negra"),
        ("2026-12-25", "Natal"),
    ],
)
def test_national_state_and_movable_holidays(target_date, expected_name):
    result = CalendarService().check_holiday(HolidayLookup(target_date=target_date))
    assert result["status"] == "ok"
    assert result["is_holiday"] is True
    assert expected_name in result["holiday_name"]
    assert result["date"] == target_date


@pytest.mark.parametrize(
    "target_date",
    [
        "2026-11-03",  # Dia comum.
        "2026-11-27",  # Black Friday não define feriado nem horário especial.
        "2026-02-17",  # Carnaval: OPTIONAL, não PUBLIC no calendário BR/MS.
        "2026-06-04",  # Corpus Christi: não inclui regras municipais.
        "2026-08-26",  # Não inclui aniversário municipal de Campo Grande.
        "2026-07-09",  # Feriado de outro estado não se aplica a MS.
    ],
)
def test_negative_result_is_scoped_and_does_not_confirm_opening(target_date):
    result = CalendarService().check_holiday(HolidayLookup(target_date=target_date))
    assert result["is_holiday"] is False
    assert result["holiday_name"] is None
    assert result["country"] == "BR"
    assert result["subdivision"] == "MS"
    assert result["municipal_holidays_checked"] is False
    assert result["optional_holidays_included"] is False
    assert result["special_hours_checked"] is False
    assert "is_open" not in result


@pytest.mark.parametrize(
    "value",
    ["2026-02-30", "02/11/2026", "2026-11-02T00:00:00", "1995-12-31", "2101-01-01", 0],
)
def test_invalid_or_unsupported_dates_are_rejected(value):
    with pytest.raises(ValidationError):
        HolidayLookup(target_date=value)


@pytest.mark.parametrize("value", [date(1996, 1, 1), date(2100, 12, 31)])
def test_supported_boundary_years_are_evaluated(value):
    assert CalendarService().check_holiday(HolidayLookup(target_date=value))["status"] == "ok"


def test_holiday_tool_works_with_no_database_or_api_key():
    tool = next(t for t in build_tools(sessionmaker()) if t.name == "check_holiday")
    result = tool.invoke({"target_date": "2026-11-02"})
    assert result["holiday_name"] == "Finados"
    assert result["source"] == "python-holidays"
    assert "Parâmetros inválidos" in tool.invoke({"target_date": "2026-11-02", "subdivision": "SP"})
