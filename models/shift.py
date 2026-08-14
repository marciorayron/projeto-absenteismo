from extensions import db


class Shift(db.Model):
    __tablename__ = 'shifts'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    start_time = db.Column(db.String(5), nullable=False)  # "HH:MM"
    end_time = db.Column(db.String(5), nullable=False)
    break_minutes = db.Column(db.Integer, default=60)
    net_work_minutes = db.Column(db.Integer, nullable=False)
    is_overnight = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)
    # Comma-separated active weekdays: 0=Monday .. 6=Sunday. "0,1,2,3,4" = Mon-Fri.
    work_days = db.Column(db.String(50), default="0,1,2,3,4")
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(),
                           onupdate=db.func.current_timestamp())

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'break_minutes': self.break_minutes,
            'net_work_minutes': self.net_work_minutes,
            'is_overnight': self.is_overnight,
            'is_active': self.is_active,
            'work_days': self.work_days,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def is_working_day(self, weekday_idx):
        """Return True if the given weekday index (0=Monday .. 6=Sunday) is a working day.

        A shift with no ``work_days`` configured is treated as working every day
        (backward compatible with records created before this feature).
        """
        if not self.work_days:
            return True
        days = [d.strip() for d in self.work_days.split(',') if d.strip() != '']
        return str(weekday_idx) in days

    @property
    def net_hours_str(self):
        """Return net working time as 'XhXXm' string."""
        h = self.net_work_minutes // 60
        m = self.net_work_minutes % 60
        return f'{h}h{m:02d}'

    @property
    def schedule_str(self):
        """Return 'start - end' string."""
        return f'{self.start_time} - {self.end_time}'

    def __repr__(self):
        return f'<Shift {self.id} - {self.name}>'