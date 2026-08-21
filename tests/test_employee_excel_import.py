"""Tests: employee Excel import sets base line_id/shift_id on the Employee model.

The Employee record is the source of truth for base line/shift assignment, so
imported rows must update it directly (not only the Allocation).
"""
import os
import tempfile

import pandas as pd

from extensions import db
from models.employee import Employee
from models.shift import Shift
from services.excel_service import process_excel_upload
from tests.conftest import login

COLUMNS = ['ID', 'Nome', 'Turno', 'Projeto', 'Linha']


def _write_xlsx(path, rows):
    df = pd.DataFrame(rows, columns=COLUMNS)
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)


def _seed_line_shift(app):
    from models.line import Line
    with app.app_context():
        line = Line(name='Linha Teste', project='Proj T', is_active=True)
        db.session.add(line)
        db.session.flush()
        if not db.session.get(Shift, 1):
            db.session.add(Shift(id=1, name='Turno 1', start_time='05:00',
                                 end_time='14:48', break_minutes=60,
                                 net_work_minutes=488, is_overnight=False,
                                 is_active=True))
        db.session.commit()
        return line.id


def _upload(path, user_id):
    try:
        return process_excel_upload(path, user_id=user_id)
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_import_sets_line_and_shift_on_new_employee(app, admin_user):
    line_id = _seed_line_shift(app)
    fd, path = tempfile.mkstemp(suffix='.xlsx')
    os.close(fd)
    _write_xlsx(path, [{'ID': 'IMP-1', 'Nome': 'Novo Importado', 'Turno': 1,
                        'Projeto': 'Proj T', 'Linha': 'Linha Teste'}])

    with app.app_context():
        summary = _upload(path, admin_user.id)
        assert summary['employees_inserted'] == 1

        emp = Employee.query.get('IMP-1')
        assert emp is not None
        assert emp.line_id == line_id
        assert emp.shift_id == 1


def test_import_updates_line_and_shift_on_existing_employee(app, admin_user):
    # Pre-create an employee WITHOUT base line/shift (simulating old import).
    with app.app_context():
        db.session.add(Employee(id='IMP-2', name='Existente', status='ACTIVE',
                                is_active=True, line_id=None, shift_id=None))
        db.session.commit()

    line_id = _seed_line_shift(app)
    fd, path = tempfile.mkstemp(suffix='.xlsx')
    os.close(fd)
    _write_xlsx(path, [{'ID': 'IMP-2', 'Nome': 'Existente', 'Turno': 1,
                        'Projeto': 'Proj T', 'Linha': 'Linha Teste'}])

    with app.app_context():
        summary = _upload(path, admin_user.id)
        assert summary['employees_updated'] == 1

        emp = Employee.query.get('IMP-2')
        assert emp.line_id == line_id
        assert emp.shift_id == 1


def test_import_does_not_store_invalid_shift_zero(app, admin_user):
    # Shift 0 (invalid turn in the spreadsheet) must not be stored on the Employee.
    fd, path = tempfile.mkstemp(suffix='.xlsx')
    os.close(fd)
    _write_xlsx(path, [{'ID': 'IMP-3', 'Nome': 'Sem Turno', 'Turno': 0,
                        'Projeto': 'Proj T', 'Linha': 'Linha Teste'}])

    with app.app_context():
        summary = _upload(path, admin_user.id)
        assert summary['employees_inserted'] == 1

        emp = Employee.query.get('IMP-3')
        assert emp is not None
        assert emp.line_id is not None   # line resolved/auto-provisioned
        assert emp.shift_id is None      # invalid shift (0) not persisted


def test_admin_employees_page_renders_without_vacation_column(client, app, admin_user,
                                                              leader_line, shift1):
    # Seed an employee with a base line/shift so the table rows render them.
    with app.app_context():
        db.session.add(Employee(id='REND-1', name='Render Emp', status='ACTIVE',
                                is_active=True, line_id=leader_line.id, shift_id=shift1.id))
        db.session.commit()

    login(client, admin_user)
    resp = client.get('/admin/employees')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # The obsolete Férias column (header + date inputs + Salvar button) is gone.
    assert 'Férias</th>' not in html
    assert 'employee_vacation' not in html
    assert 'name="vacation_start"' not in html

    # Base assignment is displayed (no dashes) for the seeded employee.
    assert leader_line.name in html
    assert 'Turno 1' in html

