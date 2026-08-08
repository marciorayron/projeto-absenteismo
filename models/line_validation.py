from extensions import db


class LineValidation(db.Model):
    __tablename__ = 'line_validations'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    record_date = db.Column(db.Date, nullable=False, index=True)
    line = db.Column(db.String(100), nullable=False)
    shift = db.Column(db.Integer, nullable=False)
    validated_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    validated_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    validated_by = db.relationship('User', backref='line_validations')

    __table_args__ = (
        db.UniqueConstraint('record_date', 'line', 'shift', name='uq_line_validation_date_line_shift'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'record_date': self.record_date.isoformat() if self.record_date else None,
            'line': self.line,
            'shift': self.shift,
            'validated_by_id': self.validated_by_id,
            'validated_at': self.validated_at.isoformat() if self.validated_at else None
        }

    def __repr__(self):
        return f'<LineValidation {self.record_date} L:{self.line} S:{self.shift}>'