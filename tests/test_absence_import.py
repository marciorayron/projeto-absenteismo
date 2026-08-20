"""Integration tests for the absence-history Excel import into Attendance."""
import io
from datetime import date

import pandas as pd

from extensions import db
from models.employee import Employee
from models.allocation import Allocation
from models.attendance import Attendance
from tests.conftest import login


def _xlsx(data_rows, columns):
    """Build an in-memory .xlsx bytes buffer from a list of row dicts."""
    df = pd.DataFrame(data_rows, columns=columns)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    buf.seek(0)
    return buf


COLUMNS = ['Data', 'Matricula', 'Tipo de Ausência', 'Turno', 'Linha', 'Projeto']


def _upload(client, buf, filename='historico.xlsx', ajax=True):
    headers = {'X-Requested-With': 'XMLHttpRequest'} if ajax else {}
    return client.post(
        '/admin/import/absence-history',
        data={'absence_history_file': (buf, filename)},
        content_type='multipart/form-data',
        headers=headers,
    )


def _seed_allocated_employee(emp_id, name, line, shift, project='Proj'):
    emp = Employee(id=emp_id, name=name, status='ACTIVE', is_active=True,
                   line_id=line.id, shift_id=shift.id)
    db.session.add(emp)
    db.session.flush()
    alloc = Allocation(employee_id=emp.id, shift=shift.id, project=project,
                       line=line.name, start_date=date(2024, 1, 1))
    db.session.add(alloc)
    db.session.flush()
    return emp

# 1. Existing employees untouched; missing ones created inactive; Attendance written.
def test_import_preserves_existing_and_creates_inactive(client, app, admin_user,
                                                        leader_line, shift1):
    with app.app_context():
        emp = _seed_allocated_employee('EMP-ACT', 'Ativo', leader_line, shift1, 'Proj A')
        original_line_id = emp.line_id
        original_active = emp.is_active
        db.session.commit()

    login(client, admin_user)

    rows = [
        {'Data': pd.Timestamp('2024-01-10'), 'Matricula': 'EMP-ACT',
         'Tipo de Ausência': 'Atestado', 'Turno': 1, 'Linha': 'Linha Líder', 'Projeto': 'Proj A'},
        {'Data': '15/02/2024', 'Matricula': 2020880,
         'Tipo de Ausência': 'Injustificado', 'Turno': 2, 'Linha': 'Linha X', 'Projeto': 'Proj Y'},
    ]
    resp = _upload(client, _xlsx(rows, COLUMNS))

    assert resp.status_code == 200
    data = resp.get_json()
    assert data['success'] is True
    assert data['attendances_created'] == 1   # EMP-ACT has an allocation
    assert data['employees_created'] == 1     # 2020880 auto-created inactive
    assert data['skipped_rows'] == 1          # 2020880 has no allocation

    with app.app_context():
        existing = db.session.get(Employee, 'EMP-ACT')
        assert existing.line_id == original_line_id == leader_line.id
        assert existing.is_active is original_active is True

        att = Attendance.query.filter_by(employee_id='EMP-ACT').one()
        assert att.event_type == 'ATESTADO'
        assert att.is_justified is True
        assert att.status == 'ABSENT'
        assert att.justification_type == 'JUSTIFIED'
        assert 'Turno: 1' in (att.notes or '')

        new_emp = db.session.get(Employee, '2020880')
        assert new_emp is not None
        assert new_emp.is_active is False
        assert new_emp.status == 'INACTIVE'


