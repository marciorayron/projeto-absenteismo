from extensions import db

class Allocation(db.Model):
    __tablename__ = 'allocations'
    __table_args__ = (
        db.Index('idx_allocation_active', 'shift', 'line', 'end_date'),
        db.Index('idx_allocation_emp_active', 'employee_id', 'end_date'),
        db.Index('idx_allocation_project_line', 'project', 'line', 'end_date'),
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    employee_id = db.Column(db.String(50), db.ForeignKey('employees.id'), nullable=False, index=True)
    shift = db.Column(db.Integer, nullable=False)
    project = db.Column(db.String(50), nullable=False)
    line = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=True)

    attendances = db.relationship('Attendance', backref='allocation', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'employee_id': self.employee_id,
            'shift': self.shift,
            'project': self.project,
            'line': self.line,
            'start_date': self.start_date.isoformat() if self.start_date else None,
            'end_date': self.end_date.isoformat() if self.end_date else None
        }

    def __repr__(self):
        return f'<Allocation {self.employee_id} - Shift {self.shift} - {self.line}>'