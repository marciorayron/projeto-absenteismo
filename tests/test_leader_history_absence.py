"""Tests: leader employee-history absence distribution & card counters map operational types."""
from datetime import date, timedelta

from extensions import db
from models.employee import Employee
from models.allocation import Allocation
from models.attendance import Attendance
from tests.conftest import login


def _seed_history(app, admin_user, line, shift, emp_id='HIST-1'):
    with app.app_context():
        emp = Employee(id=emp_id, name='Histórico', status='ACTIVE', is_active=True,
                       line_id=line.id, shift_id=shift.id)
        db.session.add(emp)
        db.session.flush()
        alloc = Allocation(employee_id=emp.id, shift=shift.id, project='Proj A',
                           line=line.name, start_date=date(2026, 1, 1))
        db.session.add(alloc)
        db.session.flush()
        base = date.today() - timedelta(days=30)
        rows = [
            # (offset_days, event_type, status, minutes_lost, is_justified)
            (0, 'FALTA', 'ABSENT', 488, False),
            (1, 'SUSPENSAO', 'ABSENT', 488, True),
            (2, 'ATESTADO', 'ABSENT', 488, True),
            (3, 'LATE_ARRIVAL', None, 30, False),
        ]
        for offset, event_type, status, minutes_lost, just in rows:
            db.session.add(Attendance(
                record_date=base + timedelta(days=offset),
                employee_id=emp.id,
                allocation_id=alloc.id,
                event_type=event_type,
                minutes_lost=minutes_lost,
                registered_by_id=admin_user.id,
                status=status,
                is_justified=just,
            ))
        db.session.commit()
        return emp.id


def test_history_maps_operational_absence_types(client, app, admin_user, leader_user,
                                                leader_line, shift1):
    emp_id = _seed_history(app, admin_user, leader_line, shift1)
    login(client, leader_user)

    resp = client.get(f'/leader/api/employee-history-data/{emp_id}')
    assert resp.status_code == 200
    summary = resp.get_json()['summary']

    # Card "Faltas / Atrasos / Saídas": Faltas = FALTA + SUSPENSAO + ATESTADO.
    assert summary['count_absence'] == 3
    assert summary['count_delay'] == 1
    assert summary['count_exit'] == 0

    # Chart "Distribuição de Tipos de Ausência" (4 fatias).
    assert summary['count_faltas_integrais'] == 2   # FALTA + SUSPENSAO
    assert summary['count_atestados'] == 1          # ATESTADO

    # minutes_lost permanece populado para todos os registros categorizados.
    assert summary['total_lost_minutes'] > 0