# 2. A missing required column is rejected with a 400 + clear message.
def test_missing_required_column_returns_400(client, admin_user):
    login(client, admin_user)
    rows = [{'Data': '01/01/2024', 'Matricula': 'X1', 'Tipo de Ausência': 'Falta'}]
    buf = _xlsx(rows, ['Data', 'Matricula', 'Tipo de Ausência'])
    buf.seek(0)
    df = pd.read_excel(buf)
    df = df.drop(columns=['Tipo de Ausência'])
    buf2 = io.BytesIO()
    with pd.ExcelWriter(buf2, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    buf2.seek(0)

    resp = _upload(client, buf2)
    assert resp.status_code == 400
    assert resp.get_json()['success'] is False


# 3. A non-Excel extension is rejected.
def test_wrong_extension_returns_400(client, admin_user):
    login(client, admin_user)
    rows = [{'Data': '01/01/2024', 'Matricula': 'X1', 'Tipo de Ausência': 'Falta'}]
    resp = _upload(client, _xlsx(rows, COLUMNS), filename='dados.txt')
    assert resp.status_code == 400
    assert resp.get_json()['success'] is False


# 4. Absence types map to the correct event_type / is_justified / status.
def test_type_mapping_and_turno(client, app, admin_user, leader_line, shift1):
    with app.app_context():
        _seed_allocated_employee('E1', 'E1', leader_line, shift1, 'Proj A')
        _seed_allocated_employee('E2', 'E2', leader_line, shift1, 'Proj A')
        _seed_allocated_employee('E3', 'E3', leader_line, shift1, 'Proj A')
        _seed_allocated_employee('E4', 'E4', leader_line, shift1, 'Proj A')
        db.session.commit()

    login(client, admin_user)
    rows = [
        {'Data': '05/03/2024', 'Matricula': 'E1', 'Tipo de Ausência': 'Situação Legal', 'Turno': 3},
        {'Data': '06/03/2024', 'Matricula': 'E2', 'Tipo de Ausência': 'Suspensão', 'Turno': 2},
        {'Data': '07/03/2024', 'Matricula': 'E3', 'Tipo de Ausência': 'Atestado', 'Turno': 1},
        {'Data': '08/03/2024', 'Matricula': 'E4', 'Tipo de Ausência': 'Injustificado', 'Turno': 1},
    ]
    resp = _upload(client, _xlsx(rows, COLUMNS))
    assert resp.status_code == 200
    assert resp.get_json()['attendances_created'] == 4

    with app.app_context():
        by_emp = {a.employee_id: a for a in Attendance.query.all()}
        assert by_emp['E1'].event_type == 'SITUACAO_LEGAL'
        assert by_emp['E1'].is_justified is True
        assert by_emp['E1'].status == 'ABSENT'
        assert 'Turno: 3' in (by_emp['E1'].notes or '')

        assert by_emp['E2'].event_type == 'SUSPENSAO'
        assert by_emp['E2'].is_justified is True
        assert by_emp['E2'].status == 'ABSENT'

        assert by_emp['E3'].event_type == 'ATESTADO'
        assert by_emp['E3'].is_justified is True
        assert by_emp['E3'].status == 'ABSENT'

        assert by_emp['E4'].event_type == 'FALTA'
        assert by_emp['E4'].is_justified is False
        assert by_emp['E4'].status == 'ABSENT'
        assert by_emp['E4'].justification_type == 'UNJUSTIFIED'


# 5. Invalid dates and unknown absence types are skipped and reported.
def test_invalid_rows_are_skipped(client, app, admin_user, leader_line, shift1):
    with app.app_context():
        _seed_allocated_employee('E3', 'E3', leader_line, shift1, 'Proj A')
        db.session.commit()

    login(client, admin_user)
    rows = [
        {'Data': 'nao-e-data', 'Matricula': 'E1', 'Tipo de Ausência': 'Falta'},
        {'Data': '10/01/2024', 'Matricula': 'E2', 'Tipo de Ausência': 'INVENTADO'},
        {'Data': '10/01/2024', 'Matricula': 'E3', 'Tipo de Ausência': 'Falta'},
    ]
    resp = _upload(client, _xlsx(rows, COLUMNS))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['attendances_created'] == 1
    assert data['skipped_rows'] == 2
    assert len(data['invalid_dates']) == 1
    assert len(data['unknown_types']) == 1

