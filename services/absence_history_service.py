"""Service for importing historical absence records into the Attendance table.

The import is deliberately non-destructive:
- Existing employees are NEVER updated (line_id, project or active status untouched).
- Missing employees are auto-created as inactive (``is_active=False``).
- Absences are written directly to ``Attendance`` with ``status='ABSENT'``, the
  appropriate ``event_type`` / ``is_justified`` flags, and linked to the employee's
  active allocation for the target date.
"""
import logging
import unicodedata
from datetime import datetime

import pandas as pd

from extensions import db
from models.employee import Employee
from models.allocation import Allocation
from models.attendance import Attendance
from models.shift import Shift

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ('Data', 'Matricula', 'Tipo de Ausência')

# Canonical absence type -> (event_type, is_justified)
ABSENCE_TYPE_MAP = {
    'ATESTADO': ('ATESTADO', True),
    'FALTA': ('FALTA', False),
    'SUSPENSAO': ('SUSPENSAO', True),
    'SITUACAO_LEGAL': ('SITUACAO_LEGAL', True),
}

# Normalized (accent-insensitive, case-insensitive) spreadsheet label aliases.
ABSENCE_TYPE_ALIASES = {
    'ATESTADO': ('ATESTADO', 'ATEST'),
    'FALTA': ('FALTA', 'FALTA INJUSTIFICADA', 'INJUSTIFICADA', 'INJUSTIFICADO',
              'FALTA SEM JUSTIFICATIVA'),
    'SUSPENSAO': ('SUSPENSAO', 'SUSPENS'),
    'SITUACAO_LEGAL': ('SITUACAO LEGAL', 'SITUACAO', 'LEGAL'),
}


class AbsenceImportError(Exception):
    """Raised for fatal validation problems (bad extension, missing columns)."""

def _normalize(text):
    """Lowercase and strip accents/diacritics for robust matching."""
    if text is None:
        return ''
    text = str(text).strip()
    text = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in text if not unicodedata.combining(c)).upper()


def _map_absence_type(raw):
    """Map a spreadsheet label to a canonical absence type, or None if unknown."""
    key = _normalize(raw)
    if not key:
        return None
    for canonical, aliases in ABSENCE_TYPE_ALIASES.items():
        for alias in aliases:
            if alias in key:
                return canonical
    return None

DEFAULT_NET_WORK_MINUTES = 480


def _net_shift_minutes(alloc):
    """Return the net working minutes for an allocation's shift.

    Falls back to 480 minutes (8h) when the allocation is missing or its shift
    has no ``net_work_minutes`` defined, so imported absences never stay zeroed.
    """
    if alloc is None:
        return DEFAULT_NET_WORK_MINUTES
    try:
        shift_id = int(alloc.shift)
    except (TypeError, ValueError):
        return DEFAULT_NET_WORK_MINUTES
    shift_def = Shift.query.get(shift_id)
    if shift_def is not None and shift_def.net_work_minutes:
        return int(shift_def.net_work_minutes)
    return DEFAULT_NET_WORK_MINUTES


def _matricula_str(value):
    """Normalize a Matricula cell to a clean string id (handles Excel numerics)."""
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _parse_date_value(value):
    """Return a datetime.date for a cell value, or None if unparseable."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    # datetime / pandas Timestamp already carry a resolved date.
    if hasattr(value, 'date') and hasattr(value, 'year'):
        return value.date()
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Try explicit formats first (ISO and Brazilian day-first) to avoid the
        # ambiguity of pandas' dayfirst flag on 'YYYY-MM-DD' strings.
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d/%m/%y', '%d-%m-%y'):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        try:
            return pd.to_datetime(s, dayfirst=True).date()
        except (ValueError, TypeError):
            return None
    try:
        return pd.to_datetime(value, dayfirst=True).date()
    except (ValueError, TypeError, pd.errors.OutOfBoundsDatetime):
        return None


def _clean_optional(value):
    """Return a trimmed string or None for an optional cell value."""
    try:
        if value is None or pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _parse_turno(value):
    """Parse the optional Turno column to an int (1/2/3), or None if invalid."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _resolve_allocation(employee_id, absence_date, turno):
    """Return the best Allocation for an employee on a date.

    Prefers an allocation matching the imported turno, then the active allocation,
    then one covering the target date.
    """
    allocs = Allocation.query.filter(Allocation.employee_id == employee_id).order_by(
        Allocation.start_date.desc()).all()
    if not allocs:
        return None

    if turno is not None:
        for a in allocs:
            if a.shift == turno:
                return a
    for a in allocs:
        if a.end_date is None:
            return a
    for a in allocs:
        if a.start_date and a.end_date and a.start_date <= absence_date <= a.end_date:
            return a
    return allocs[0]


