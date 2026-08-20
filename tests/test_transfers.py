"""Integration tests for the multi-role transfer workflow (PUSH / PULL).

Covers the behaviour required by the "Leader Transfer Scope" fix across the
three roles that can drive transfers: LIDER, ADMIN and SUPERVISOR.
"""
from datetime import date

from extensions import db
from models.employee import Employee
from models.transfer import TransferRequest
from tests.conftest import login


def _get_after_request(app, request_id):
    """Return a fresh TransferRequest row inside an app context (avoid stale reads)."""
    with app.app_context():
        db.session.expire_all()
        return db.session.get(TransferRequest, request_id)


# 1. Leader PUSH (send an employee from their own scoped line)
def test_leader_create_push_transfer_success(client, app, leader_user, scoped_employee, target_line):
    login(client, leader_user)

    resp = client.post('/transfers/create', json={
        'employee_id': scoped_employee.id,
        'request_type': 'PUSH',
        'target_line_id': str(target_line.id),
        'target_shift_id': '',
        'notes': '',
    })

    assert resp.status_code in (200, 201)
    data = resp.get_json()
    assert data['success'] is True

    transfer = _get_after_request(app, data['request_id'])
    assert transfer.status == 'PENDING'
    assert transfer.request_type == 'PUSH'
    assert transfer.employee_id == scoped_employee.id
    assert transfer.target_line_id == target_line.id
    assert transfer.requester_id == leader_user.id


# 2. Leader PULL (request an employee from an external line into their scope)
def test_leader_create_pull_transfer_success(client, app, leader_user, external_employee, leader_line):
    login(client, leader_user)

    resp = client.post('/transfers/create', json={
        'employee_id': external_employee.id,
        'request_type': 'PULL',
        'target_line_id': str(leader_line.id),
        'target_shift_id': '',
        'notes': '',
    })

    assert resp.status_code in (200, 201)
    data = resp.get_json()
    assert data['success'] is True

    transfer = _get_after_request(app, data['request_id'])
    assert transfer.status == 'PENDING'
    assert transfer.request_type == 'PULL'
    assert transfer.employee_id == external_employee.id
    assert transfer.target_line_id == leader_line.id


# 3. Security guard: a requester can never approve their own request.
def test_leader_cannot_approve_own_request(client, app, leader_user, pending_transfer):
    login(client, leader_user)

    resp = client.post(f'/transfers/{pending_transfer.id}/respond',
                       data={'decision': 'APPROVE'})

    assert resp.status_code == 403
    transfer = _get_after_request(app, pending_transfer.id)
    assert transfer.status == 'PENDING'


# 4. The source leader can approve a PULL request addressed to them.
def test_source_leader_can_approve_pull(client, app, source_leader, pending_pull_transfer):
    login(client, source_leader)

    resp = client.post(f'/transfers/{pending_pull_transfer.id}/respond',
                       data={'decision': 'APPROVE'})

    assert resp.status_code in (200, 302)
    transfer = _get_after_request(app, pending_pull_transfer.id)
    assert transfer.status == 'APPROVED'


# 5. ADMIN bypasses the queue: transfer is applied immediately (AUTO-APPROVED).
def test_admin_direct_transfer_auto_approved(client, app, admin_user, any_employee, target_line):
    login(client, admin_user)

    resp = client.post('/transfers/create', json={
        'employee_id': any_employee.id,
        'request_type': 'PUSH',
        'target_line_id': str(target_line.id),
        'target_shift_id': '',
        'notes': '',
    })

    assert resp.status_code in (200, 201)
    data = resp.get_json()
    assert data['success'] is True
    assert data['auto_approved'] is True

    with app.app_context():
        transfer = db.session.get(TransferRequest, data['request_id'])
        assert transfer.status == 'APPROVED'

        # Employee + active Allocation updated immediately.
        employee = db.session.get(Employee, any_employee.id)
        assert employee.line_id == target_line.id
        active = employee.get_active_allocation()
        assert active is not None
        assert active.line == target_line.name
        assert active.project == target_line.project


# 6. SUPERVISOR also gets elevated (auto-approved) privileges, mirroring ADMIN.
def test_supervisor_direct_transfer_auto_approved(client, app, supervisor_user, any_employee, target_line):
    login(client, supervisor_user)

    resp = client.post('/transfers/create', json={
        'employee_id': any_employee.id,
        'request_type': 'PUSH',
        'target_line_id': str(target_line.id),
        'target_shift_id': '',
        'notes': '',
    })

    assert resp.status_code in (200, 201)
    data = resp.get_json()
    assert data['success'] is True
    assert data['auto_approved'] is True

    with app.app_context():
        transfer = db.session.get(TransferRequest, data['request_id'])
        assert transfer.status == 'APPROVED'
        employee = db.session.get(Employee, any_employee.id)
        assert employee.line_id == target_line.id


# 7. Backend scope endpoint returns only active employees of the leader's lines.
def test_leader_scope_endpoint_returns_scoped_employees(client, app, leader_user,
                                                        scoped_employee, external_employee):
    login(client, leader_user)

    resp = client.get('/leader/api/employees/scope')
    assert resp.status_code == 200
    payload = resp.get_json()

    ids = {e['employee_id'] for e in payload['employees']}
    assert scoped_employee.id in ids          # own scoped line employee present
    assert external_employee.id not in ids    # external-line employee excluded
    # The payload carries the line_id the UI needs to filter the destination dropdown.
    assert all('line_id' in e for e in payload['employees'])


# 8. Excel-imported operator (Employee.line_id NULL, line only in the active
#    Allocation) must be resolved into the leader's scope via the Allocation fallback
#    — otherwise they'd be invisible to the transfer modal and forced into "Receber".
def test_leader_scope_resolves_employee_via_allocation(client, app, leader_user,
                                                       leader_line, target_line, shift1):
    from models.allocation import Allocation

    with app.app_context():
        emp = Employee(id='EMP-EXCEL', name='Adeilton Excel', status='ACTIVE',
                       is_active=True, line_id=None, shift_id=shift1.id)
        db.session.add(emp)
        db.session.flush()
        db.session.add(Allocation(employee_id=emp.id, shift=shift1.id,
                                  project=leader_line.project, line=leader_line.name,
                                  start_date=date.today()))
        db.session.commit()

    login(client, leader_user)

    # (a) Appears in the scope API with the resolved line id.
    resp = client.get('/leader/api/employees/scope')
    assert resp.status_code == 200
    payload = resp.get_json()
    match = [e for e in payload['employees'] if e['employee_id'] == 'EMP-EXCEL']
    assert len(match) == 1
    assert match[0]['line_id'] == leader_line.id

    # (b) Leader can PUSH this operator (origin resolved via Allocation, target differs).
    resp2 = client.post('/transfers/create', json={
        'employee_id': 'EMP-EXCEL',
        'request_type': 'PUSH',
        'target_line_id': str(target_line.id),
        'target_shift_id': '',
        'notes': '',
    })
    assert resp2.status_code in (200, 201)
    data2 = resp2.get_json()
    assert data2['success'] is True

    transfer = _get_after_request(app, data2['request_id'])
    assert transfer.status == 'PENDING'
    assert transfer.origin_line_id == leader_line.id
    assert transfer.target_line_id == target_line.id

