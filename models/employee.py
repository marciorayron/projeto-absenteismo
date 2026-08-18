from extensions import db
from datetime import date


class Employee(db.Model):
    __tablename__ = 'employees'

    id = db.Column(db.String(50), primary_key=True)  # Preserves original ID like "2020880"
    name = db.Column(db.String(150), nullable=False)
    status = db.Column(db.String(20), default='ACTIVE')  # kept in sync with is_active
    # Current line/shift/project assignment. The active Allocation is kept in sync
    # so the existing KPI/attendance queries (which read Allocation) stay consistent.
    line_id = db.Column(db.Integer, db.ForeignKey('lines.id'), nullable=True)
    shift_id = db.Column(db.Integer, db.ForeignKey('shifts.id'), nullable=True)
    project_id = db.Column(db.Integer, nullable=True)  # no Project table; project comes from Line.project
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    vacation_start = db.Column(db.Date, nullable=True)
    vacation_end = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    line = db.relationship('Line', backref='employees', lazy=True)
    shift = db.relationship('Shift', backref='employees', lazy=True)

    allocations = db.relationship('Allocation', backref='employee', lazy=True,
                                  order_by='Allocation.start_date.desc()')
    attendances = db.relationship('Attendance', backref='employee', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'status': self.status,
            'is_active': self.is_active,
            'line_id': self.line_id,
            'shift_id': self.shift_id,
            'project_id': self.project_id,
            'project': self.line.project if self.line else None,
            'line': self.line.name if self.line else None,
            'shift': self.shift_id,
            'vacation_start': self.vacation_start.isoformat() if self.vacation_start else None,
            'vacation_end': self.vacation_end.isoformat() if self.vacation_end else None,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

    @property
    def project_name(self):
        """Project string derived from the assigned Line (source of truth for project)."""
        return self.line.project if self.line else ''

    @property
    def line_name(self):
        return self.line.name if self.line else ''

    def get_active_allocation(self):
        """Returns the active allocation (no end_date) or the most recent one."""
        from models.allocation import Allocation
        active = Allocation.query.filter_by(employee_id=self.id, end_date=None).first()
        if active:
            return active
        return Allocation.query.filter_by(employee_id=self.id).order_by(Allocation.start_date.desc()).first()

    def sync_allocation(self, commit=True):
        """Create/update the active Allocation to reflect this employee's line/shift/project.

        The KPI, attendance, leader and report queries read line/shift/project from
        ``Allocation``, so the transfer/CRUD flows must keep the active allocation in
        sync with the ``Employee`` fields to avoid skewing metrics.
        """
        from models.allocation import Allocation
        if self.line is None or self.shift_id is None:
            return
        project = self.line.project or ''
        alloc = Allocation.query.filter_by(employee_id=self.id, end_date=None).first()
        if alloc:
            alloc.shift = self.shift_id
            alloc.project = project
            alloc.line = self.line.name
        else:
            alloc = Allocation(
                employee_id=self.id,
                shift=self.shift_id,
                project=project,
                line=self.line.name,
                start_date=date.today()
            )
            db.session.add(alloc)
        if commit:
            db.session.commit()

    def __repr__(self):
        return f'<Employee {self.id} - {self.name}>'
