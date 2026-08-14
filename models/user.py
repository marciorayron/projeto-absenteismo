from extensions import db, login_manager
from flask_login import UserMixin


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='LIDER')  # 'LIDER' or 'ADMIN'
    is_active = db.Column(db.Boolean, default=True)

    managed_scopes = db.relationship(
        'LeaderScope', backref='leader', cascade='all, delete-orphan', lazy=True
    )

    @property
    def managed_scope_list(self):
        """Return assigned scopes as dicts {shift, line_id, project, line} for templates/JSON."""
        return [
            {
                'shift': s.shift_id,
                'line_id': s.line_id,
                'project': s.line.project if s.line else '',
                'line': s.line.name if s.line else ''
            }
            for s in self.managed_scopes
        ]

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'role': self.role,
            'is_active': self.is_active,
            'managed_scope': [s.to_dict() for s in self.managed_scopes]
        }

    def __repr__(self):
        return f'<User {self.username} ({self.role})>'
