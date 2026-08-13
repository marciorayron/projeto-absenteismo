from extensions import db

class Attendance(db.Model):
    __tablename__ = 'attendances'
    __table_args__ = (
        db.Index('idx_attendance_date_event', 'record_date', 'event_type'),
        db.Index('idx_attendance_emp_date', 'employee_id', 'record_date'),
        db.Index('idx_attendance_emp_date_event', 'employee_id', 'record_date', 'event_type'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    record_date = db.Column(db.Date, nullable=False, index=True)
    employee_id = db.Column(db.String(50), db.ForeignKey('employees.id'), nullable=False, index=True)
    allocation_id = db.Column(db.Integer, db.ForeignKey('allocations.id'), nullable=False)
    event_type = db.Column(db.String(30), nullable=False)  # PRESENT, FULL_ABSENCE, VACATION, LATE_ARRIVAL, EARLY_EXIT
    check_in_time = db.Column(db.Time, nullable=True)
    check_out_time = db.Column(db.Time, nullable=True)
    minutes_lost = db.Column(db.Integer, default=0)
    registered_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    justification_type = db.Column(db.String(20), nullable=True)  # 'JUSTIFIED' | 'UNJUSTIFIED'
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    registered_by = db.relationship('User', backref='registered_attendances')

    def to_dict(self):
        return {
            'id': self.id,
            'record_date': self.record_date.isoformat() if self.record_date else None,
            'employee_id': self.employee_id,
            'allocation_id': self.allocation_id,
            'event_type': self.event_type,
            'check_in_time': self.check_in_time.isoformat() if self.check_in_time else None,
            'check_out_time': self.check_out_time.isoformat() if self.check_out_time else None,
            'minutes_lost': self.minutes_lost,
            'registered_by_id': self.registered_by_id,
            'justification_type': self.justification_type,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    def __repr__(self):
        return f'<Attendance {self.employee_id} - {self.record_date} - {self.event_type}>'