"""Lightweight SQLite schema migration helper.

``db.create_all()`` only creates missing tables — it does NOT add new columns
to tables that already exist. This helper inspects the current schema and runs
idempotent ``ALTER TABLE ... ADD COLUMN`` statements for any missing columns,
so existing deployments get the new fields without recreating the database.
"""
from sqlalchemy import inspect, text
from extensions import db


# table -> list of (column_name, SQL type)
_COLUMN_MIGRATIONS = {
    'employees': [
        ('vacation_start', 'DATE'),
        ('vacation_end', 'DATE'),
    ],
    'attendances': [
        ('justification_type', 'VARCHAR(20)'),
        ('notes', 'TEXT'),
    ],
    'shifts': [
        ('work_days', 'VARCHAR(50)'),
    ],
    'lines': [
        ('is_active', 'BOOLEAN'),
    ],
}


def ensure_schema():
    """Add any missing columns declared in _COLUMN_MIGRATIONS (idempotent)."""
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

    for table, columns in _COLUMN_MIGRATIONS.items():
        if table not in existing_tables:
            continue
        existing_columns = {col['name'] for col in inspector.get_columns(table)}
        for name, sql_type in columns:
            if name not in existing_columns:
                db.session.execute(
                    text(f'ALTER TABLE {table} ADD COLUMN {name} {sql_type}')
                )

    # Backfill: ensure every existing shift has a working-days configuration.
    # Use a fresh PRAGMA reflection (Inspector caches column lists, so it would
    # not see the column added above within the same run).
    if 'shifts' in existing_tables:
        col_rows = db.session.execute(text('PRAGMA table_info(shifts)')).fetchall()
        shift_columns = {row[1] for row in col_rows}
        if 'work_days' in shift_columns:
            db.session.execute(
                text("UPDATE shifts SET work_days = '0,1,2,3,4' "
                     "WHERE work_days IS NULL OR work_days = ''")
            )

    # Backfill: ensure every existing line is active by default.
    if 'lines' in existing_tables:
        col_rows = db.session.execute(text('PRAGMA table_info(lines)')).fetchall()
        line_columns = {row[1] for row in col_rows}
        if 'is_active' in line_columns:
            db.session.execute(
                text("UPDATE lines SET is_active = 1 WHERE is_active IS NULL")
            )

    db.session.commit()

    # Populate the `lines` table from distinct Allocation records.
    _populate_lines()


def _populate_lines():
    """Insert any distinct (line, project) combos from Allocation into Line."""
    from models.line import Line
    from models.allocation import Allocation

    existing = {(l.name, l.project) for l in Line.query.all()}
    rows = db.session.query(Allocation.line, Allocation.project).filter(
        Allocation.line.isnot(None),
        Allocation.line != ''
    ).distinct().all()
    for line_name, project in rows:
        key = (line_name, project or '')
        if key in existing:
            continue
        db.session.add(Line(name=line_name, project=project or ''))
        existing.add(key)
    db.session.commit()
