from extensions import db
from datetime import datetime, timezone


def _utcnow():
    return datetime.now(timezone.utc)


class TransferRequest(db.Model):
    """A multi-leader transfer request for line/shift reassignment.

    ``request_type``:
      - PUSH: the requester leader is sending one of their employees to another line.
      - PULL: the requester leader is requesting an employee owned by another leader.
    ``status``: PENDING -> APPROVED | REJECTED | CANCELLED.
    """

    __tablename__ = 'transfer_requests'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    employee_id = db.Column(db.String(50), db.ForeignKey('employees.id'), nullable=False, index=True)
    requester_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    target_leader_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    origin_line_id = db.Column(db.Integer, db.ForeignKey('lines.id'), nullable=True)
    origin_shift_id = db.Column(db.Integer, db.ForeignKey('shifts.id'), nullable=True)
    target_line_id = db.Column(db.Integer, db.ForeignKey('lines.id'), nullable=False)
    target_shift_id = db.Column(db.Integer, db.ForeignKey('shifts.id'), nullable=True)
    request_type = db.Column(db.String(10), nullable=False)  # PUSH | PULL
    status = db.Column(db.String(12), nullable=False, default='PENDING')  # PENDING|APPROVED|REJECTED|CANCELLED
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=_utcnow)
    updated_at = db.Column(db.DateTime, default=_utcnow, onupdate=_utcnow)

    employee = db.relationship('Employee', backref='transfer_requests', lazy=True)
    requester = db.relationship('User', foreign_keys=[requester_id], backref='transfers_required', lazy=True)
    target_leader = db.relationship('User', foreign_keys=[target_leader_id], backref='transfers_targeted', lazy=True)
    origin_line = db.relationship('Line', foreign_keys=[origin_line_id], backref='transfer_origins', lazy=True)
    origin_shift = db.relationship('Shift', foreign_keys=[origin_shift_id], backref='transfer_origin_shifts', lazy=True)
    target_line = db.relationship('Line', foreign_keys=[target_line_id], backref='transfer_requests', lazy=True)
    target_shift = db.relationship('Shift', foreign_keys=[target_shift_id], backref='transfer_requests', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'employee_id': self.employee_id,
            'employee_name': self.employee.name if self.employee else None,
            'requester_id': self.requester_id,
            'requester_name': self.requester.username if self.requester else None,
            'target_leader_id': self.target_leader_id,
            'target_leader_name': self.target_leader.username if self.target_leader else None,
            'origin_line_id': self.origin_line_id,
            'origin_line': self.origin_line.name if self.origin_line else None,
            'origin_project': self.origin_line.project if self.origin_line else None,
            'origin_shift_id': self.origin_shift_id,
            'target_line_id': self.target_line_id,
            'target_line': self.target_line.name if self.target_line else None,
            'target_project': self.target_line.project if self.target_line else None,
            'target_shift_id': self.target_shift_id,
            'request_type': self.request_type,
            'status': self.status,
            'notes': self.notes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f'<TransferRequest {self.id} {self.request_type} {self.employee_id} -> {self.target_line_id} [{self.status}]>'


class EmployeeMovementLog(db.Model):
    """Audit log entry created whenever a transfer request is approved."""

    __tablename__ = 'employee_movement_logs'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    employee_id = db.Column(db.String(50), db.ForeignKey('employees.id'), nullable=False, index=True)
    origin_line_id = db.Column(db.Integer, db.ForeignKey('lines.id'), nullable=True)
    target_line_id = db.Column(db.Integer, db.ForeignKey('lines.id'), nullable=False)
    origin_shift_id = db.Column(db.Integer, db.ForeignKey('shifts.id'), nullable=True)
    target_shift_id = db.Column(db.Integer, db.ForeignKey('shifts.id'), nullable=True)
    approved_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=_utcnow, index=True)

    employee = db.relationship('Employee', backref='movement_logs', lazy=True)
    origin_line = db.relationship('Line', foreign_keys=[origin_line_id], backref='movement_origin', lazy=True)
    target_line = db.relationship('Line', foreign_keys=[target_line_id], backref='movement_target', lazy=True)
    origin_shift = db.relationship('Shift', foreign_keys=[origin_shift_id], backref='movement_origin_shift', lazy=True)
    target_shift = db.relationship('Shift', foreign_keys=[target_shift_id], backref='movement_target_shift', lazy=True)
    approved_by = db.relationship('User', foreign_keys=[approved_by_id], backref='movements_approved', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'employee_id': self.employee_id,
            'employee_name': self.employee.name if self.employee else None,
            'origin_line_id': self.origin_line_id,
            'origin_line': self.origin_line.name if self.origin_line else None,
            'origin_project': self.origin_line.project if self.origin_line else None,
            'target_line_id': self.target_line_id,
            'target_line': self.target_line.name if self.target_line else None,
            'target_project': self.target_line.project if self.target_line else None,
            'origin_shift_id': self.origin_shift_id,
            'target_shift_id': self.target_shift_id,
            'approved_by_id': self.approved_by_id,
            'approved_by': self.approved_by.username if self.approved_by else None,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
        }

    def __repr__(self):
        return f'<EmployeeMovementLog {self.id} {self.employee_id} {self.origin_line_id}->{self.target_line_id}>'
