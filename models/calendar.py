from extensions import db


class CompanyCalendar(db.Model):
    """Calendar exceptions that should not skew absenteeism metrics.

    ``type`` one of:
      - FERIADO           (holiday — ignores automated absence for the date)
      - FOLGA_COMPENSADA  (compensated bridge day off — ignores automated absence)
      - SABADO_LETIVO     (make-up working Saturday / swap day)
    """

    __tablename__ = 'company_calendar'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    date = db.Column(db.Date, unique=True, nullable=False, index=True)
    type = db.Column(db.String(30), nullable=False)  # FERIADO | FOLGA_COMPENSADA | SABADO_LETIVO
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    def to_dict(self):
        return {
            'id': self.id,
            'date': self.date.isoformat() if self.date else None,
            'type': self.type,
            'description': self.description,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    def __repr__(self):
        return f'<CompanyCalendar {self.date} - {self.type}>'
