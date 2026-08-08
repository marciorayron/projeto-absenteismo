from extensions import db, bcrypt
from models.user import User
from models.shift import Shift
from services.metrics_service import calculate_shift_net_minutes


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
