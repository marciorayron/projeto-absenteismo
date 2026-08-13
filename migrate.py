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

    db.session.commit()
