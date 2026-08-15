"""Employee-related business logic helpers."""
from extensions import db
from models.employee import Employee
from models.allocation import Allocation
from models.attendance import Attendance


def migrate_employee_id(old_id, new_id):
    """Migrate an employee's registration number (matrícula) to a new ID.

    Preserves all historical records (Attendance and Allocation rows, and by
    extension their AuditLog entries) by reassigning their employee_id foreign
    keys, then removes the old employee entity.

    Args:
        old_id (str): current matrícula.
        new_id (str): destination matrícula.

    Returns:
        Employee: the new Employee record.

    Raises:
        ValueError: if old_id is missing/unknown or new_id is already taken.
    """
    old_id = str(old_id or '').strip()
    new_id = str(new_id or '').strip()

    if not old_id:
        raise ValueError('Matrícula de origem não informada.')
    if not new_id:
        raise ValueError('Nova matrícula não informada.')
    if old_id == new_id:
        raise ValueError('A nova matrícula deve ser diferente da atual.')

    old_emp = Employee.query.get(old_id)
    if not old_emp:
        raise ValueError(f'Funcionário com matrícula {old_id} não encontrado.')

    if Employee.query.get(new_id):
        raise ValueError(f'Matrícula de destino {new_id} já existe.')

    try:
        # 1. Create the new employee first so new_id exists before FK reassignment.
        new_emp = Employee(
            id=new_id,
            name=old_emp.name,
            status=old_emp.status,
            vacation_start=old_emp.vacation_start,
            vacation_end=old_emp.vacation_end,
        )
        db.session.add(new_emp)
        db.session.flush()

        # 2. Reassign historical records to the new ID.
        Attendance.query.filter_by(employee_id=old_id).update(
            {'employee_id': new_id}, synchronize_session=False
        )
        Allocation.query.filter_by(employee_id=old_id).update(
            {'employee_id': new_id}, synchronize_session=False
        )

        # 3. Remove the old employee entity.
        db.session.delete(old_emp)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return new_emp
