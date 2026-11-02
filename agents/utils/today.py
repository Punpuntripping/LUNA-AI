"""Today's date, as one line for an agent's prompt.

A model knows no date of its own. Ask it how long ago 1 August was and it guesses,
and a guessed date turns into wrong deadline advice. So the agents that reason
about time read this line, printed after their static prompt: it changes once a
day, and the static prefix above it stays cached.

Spec: ``.claude/plans/conversation_memory_agent.md`` §3.
"""
from __future__ import annotations

import logging
from datetime import date

from shared.library.supersession import riyadh_today

__all__ = ["today_line"]

logger = logging.getLogger(__name__)

_GREGORIAN_MONTHS = (
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
)

#: Not hijridate's ``month_name("ar")``: it prints month 4 as «ربيع الثاني», and
#: Saudi courts and our own corpus write «ربيع الآخر». A lawyer can copy this date
#: into a document. The other eleven match the library's names.
_HIJRI_MONTHS = (
    "محرم", "صفر", "ربيع الأول", "ربيع الآخر", "جمادى الأولى", "جمادى الآخرة",
    "رجب", "شعبان", "رمضان", "شوال", "ذو القعدة", "ذو الحجة",
)

_LINE = (
    "Today is {date} (Saudi time). Use this for any deadline, elapsed time or "
    "reference to today. Never guess the date. Do not add it to a document unless "
    "the document calls for a date."
)


def _gregorian(day: date) -> str:
    return f"{day.day} {_GREGORIAN_MONTHS[day.month - 1]} {day.year}م"


def _hijri(day: date) -> str:
    """The Umm al-Qura date. Raises outside the library's table (1924 to 2077)."""
    # Imported here so a missing package costs the Hijri date, not every agent.
    from hijridate import Gregorian

    hijri = Gregorian.fromdate(day).to_hijri()
    return f"{hijri.day} {_HIJRI_MONTHS[hijri.month - 1]} {hijri.year}هـ"


def today_line() -> str:
    """The date line, in Saudi time and Latin digits. Never raises into a turn.

    A failed Hijri conversion leaves the Gregorian date alone on the line.
    """
    day = riyadh_today()
    rendered = _gregorian(day)
    try:
        rendered = f"{rendered} الموافق {_hijri(day)}"
    except Exception:  # noqa: BLE001 — a date must never cost the turn.
        logger.warning("today_line: Hijri conversion failed", exc_info=True)
    return _LINE.format(date=rendered)
