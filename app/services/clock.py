"""Relógio do atendimento, independente de banco, LLM e regras de expediente."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.schemas.clock import CurrentDateTime

STORE_TIMEZONE = "America/Campo_Grande"
WEEKDAYS = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)


class ClockService:
    def __init__(self, now: Callable[[], datetime] | None = None):
        self.now = now or (lambda: datetime.now(UTC))
        self.timezone = ZoneInfo(STORE_TIMEZONE)

    def get_current_datetime(self, request: CurrentDateTime) -> dict:
        """Retorna um único instante convertido para o fuso IANA da loja.

        Args:
            request: Consulta vazia; o fuso não pode ser substituído pelo modelo.

        Returns:
            Data/hora local e UTC, dia da semana, offset e indicador de horário
            de verão. Não consulta calendário de feriados nem define abertura da loja.

        Raises:
            ValueError: O relógio injetado fornece um instante sem informação de fuso.
        """
        instant = self.now()
        if instant.utcoffset() is None:
            raise ValueError("O relógio deve fornecer uma data/hora com fuso")
        local = instant.astimezone(self.timezone)
        return {
            "status": "ok",
            "datetime": local.isoformat(timespec="seconds"),
            "date": local.date().isoformat(),
            "time": local.strftime("%H:%M:%S"),
            "timezone": self.timezone.key,
            "utc_offset": local.strftime("%:z"),
            "utc_offset_seconds": int(local.utcoffset().total_seconds()),
            "weekday": WEEKDAYS[local.weekday()],
            "weekday_iso": local.isoweekday(),
            "is_daylight_saving_time": local.dst() != timedelta(0),
            "utc_datetime": instant.astimezone(UTC).isoformat(timespec="seconds"),
            "holiday_calendar_available": False,
        }