def process_absence_history_upload(file_stream, filename, registered_by_id=None):
    """Parse an absence-history spreadsheet and write rows into ``Attendance``.

    Args:
        file_stream: file-like object positioned at the start of the workbook.
        filename: original uploaded filename (used for validation and provenance).
        registered_by_id: optional User id recorded as the attendance author.

    Returns:
        dict summary with keys: processed_rows, attendances_created,
        attendances_updated, employees_created, skipped_rows, invalid_dates,
        unknown_types, errors.
    """
    summary = {
        'processed_rows': 0,
        'attendances_created': 0,
        'attendances_updated': 0,
        'employees_created': 0,
        'skipped_rows': 0,
        'invalid_dates': [],
        'unknown_types': [],
        'errors': [],
    }

    lower_name = (filename or '').lower()
    if not lower_name.endswith(('.xlsx', '.xls')):
        raise AbsenceImportError('Formato inválido. Envie um arquivo .xlsx ou .xls.')

    file_stream.seek(0)
    try:
        df = pd.read_excel(file_stream)
    except Exception as exc:  # pandas raises various reader exceptions
        logger.exception('Failed to read absence workbook %s', filename)
        raise AbsenceImportError(f'Não foi possível ler o arquivo Excel: {exc}') from exc

    if df is None or df.empty:
        raise AbsenceImportError('O arquivo não contém nenhuma linha de dados.')

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        raise AbsenceImportError(
            f'Colunas obrigatórias ausentes: {", ".join(missing_cols)}. '
            f'Esperadas: {", ".join(REQUIRED_COLUMNS)}.'
        )

    if registered_by_id is None:
        from models.user import User
        user = User.query.filter_by(role='ADMIN').first()
        registered_by_id = user.id if user else 1

    summary['processed_rows'] = int(len(df))
    created_employees = {}

    for idx, row in df.iterrows():
        matricula = _matricula_str(row.get('Matricula'))
        absence_date = _parse_date_value(row.get('Data'))
        canonical = _map_absence_type(row.get('Tipo de Ausência'))
        turno = _parse_turno(row.get('Turno'))

        if not matricula:
            summary['skipped_rows'] += 1
            summary['errors'].append(f'Linha {idx + 2}: matrícula vazia, ignorada.')
            continue

        if absence_date is None:
            summary['skipped_rows'] += 1
            summary['invalid_dates'].append(f'{matricula} ({row.get("Data")})')
            continue

        if canonical is None:
            summary['skipped_rows'] += 1
            summary['unknown_types'].append(f'{matricula} ({row.get("Tipo de Ausência")})')
            continue

        event_type, is_justified = ABSENCE_TYPE_MAP[canonical]

        # Auto-create missing employees as INACTIVE (never touch existing ones).
        employee = Employee.query.get(matricula)
        if employee is None and matricula not in created_employees:
            employee = Employee(id=matricula, name=matricula, status='INACTIVE', is_active=False)
            db.session.add(employee)
            created_employees[matricula] = employee
            summary['employees_created'] += 1

        alloc = _resolve_allocation(matricula, absence_date, turno)
        if alloc is None:
            summary['skipped_rows'] += 1
            summary['errors'].append(
                f'{matricula}: sem alocação para registrar a ausência em {absence_date}.')
            continue

        # Record the historical line/project/turno as notes (Attendance has no
        # historical line/shift columns of its own).
        line = _clean_optional(row.get('Linha'))
        project = _clean_optional(row.get('Projeto'))
        note_parts = []
        if line:
            note_parts.append(f'Linha: {line}')
        if project:
            note_parts.append(f'Projeto: {project}')
        if turno is not None:
            note_parts.append(f'Turno: {turno}')
        notes = ' | '.join(note_parts) or None

        justification_type = 'JUSTIFIED' if is_justified else 'UNJUSTIFIED'

        # Assign the employee's net shift minutes (8h fallback) so imported
        # absences are never left with minutes_lost = 0.
        minutes_lost = _net_shift_minutes(alloc)

        existing = Attendance.query.filter_by(
            employee_id=matricula, record_date=absence_date).first()
        if existing:
            existing.event_type = event_type
            existing.is_justified = is_justified
            existing.status = 'ABSENT'
            existing.allocation_id = alloc.id
            existing.justification_type = justification_type
            existing.notes = notes
            existing.minutes_lost = minutes_lost
            summary['attendances_updated'] += 1
        else:
            db.session.add(Attendance(
                record_date=absence_date,
                employee_id=matricula,
                allocation_id=alloc.id,
                event_type=event_type,
                minutes_lost=minutes_lost,
                registered_by_id=registered_by_id,
                justification_type=justification_type,
                is_justified=is_justified,
                status='ABSENT',
                notes=notes,
            ))
            summary['attendances_created'] += 1

    db.session.commit()

    logger.info(
        'Absence history import "%s": %d created, %d updated, %d new inactive employees.',
        filename, summary['attendances_created'], summary['attendances_updated'],
        summary['employees_created'],
    )
    return summary



def backfill_minutes_lost():
    """Recalculate ``minutes_lost`` for ABSENT records that are still zeroed.

    One-time utility targeting Attendance rows with ``status='ABSENT'`` whose
    ``minutes_lost`` is NULL or 0 (typical of records imported before the minutes
    calculation was introduced). Each row is updated to its allocation's net
    shift minutes (8h fallback). Returns the number of records updated.
    """
    from sqlalchemy import or_

    candidates = Attendance.query.filter(
        Attendance.status == 'ABSENT',
        or_(Attendance.minutes_lost.is_(None), Attendance.minutes_lost == 0)
    ).all()

    updated = 0
    for att in candidates:
        alloc = Allocation.query.get(att.allocation_id)
        net = _net_shift_minutes(alloc)
        if (att.minutes_lost or 0) != net:
            att.minutes_lost = net
            updated += 1

    if updated:
        db.session.commit()
        logger.info('Backfill minutes_lost: %d records updated.', updated)
    return updated

