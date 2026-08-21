"""Tests: reports & dashboard read strictly from Attendance (incl. new absence types)."""
from datetime import date

from extensions import db
from models.employee import Employee
from models.allocation import Allocation
from models.attendance import Attendance
from services.metrics_service import calculate_bradford_bulk
from tests.conftest import login


def _seed_attendance(app, admin_user, leader_line, shift1, emp_id='RPT-EMP1'):
    with app.app_context():
        emp = Employee(id=emp_id, name='Ana Ativa', status='ACTIVE', is_active=True,
                       line_id=leader_line.id, shift_id=shift1.id)
        db.session.add(emp)
        db.session.flush()
        alloc = Allocation(employee_id=emp.id, shift=shift1.id, project='Proj A',
                           line=leader_line.name, start_date=date(2026, 1, 1))
        db.session.add(alloc)
        db.session.flush()
        db.session.add(Attendance(record_date=date(2026, 7, 2), employee_id=emp.id,
                                  allocation_id=alloc.id, event_type='FULL_ABSENCE',
                                  minutes_lost=488, registered_by_id=admin_user.id,
                                  status='ABSENT', is_justified=False))
        db.session.add(Attendance(record_date=date(2026, 8, 19), employee_id=emp.id,
                                  allocation_id=alloc.id, event_type='ATESTADO',
                                  minutes_lost=488, registered_by_id=admin_user.id,
                                  status='ABSENT', is_justified=True))
        db.session.commit()
        return emp.id


# 1. Reports show valid shifts and the new absence-type labels (never "None").
def test_reports_shows_shift_and_absence_types(client, app, admin_user, leader_user,
                                               leader_line, shift1):
    _seed_attendance(app, admin_user, leader_line, shift1)
    login(client, leader_user)

    resp = client.get('/reports/?date_from=2026-07-01&date_to=2026-08-31&per_page=50')
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'Ana Ativa' in html
    assert 'Turno 1' in html
    assert 'Atestado' in html      # ATESTADO label
    assert '>None<' not in html


# 2. Day-of-week maps Wednesday (19/08/2026) to Quarta (index 3), not Terça.
def test_day_of_week_wednesday_is_quarta(client, app, admin_user, leader_user,
                                         leader_line, shift1):
    _seed_attendance(app, admin_user, leader_line, shift1)
    login(client, leader_user)

    resp = client.get('/dashboard/api/by-day-of-week?date_from=2026-08-01&date_to=2026-08-31')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['days'][3] == 'Quarta'
    assert data['counts'][3] == 1   # the ATESTADO on 19/08 (Wednesday)
    assert data['counts'][2] == 0   # NOT Terça


# 3. Overview + Bradford count Attendance rows.
def test_overview_and_bradford(client, app, admin_user, leader_user, leader_line, shift1):
    _seed_attendance(app, admin_user, leader_line, shift1)
    login(client, leader_user)

    resp = client.get('/dashboard/api/overview?date_from=2026-07-01&date_to=2026-08-31')
    data = resp.get_json()
    assert data['absent_records'] == 2
    assert data['absent_employees'] == 1
    assert data['total_lost_minutes'] == 488 * 2

    start = date(2026, 7, 1)
    end = date(2026, 8, 31)
    with app.app_context():
        results = calculate_bradford_bulk(employee_ids=['RPT-EMP1'], start_date=start, end_date=end)
        # Any ABSENT record (status='ABSENT' or the imported absence types) counts:
        # FULL_ABSENCE (07/02) + ATESTADO (08/19) = 2 days, 2 spells -> B = 2^2 * 2 = 8.
        assert results['RPT-EMP1']['total_days'] == 2
        assert results['RPT-EMP1']['spells'] == 2
        assert results['RPT-EMP1']['bradford_score'] == 8


# 4. Excel export still works.
def test_reports_export_ok(client, app, admin_user, leader_user, leader_line, shift1):
    _seed_attendance(app, admin_user, leader_line, shift1)
    login(client, leader_user)

    resp = client.get('/reports/export-excel?date_from=2026-07-01&date_to=2026-08-31')
    assert resp.status_code == 200
    assert 'spreadsheetml' in resp.content_type
