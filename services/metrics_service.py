from datetime import time as dt_time, datetime, timedelta, date as dt_date
from extensions import db
from models.attendance import Attendance

# ── Operational absence categories ──────────────────────────────
# Full absences (integral) + imported operational absence types.
ABSENCE_CATEGORY_FULL = ('FULL_ABSENCE', 'FALTA', 'SUSPENSAO', 'SITUACAO_LEGAL')
# Medical / certificates.
ABSENCE_CATEGORY_MEDICAL = ('ATESTADO', 'LICENCA')
# Late arrival (Atraso).
ABSENCE_CATEGORY_LATE = ('LATE_ARRIVAL', 'ATRASO')
# Early leave (Saída Antecipada). EARLY_EXIT kept for backward compatibility.
ABSENCE_CATEGORY_EARLY = ('EARLY_EXIT', 'EARLY_DEPARTURE', 'SAIDA_ANTECIPADA')
# Records that count toward the "Faltas" card counter (full + medical).
ABSENCE_CATEGORY_ABSENT = ABSENCE_CATEGORY_FULL + ABSENCE_CATEGORY_MEDICAL


def absence_type_category(event_type):
    """Map an Attendance ``event_type`` to its operational category.

    Returns one of 'full', 'medical', 'late', 'early', or None for unknown
    (e.g. 'PRESENT', 'VACATION') event types.
    """
    if event_type in ABSENCE_CATEGORY_FULL:
        return 'full'
    if event_type in ABSENCE_CATEGORY_MEDICAL:
        return 'medical'
    if event_type in ABSENCE_CATEGORY_LATE:
        return 'late'
    if event_type in ABSENCE_CATEGORY_EARLY:
        return 'early'
    return None


def calculate_shift_net_minutes(start_str, end_str, break_minutes):
    """
    Calculate net working minutes and overnight status from HH:MM strings.

    Args:
        start_str (str): Start time as "HH:MM".
        end_str (str): End time as "HH:MM".
        break_minutes (int): Break time in minutes.

    Returns:
        tuple: (net_work_minutes, is_overnight)
    """
    start_parts = start_str.split(':')
    end_parts = end_str.split(':')
    start_min = int(start_parts[0]) * 60 + int(start_parts[1])
    end_min = int(end_parts[0]) * 60 + int(end_parts[1])

    is_overnight = end_min <= start_min
    if is_overnight:
        end_min += 24 * 60  # e.g. 00:16 -> 1456 (24*60 + 16)

    net = (end_min - start_min) - break_minutes
    return max(0, net), is_overnight


def time_to_minutes(t):
    """Convert a time object to minutes since midnight."""
    if t is None:
        return None
    return t.hour * 60 + t.minute


