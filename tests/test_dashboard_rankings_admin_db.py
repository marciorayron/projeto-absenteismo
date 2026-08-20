"""Tests: inactive employees excluded from rankings + quick-action justification flags."""
from datetime import date, timedelta

from extensions import db
from models.employee import Employee
from models.allocation import Allocation
from models.attendance import Attendance
from tests.conftest import login


def _high_score_days():
    """10 absence days forming 5 spells -> B = 5^2 * 10 = 250 (high risk)."""
    base = date.today() - timedelta(days=20)
    return [base + timedelta(days=d) for d in [1, 2, 6, 7, 11, 12, 16, 17, 21, 22]]


def _seed_ranked_employee(emp_id, name, active, line, shift, admin_user, days):
    emp = Employee(id=emp_id, name=name,
                   status='ACTIVE' if active else 'INACTIVE', is_active=active)
    db.session.add(emp)
    db.session.flush()
    alloc = Allocation(employee_id=emp.id, shift=shift.id, project='P',
                       line=line.name, start_date=date(2026, 1, 1))
    db.session.add(alloc)
    db.session.flush()
    for d in days:
        db.session.add(Attendance(record_date=d, employee_id=emp.id, allocation_id=alloc.id,
                                  event_type='FULL_ABSENCE', minutes_lost=488,
                                  registered_by_id=admin_user.id, status='ABSENT',
                                  is_justified=False))


# 1. Inactive employees are excluded from top-absentees and bradford-top-risks.
def test_inactive_excluded_from_rankings(client, app, admin_user, leader_user,
                                         leader_line, shift1):
    with app.app_context():
        _seed_ranked_employee('RK-ACT', 'Ativo', True, leader_line, shift1, admin_user, _high_score_days())
        _seed_ranked_employee('RK-INACT', 'Inativo', False, leader_line, shift1, admin_user, _high_score_days())
        db.session.commit()

    login(client, leader_user)

    resp = client.get('/dashboard/api/top-absentees')
    ids = {e['employee_id'] for e in resp.get_json()['top_absentees']}
    assert 'RK-ACT' in ids
    assert 'RK-INACT' not in ids

    resp2 = client.get('/dashboard/api/bradford-top-risks')
    risk_ids = {e['employee_id'] for e in resp2.get_json()['risks']}
    assert 'RK-ACT' in risk_ids
    assert 'RK-INACT' not in risk_ids


# 2. The quick-action endpoint stores status/is_justified per the modal mapping.
def test_quick_action_sets_is_justified(client, app, admin_user, leader_line, shift1):
    with app.app_context():
        emp = Employee(id='QA-1', name='QA Emp', status='ACTIVE', is_active=True,
                       line_id=leader_line.id, shift_id=shift1.id)
        db.session.add(emp)
        db.session.flush()
        alloc = Allocation(employee_id=emp.id, shift=shift1.id, project='P',
                           line=leader_line.name, start_date=date(2026, 1, 1))
        db.session.add(alloc)
        db.session.commit()

    login(client, admin_user)

    # Injustificada -> FALTA, is_justified=False, status='ABSENT'
    resp = client.post('/leader/api/quick-action', json={
        'employee_id': 'QA-1', 'record_date': '2026-08-10',
        'event_type': 'FALTA', 'justification_type': 'UNJUSTIFIED',
        'is_justified': False,
    })
    assert resp.status_code == 200
    assert resp.get_json()['success'] is True

    # Justificada + Atestado -> ATESTADO, is_justified=True, status='ABSENT'
    resp2 = client.post('/leader/api/quick-action', json={
        'employee_id': 'QA-1', 'record_date': '2026-08-11',
        'event_type': 'ATESTADO', 'justification_type': 'JUSTIFIED',
        'is_justified': True,
    })
    assert resp2.status_code == 200

    with app.app_context():
        by_date = {a.record_date: a for a in Attendance.query.filter_by(employee_id='QA-1').all()}
        falta = by_date[date(2026, 8, 10)]
        assert falta.event_type == 'FALTA'
        assert falta.is_justified is False
        assert falta.status == 'ABSENT'
        assert falta.minutes_lost == shift1.net_work_minutes

        atestado = by_date[date(2026, 8, 11)]
        assert atestado.event_type == 'ATESTADO'
        assert atestado.is_justified is True
        assert atestado.status == 'ABSENT'
