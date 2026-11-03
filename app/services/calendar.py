"""Calendário offline; não decide expediente nem consulta feriados municipais."""

import holidays

from app.schemas.calendar import HolidayLookup


class CalendarService:
    def check_holiday(self, request: HolidayLookup) -> dict:
        calendar = holidays.country_holidays(
            "BR",
            subdiv="MS",
            years=request.target_date.year,
            categories=holidays.PUBLIC,
            language="pt_BR",
            observed=False,
            expand=False,
        )
        name = calendar.get(request.target_date)
        return {
            "status": "ok",
            "date": request.target_date.isoformat(),
            "is_holiday": name is not None,
            "holiday_name": name,
            "country": "BR",
            "subdivision": "MS",
            "source": "python-holidays",
            "municipal_holidays_checked": False,
            "optional_holidays_included": False,
            "special_hours_checked": False,
            "limitations": (
                "Consulta feriados nacionais e de MS conforme a biblioteca holidays, sem "
                "feriados municipais, pontos facultativos ou horários especiais da loja. "
                "Ausência de feriado nesse calendário não confirma abertura. Combine com "
                "a política de expediente; não presuma extensão na Black Friday ou no Natal."
            ),
        }