def minutes_to_time(minutes):
    """Convert minutes since midnight to a time object (handles overflow to next day)."""
    if minutes is None:
        return None
    minutes = minutes % (24 * 60)
    return dt_time(hour=minutes // 60, minute=minutes % 60)


def calculate_lost_minutes(shift_id, event_type, check_in_time, check_out_time, config):
    """
    Core business logic for calculating lost minutes.

    Pulls shift definitions from the database (Shift model).
    Falls back to default Turno 1 if the shift is not found.

    Args:
        shift_id (int): Shift ID (1, 2, 3, 4, ...).
        event_type (str): 'PRESENT', 'FULL_ABSENCE', 'VACATION', 'LATE_ARRIVAL', 'EARLY_EXIT'.
        check_in_time (datetime.time): Time of check-in.
        check_out_time (datetime.time): Time of check-out.
        config: Flask app config object (for tolerance/threshold values).

    Returns:
        tuple: (event_type_effective, minutes_lost) where event_type_effective may be
               reclassified (e.g., EARLY_EXIT -> FULL_ABSENCE).
    """
    from models.shift import Shift

    try:
        s_id = int(shift_id)
    except (ValueError, TypeError):
        s_id = 1

    db_shift = Shift.query.get(s_id)

    if db_shift:
        net_work_minutes = db_shift.net_work_minutes
        shift_start = datetime.strptime(db_shift.start_time, '%H:%M').time()
        shift_end = datetime.strptime(db_shift.end_time, '%H:%M').time()
        is_overnight = db_shift.is_overnight
    else:
        # Default fallback — Turno 1 (diurno padrão)
        net_work_minutes = 488
        shift_start = datetime.strptime('05:00', '%H:%M').time()
        shift_end = datetime.strptime('14:48', '%H:%M').time()
        is_overnight = False

    shift_start_min = time_to_minutes(shift_start)
    shift_end_min = time_to_minutes(shift_end)

    if is_overnight:
        shift_end_min += 24 * 60  # e.g. 00:16 -> 1480

    # Extract tolerance and threshold from config
    tolerance = config.get('TOLERANCE_MINUTES', 5) if isinstance(config, dict) else getattr(config, 'TOLERANCE_MINUTES', 5)
    early_exit_threshold = config.get('EARLY_EXIT_THRESHOLD_MINUTES', 60) if isinstance(config, dict) else getattr(config, 'EARLY_EXIT_THRESHOLD_MINUTES', 60)

    effective_type = event_type

    if event_type == 'PRESENT':
        return effective_type, 0

    if event_type == 'VACATION':
        return effective_type, 0

    if event_type == 'FULL_ABSENCE':
        return effective_type, net_work_minutes

    if event_type == 'LATE_ARRIVAL':
        if not check_in_time:
            # No check-in time -> treat as full absence
            return 'FULL_ABSENCE', net_work_minutes

        check_in_min = time_to_minutes(check_in_time)
        if is_overnight:
            # For overnight shift, if check-in is after midnight, add 24h
            if check_in_min < shift_start_min:
                check_in_min += 24 * 60

        delay_minutes = check_in_min - shift_start_min

        if delay_minutes <= tolerance:
            # Within tolerance -> no lost minutes, effectively present
            return 'PRESENT', 0

        # Lost minutes = delay beyond tolerance
        lost = max(0, delay_minutes - tolerance)
        return effective_type, lost

    if event_type == 'EARLY_EXIT':
        if not check_in_time or not check_out_time:
            # Missing times -> treat as full absence
            return 'FULL_ABSENCE', net_work_minutes

        check_in_min = time_to_minutes(check_in_time)
        check_out_min = time_to_minutes(check_out_time)

        if is_overnight:
            if check_in_min < shift_start_min:
                check_in_min += 24 * 60
            if check_out_min < shift_start_min:
                check_out_min += 24 * 60

        # Time worked before exit
        minutes_worked = check_out_min - check_in_min

        # Rule: if employee exits <= 60 minutes after shift start, reclassify as FULL_ABSENCE
        minutes_since_shift_start = check_out_min - shift_start_min
        if minutes_since_shift_start <= early_exit_threshold:
            return 'FULL_ABSENCE', net_work_minutes

        # Otherwise, remaining shift minutes are lost
        minutes_remaining = net_work_minutes - (check_out_min - shift_start_min)
        lost = max(0, minutes_remaining)
        return effective_type, lost

    # Unknown event type
    return event_type, 0


def calculate_bradford_factor(employee_id, start_date=None, end_date=None):
    """
    Calculate the Bradford Factor for an employee using SQL window functions.

    B = S² × D

    - S = number of distinct absence spells (consecutive absence days = 1 spell).
    - D = total days of absence (status='ABSENT' or an imported absence type).

    Delegates to calculate_bradford_bulk() for a single employee.
    """
    results = calculate_bradford_bulk(start_date=start_date, end_date=end_date, employee_ids=[employee_id])
    if employee_id in results:
        return results[employee_id]
    return _empty_bradford_result(start_date, end_date)


def calculate_bradford_bulk(employee_ids=None, start_date=None, end_date=None):
    """
    Calculate Bradford Factor for multiple employees in a single SQL round-trip.

    Uses SQLite window functions (LAG) to detect consecutive absence spells
    directly in the database, avoiding Python-side iteration.

    An absence day is any Attendance row with ``status='ABSENT'`` or an imported
    absence type (``FALTA``, ``ATESTADO``, ``SUSPENSAO``, ``SITUACAO_LEGAL``).

    Args:
        employee_ids (list[str], optional): Specific employees. None = all active.
        start_date (date, optional): Start of period. Defaults to 365 days ago.
        end_date (date, optional): End of period. Defaults to today.

    Returns:
        dict: {employee_id: {bradford_score, spells, total_days, risk_level, ...}}
    """
    if end_date is None:
        end_date = dt_date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=365)

    # --- Step 1: Get distinct absence dates per employee (SQL-side dedup) ---
    # Subquery to compute spell breaks using LAG window function
    # We compare current date with previous date per employee; if gap > 1 day, it's a new spell.

    # Use raw SQL via text() for the window function, since SQLAlchemy's ORM
    # doesn't naturally express LAG + julianday arithmetic cleanly across dialects.

    from sqlalchemy import text

    # Build employee filter clause
    emp_clause = ""
    emp_params = {}
    if employee_ids is not None:
        if not employee_ids:
            # Empty list = return empty results
            return {}
        placeholders = ", ".join([f":emp_{i}" for i in range(len(employee_ids))])
        emp_clause = f"AND employee_id IN ({placeholders})"
        for i, eid in enumerate(employee_ids):
            emp_params[f"emp_{i}"] = eid

    query = text(f"""
        SELECT
            employee_id,
            COUNT(*) AS total_days,
            SUM(is_new_spell) AS spells
        FROM (
            SELECT
                a.employee_id,
                a.record_date,
                CASE
                    WHEN LAG(a.record_date) OVER (
                        PARTITION BY a.employee_id ORDER BY a.record_date
                    ) IS NULL THEN 1
                    WHEN julianday(a.record_date) - julianday(
                        LAG(a.record_date) OVER (
                            PARTITION BY a.employee_id ORDER BY a.record_date
                        )
                    ) > 1 THEN 1
                    ELSE 0
                END AS is_new_spell
            FROM (
                SELECT DISTINCT employee_id, record_date
                FROM attendances
                WHERE (status = 'ABSENT'
                       OR event_type IN ('FALTA', 'ATESTADO', 'SUSPENSAO', 'SITUACAO_LEGAL'))
                  AND record_date >= :start_date
                  AND record_date <= :end_date
                  {emp_clause}
            ) a
        ) sub
        GROUP BY employee_id
    """)

    params = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        **emp_params
    }

    rows = db.session.execute(query, params).fetchall()

    results = {}
    period_days = (end_date - start_date).days

    for employee_id, total_days, spells in rows:
        score = (spells ** 2) * total_days

        if score < 50:
            risk_level = 'low'
        elif score < 200:
            risk_level = 'moderate'
        else:
            risk_level = 'high'

        results[employee_id] = {
            'bradford_score': score,
            'spells': spells,
            'total_days': total_days,
            'risk_level': risk_level,
            'period_days': period_days,
            'absence_dates': []  # Omitted in bulk mode for performance
        }

    # Fill in employees with zero absences (if specific IDs were requested)
    if employee_ids is not None:
        for eid in employee_ids:
            if eid not in results:
                results[eid] = _empty_bradford_result(start_date, end_date)

    return results


def _empty_bradford_result(start_date=None, end_date=None):
    """Return a zero-score Bradford result dict."""
    if end_date is None:
        end_date = dt_date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=365)
    return {
        'bradford_score': 0,
        'spells': 0,
        'total_days': 0,
        'risk_level': 'low',
        'period_days': (end_date - start_date).days,
        'absence_dates': []
    }