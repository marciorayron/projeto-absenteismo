"""Shared pytest fixtures for the transfer-workflow integration tests.

A dedicated, temporary file-based SQLite database is used (WAL mode + separate
connections make an in-memory DB unreliable here). The ``app`` fixture rebuilds
the schema for every test so each scenario starts from a clean state.
"""
import os
import tempfile

import pytest

from extensions import db, bcrypt
from models.user import User
from models.line import Line
from models.shift import Shift
from models.leader_scope import LeaderScope
from models.employee import Employee
from models.transfer import TransferRequest

_TEST_DB = os.path.join(tempfile.gettempdir(), 'absenteismo_test.db')

TEST_PASSWORD = 'test123'


def _set_test_db():
    os.environ['DATABASE_URL'] = 'sqlite:///' + _TEST_DB.replace('\\', '/')


@pytest.fixture()
def app():
    """Build the Flask app against an isolated test database.

    The application context is held open for the whole test so ORM fixtures
    (created inside this context) stay bound to the session and their attributes
    can be read lazily without DetachedInstanceError.
    """
    _set_test_db()
    from app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    # These may already be disabled (no CSRF extension is loaded) but keep it
    # explicit so the test environment is predictable.
    app.config['WTF_CSRF_ENABLED'] = False

    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
    # Exiting the context tears down the scoped session automatically.


@pytest.fixture()
def client(app):
    """HTTP test client bound to the isolated app."""
    return app.test_client()

# ─────────────────────────── Builders ───────────────────────────
# Each builder assumes an active application context (fixtures open one).

def _line(name, project):
    line = Line(name=name, project=project, is_active=True)
    db.session.add(line)
    db.session.commit()
    return line


def _shift(sid, name='1º Turno', start='05:00', end='14:48', net=488):
    shift = Shift(
        id=sid,
        name=name,
        start_time=start,
        end_time=end,
        break_minutes=60,
        net_work_minutes=net,
        is_overnight=False,
        is_active=True,
        work_days='0,1,2,3,4',
    )
    db.session.add(shift)
    db.session.commit()
    return shift


def _user(username, role, password=TEST_PASSWORD):
    user = User(
        username=username,
        role=role,
        is_active=True,
        password_hash=bcrypt.generate_password_hash(password).decode('utf-8'),
    )
    db.session.add(user)
    db.session.commit()
    return user


def _scope(user, line, shift):
    scope = LeaderScope(user_id=user.id, line_id=line.id, shift_id=shift.id)
    db.session.add(scope)
    db.session.commit()
    return scope


def _employee(emp_id, name, line, shift):
    emp = Employee(
        id=emp_id,
        name=name,
        status='ACTIVE',
        is_active=True,
        line_id=line.id,
        shift_id=shift.id,
    )
    db.session.add(emp)
    db.session.commit()
    return emp


def _transfer(employee, requester, target_line, target_leader=None,
              request_type='PUSH', status='PENDING'):
    req = TransferRequest(
        employee_id=employee.id,
        requester_id=requester.id,
        target_leader_id=target_leader.id if target_leader else None,
        origin_line_id=employee.line_id,
        origin_shift_id=employee.shift_id,
        target_line_id=target_line.id,
        target_shift_id=employee.shift_id,
        request_type=request_type,
        status=status,
    )
    db.session.add(req)
    db.session.commit()
    return req


# ─────────────────────────── Domain fixtures ───────────────────────────
# All of these run while the ``app`` fixture keeps the app context open, so the
# builder helpers can use ``db.session`` directly and the returned ORM instances
# remain bound to the session throughout the test.

@pytest.fixture()
def shift1(app):
    return _shift(1)


@pytest.fixture()
def leader_line(app):
    return _line('Linha Líder', 'Proj A')


@pytest.fixture()
def target_line(app):
    return _line('Linha Destino', 'Proj B')


@pytest.fixture()
def source_line(app):
    return _line('Linha Origem', 'Proj C')


# ─────────────────────────── User fixtures ───────────────────────────

@pytest.fixture()
def admin_user(app):
    return _user('admin_t', 'ADMIN')


@pytest.fixture()
def supervisor_user(app):
    return _user('supervisor_t', 'SUPERVISOR')


@pytest.fixture()
def leader_user(app, leader_line, shift1):
    user = _user('lider_t', 'LIDER')
    _scope(user, leader_line, shift1)
    return user


@pytest.fixture()
def source_leader(app, source_line, shift1):
    user = _user('lider_origem', 'LIDER')
    _scope(user, source_line, shift1)
    return user


# ─────────────────────────── Employee fixtures ───────────────────────────

@pytest.fixture()
def scoped_employee(app, leader_line, shift1):
    return _employee('EMP-SCOPE', 'Maria Escopo', leader_line, shift1)


@pytest.fixture()
def external_employee(app, source_line, shift1):
    return _employee('EMP-EXT', 'João Externo', source_line, shift1)


@pytest.fixture()
def any_employee(app, leader_line, shift1):
    return _employee('EMP-ANY', 'Ana Qualquer', leader_line, shift1)


# ─────────────────────────── Transfer fixtures ───────────────────────────

@pytest.fixture()
def pending_transfer(app, leader_user, scoped_employee, target_line):
    """A PENDING PUSH request created by the leader (for self-approval test)."""
    return _transfer(scoped_employee, leader_user, target_line,
                     request_type='PUSH', status='PENDING')


@pytest.fixture()
def pending_pull_transfer(app, leader_user, source_leader, external_employee, leader_line):
    """A PENDING PULL request that the source leader must approve."""
    return _transfer(external_employee, leader_user, leader_line,
                     target_leader=source_leader,
                     request_type='PULL', status='PENDING')


# ─────────────────────────── Helpers ───────────────────────────

def login(client, user, password=TEST_PASSWORD):
    """Authenticate the given user via the real login endpoint."""
    return client.post('/login', data={'username': user.username, 'password': password})

