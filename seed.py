from extensions import db, bcrypt
from models.user import User
from models.shift import Shift
from models.line import Line
from services.metrics_service import calculate_shift_net_minutes

# Canonical list of official lines grouped by project. These are seeded into the
# `Line` table so that the Excel import can match (Project, Line) exactly.
OFFICIAL_LINES = [
    # CCP
    ("CCP", "Abandono"), ("CCP", "Ativa"), ("CCP", "CCP"),
    # Corte & LP
    ("Corte & LP", "Corte"), ("Corte & LP", "Lead Prep"),
    # GEM
    ("GEM", "Body"), ("GEM", "Body 1 4NB"), ("GEM", "Body 2 4NB"),
    ("GEM", "Body 3 5HB"), ("GEM", "Body 4 5HB"), ("GEM", "Body 6 5HB"),
    ("GEM", "Console"), ("GEM", "Engine 1"), ("GEM", "Engine 2"),
    ("GEM", "FRONT BUMPER"), ("GEM", "FRONT DOOR CO DRIVER"), ("GEM", "FRONT DOOR DRIVER"),
    ("GEM", "Fuel Injector"), ("GEM", "IP 1"), ("GEM", "IP 2"), ("GEM", "IP 3"),
    ("GEM", "IP 4"), ("GEM", "IP EXT"), ("GEM", "LIFT GATE"), ("GEM", "Rear Bumper"),
    ("GEM", "REAR DOOR CO DRIVER"), ("GEM", "Rear Door Driver"), ("GEM", "Rear Lamp"),
    ("GEM", "Roof"),
    # MASTER
    ("MASTER", "Arriere"), ("MASTER", "Avanti"), ("MASTER", "Moteur"),
    ("MASTER", "Multimedia"), ("MASTER", "Planche bord"), ("MASTER", "Sous Coisse"),
    # SPIN
    ("SPIN", "Body - SPIN"), ("SPIN", "Console - Spin"), ("SPIN", "Engine - SPIN"),
    ("SPIN", "Front Door Co Driver - SPIN"), ("SPIN", "Front Door Driver - SPIN"),
    ("SPIN", "Fuel Pump - SPIN"), ("SPIN", "IP - Spin"), ("SPIN", "License Plate - SPIN"),
    ("SPIN", "Lift Gate - Spin"), ("SPIN", "Lift Gate EXT - SPIN"), ("SPIN", "Object alarm - SPIN"),
    ("SPIN", "Rear Door Co Driver - Spin"), ("SPIN", "Rear Door Driver - Spin"),
    ("SPIN", "Seat Co Driver - Spin"), ("SPIN", "View mir"),
    # Suportes
    ("Suportes", "EHS"), ("Suportes", "Engenharia"), ("Suportes", "Geral"),
    ("Suportes", "Logística"), ("Suportes", "Protótipo"), ("Suportes", "Qualidade"),
    ("Suportes", "SCRAP"),
    # U11
    ("U11", "Main"), ("U11", "Small BMW"),
    # VS30
    ("VS30", "Canvas"), ("VS30", "Dach"), ("VS30", "Heck"), ("VS30", "IP VS30"),
    ("VS30", "Multicrimp VS30"), ("VS30", "Sitzkiste"), ("VS30", "Vorne"),
]


def seed_official_lines():
    """Insert the official canonical (project, line) pairs if not already present.

    Idempotent: existing lines are never duplicated or modified.
    """
    existing = {(l.project, l.name) for l in Line.query.all()}
    added = 0
    for project, name in OFFICIAL_LINES:
        if (project, name) in existing:
            continue
        db.session.add(Line(name=name, project=project, is_active=True))
        existing.add((project, name))
        added += 1
    if added:
        db.session.commit()
        print(f'{added} linha(s) oficial(is) criada(s).')


def create_initial_users(app):
    """Create default admin and leader users if they don't exist."""
    with app.app_context():
        # Check if admin user exists
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            admin = User(
                username='admin',
                password_hash=bcrypt.generate_password_hash('admin123').decode('utf-8'),
                role='ADMIN',
                is_active=True
            )
            db.session.add(admin)
            print('Usuário admin criado (senha: admin123)')

        # Check if leader user exists
        leader = User.query.filter_by(username='lider').first()
        if not leader:
            leader = User(
                username='lider',
                password_hash=bcrypt.generate_password_hash('lider123').decode('utf-8'),
                role='LIDER',
                is_active=True
            )
            db.session.add(leader)
            print('Usuário lider criado (senha: lider123)')

        # Seed default shifts if table is empty
        if Shift.query.count() == 0:
            default_shifts = [
                (1, '1º Turno (Diurno)', '05:00', '14:48', 60),
                (2, '2º Turno (Noturno)', '14:48', '00:16', 60),
                (3, '3º Turno (Madrugada)', '00:16', '05:00', 30),
                (4, '4º Turno (Administrativo)', '08:00', '17:00', 60),
            ]
            for sid, name, start, end, break_min in default_shifts:
                net, overnight = calculate_shift_net_minutes(start, end, break_min)
                shift = Shift(
                    id=sid,
                    name=name,
                    start_time=start,
                    end_time=end,
                    break_minutes=break_min,
                    net_work_minutes=net,
                    is_overnight=overnight,
                    is_active=True
                )
                db.session.add(shift)
            print('Turnos padrão criados (IDs 1-4).')

        db.session.commit()
        print('Usuários iniciais configurados com sucesso!')

        # Ensure the official line catalog is present.
        seed_official_lines()
