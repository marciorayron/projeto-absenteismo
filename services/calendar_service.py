"""Pre-populate the CompanyCalendar with official national, state and municipal holidays.

National and Santa Catarina state holidays are fetched from the ``holidays``
library (``holidays.BR(state='SC', ...)``). Navegantes/SC municipal holidays are
defined manually (they are not part of the ``holidays`` package). Corpus Christi
is a "mobile" date computed from Easter (60 days after Easter Sunday).

The routine is idempotent: dates already present in ``CompanyCalendar`` are never
duplicated and never overwritten, so admins can freely manage operational
exceptions (bridge days, Saturday swaps, etc.).
"""

from datetime import date, timedelta

from extensions import db
from models.calendar import CompanyCalendar

# Navegantes/SC municipal holidays (month, day, description).
# These are local observances not covered by the `holidays` package.
NAVEGANTES_MUNICIPAL_HOLIDAYS = [
    (2, 2, 'Nossa Senhora dos Navegantes (Padroeira de Navegantes)'),
    (8, 26, 'Emancipação Político-Administrativa de Navegantes'),
]

# Default window: from 2024 forward. The current date (2026) falls inside it.
DEFAULT_START_YEAR = 2024
DEFAULT_YEARS_AHEAD = 3


def _easter(year):
    """Return the date of Easter Sunday for ``year`` (Anonymous Gregorian algorithm)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _collect_official_holidays(start_year, years_ahead):
    """Return ``{date: name}`` for national, state and municipal official holidays."""
    year_range = range(start_year, start_year + years_ahead)
    result = {}

    try:
        import holidays  # local import keeps the dependency optional at import time
        for d, name in holidays.BR(state='SC', years=year_range).items():
            result[d] = name
    except Exception:
        # Newer versions of the library renamed the top-level API.
        try:
            import holidays
            for d, name in holidays.country_holidays('BR', subdiv='SC', years=year_range).items():
                result[d] = name
        except Exception:
            pass

    # Municipal holidays for Navegantes/SC (defined manually).
    for month, day, desc in NAVEGANTES_MUNICIPAL_HOLIDAYS:
        for y in year_range:
            result[date(y, month, day)] = desc

    # Corpus Christi is a "mobile" (movable) date — 60 days after Easter Sunday.
    for y in year_range:
        result[_easter(y) + timedelta(days=60)] = 'Corpus Christi'

    return result


def seed_official_holidays(start_year=DEFAULT_START_YEAR, years_ahead=DEFAULT_YEARS_AHEAD):
    """Populate the CompanyCalendar table with official holidays (idempotent).

    Args:
        start_year (int): first year to populate (default 2024).
        years_ahead (int): number of years to populate from ``start_year`` (default 3).

    Returns:
        int: number of new holiday rows inserted.
    """
    official = _collect_official_holidays(start_year, years_ahead)
    if not official:
        return 0

    min_d = date(start_year, 1, 1)
    max_d = date(start_year + years_ahead - 1, 12, 31)
    existing = {
        e.date for e in CompanyCalendar.query.filter(
            CompanyCalendar.date >= min_d,
            CompanyCalendar.date <= max_d
        ).all()
    }

    added = 0
    for d, name in sorted(official.items()):
        if d in existing:
            continue
        db.session.add(CompanyCalendar(date=d, type='FERIADO', description=name))
        added += 1

    if added:
        db.session.commit()
    return added
