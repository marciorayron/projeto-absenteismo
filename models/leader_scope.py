from extensions import db


class LeaderScope(db.Model):
    __tablename__ = 'leader_scopes'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    line_id = db.Column(db.Integer, db.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False)
    shift_id = db.Column(db.Integer, db.ForeignKey('shifts.id', ondelete='CASCADE'), nullable=False)

    # 'leader' backref is defined on User.managed_scopes
    line = db.relationship('Line', backref='leader_scopes')
    shift = db.relationship('Shift', backref='leader_scopes')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'line_id': self.line_id,
            'shift_id': self.shift_id,
            'line': self.line.name if self.line else None,
            'project': self.line.project if self.line else None,
            'shift': self.shift_id
        }

    def __repr__(self):
        return f'<LeaderScope user={self.user_id} line={self.line_id} shift={self.shift_id}>'
