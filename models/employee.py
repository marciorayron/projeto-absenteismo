from extensions import db

class Employee(db.Model):
    __tablename__ = 'employees'

    id = db.Column(db.String(50), primary_key=True)  # Preserves original ID like "2020880"
    name = db.Column(db.String(150), nullable=False)
    status = db.Column(db.String(20), default='ACTIVE')
    vacation_start = db.Column(db.Date, nullable=True)
    vacation_end = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    allocations = db.relationship('Allocation', backref='employee', lazy=True,
                                  order_by='Allocation.start_date.desc()')
    attendances = db.relationship('Attendance', backref='employee', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'status': self.status,
            'vacation_start': self.vacation_start.isoformat() if self.vacation_start else None,
            'vacation_end': self.vacation_end.isoformat() if self.vacation_end else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    def get_active_allocation(self):
        """Returns the active allocation (no end_date) or the most recent one."""
        from models.allocation import Allocation
        active = Allocation.query.filter_by(employee_id=self.id, end_date=None).first()
        if active:
            return active
        return Allocation.query.filter_by(employee_id=self.id).order_by(Allocation.start_date.desc()).first()

    def __repr__(self):
        return f'<Employee {self.id} - {self.name}>'