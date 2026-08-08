import math
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from extensions import db
from models.employee import Employee
from models.allocation import Allocation
from models.attendance import Attendance
from models.audit_log import AuditLog
from models.line_validation import LineValidation
from models.user import User
from models.shift import Shift
from services.metrics_service import calculate_lost_minutes, calculate_bradford_factor
from datetime import date, datetime, time as dt_time, timedelta
from collections import defaultdict

leader_bp = Blueprint('leader', __name__, url_prefix='/leader')


def _is_shift_closed(shift_id):
    """Return True if the shift ended more than 2 hours ago."""
    shift_def = Shift.query.get(shift_id)
    if not shift_def:
        return False

    now = datetime.now()
    try:
        h, m = map(int, shift_def.end_time.split(':'))
    except (ValueError, AttributeError):
        return False

    end_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if shift_def.is_overnight:
        # If end time has already passed today but shift is overnight, end is tomorrow
        if end_dt < now:
            end_dt = end_dt + timedelta(days=1)
    # Otherwise, end is today (if already passed, shift is closed)

    return now > end_dt + timedelta(hours=2)


@leader_bp.route('/')
@login_required
def index():
    """Main operational view for shift leaders with pagination and search — fully SQL-driven."""
    selected_date_str = request.args.get('date', '').strip()
    if selected_date_str:
        try:
            parsed_date = datetime.strptime(selected_date_str, '%Y-%m-%d').date()
        except ValueError:
            parsed_date = date.today()
    else:
        parsed_date = date.today()
    selected_date = parsed_date.isoformat()

    shift = request.args.get('shift', '')
    project = request.args.get('project', '')
    line = request.args.get('line', '')
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)

    # Get dynamic filter options
    shifts_rows = db.session.query(Allocation.shift).distinct().order_by(Allocation.shift).all()
    shifts = [s[0] for s in shifts_rows if s[0] is not None]

    allocations_query = db.session.query(Allocation.project).distinct().all()
    projects = sorted([a[0] for a in allocations_query if a[0]])

    lines_query = db.session.query(Allocation.line).distinct().all()
    lines = sorted([l[0] for l in lines_query if l[0]])

    # --- Build paginated query with JOIN to Employee (eliminates N+1) ---
    base_query = db.session.query(Allocation, Employee).join(
        Employee, Allocation.employee_id == Employee.id
    ).filter(
        Allocation.end_date.is_(None),
        Employee.status == 'ACTIVE'
    )

    if shift:
        base_query = base_query.filter(Allocation.shift == int(shift))
    if project:
        base_query = base_query.filter(Allocation.project == project)
    if line:
        base_query = base_query.filter(Allocation.line == line)

    # Search filter directly in SQL (employee ID or name)
    if search:
        search_pattern = f'%{search}%'
        base_query = base_query.filter(
            db.or_(
                Employee.id.ilike(search_pattern),
                Employee.name.ilike(search_pattern)
            )
        )

    # Apply pagination at the SQL level
    pagination = base_query.order_by(Employee.name).paginate(
        page=page, per_page=per_page, error_out=False
    )

    # --- Build employee list with their attendance for the selected date ---
    # Batch-fetch all attendance records for the paginated employees on the selected date
    paginated_emp_ids = [emp.id for _, emp in pagination.items]
    attendance_map = {}
    if paginated_emp_ids:
        att_records = Attendance.query.filter(
            Attendance.employee_id.in_(paginated_emp_ids),
            Attendance.record_date == parsed_date
        ).all()
        attendance_map = {a.employee_id: a for a in att_records}

    employee_list = []
    for alloc, emp in pagination.items:
        employee_list.append({
            'allocation': alloc,
            'employee': emp,
            'attendance': attendance_map.get(emp.id)
        })

    # Line validation status
    line_validation_status = None
    can_validate = False
    if shift and line:
        can_validate = True
        validation = LineValidation.query.filter_by(
            record_date=parsed_date,
            line=line,
            shift=int(shift)
        ).first()
        if validation:
            line_validation_status = {
                'validated': True,
                'validated_at': validation.validated_at.strftime('%d/%m/%Y %H:%M') if validation.validated_at else '',
                'validated_by': validation.validated_by.username if validation.validated_by else 'Desconhecido'
            }
        else:
            has_employees = len(employee_list) > 0
            line_validation_status = {
                'validated': False,
                'has_employees': has_employees
            }

    # Build shift schedules and closed status for frontend lock
    all_shifts = Shift.query.all()
    shift_schedules = {}
    closed_shift_ids = set()
    for s in all_shifts:
        shift_schedules[s.id] = {
            'start_time': s.start_time,
            'end_time': s.end_time,
            'is_overnight': s.is_overnight
        }
        if _is_shift_closed(s.id):
            closed_shift_ids.add(s.id)

    return render_template(
        'leader/index.html',
        employee_list=employee_list,
        selected_date=selected_date,
        selected_shift=shift,
        selected_project=project,
        selected_line=line,
        search=search,
        page=pagination.page,
        per_page=pagination.per_page,
        total_pages=pagination.pages,
        total_items=pagination.total,
        shifts=shifts,
        projects=projects,
        lines=lines,
        line_validation_status=line_validation_status,
        can_validate=can_validate,
        shift_schedules=shift_schedules,
        closed_shift_ids=closed_shift_ids
    )


@leader_bp.route('/register', methods=['POST'])
@login_required
def register():
    """Register attendance for an employee (legacy form-based)."""
    if current_user.role not in ['LIDER', 'ADMIN']:
        flash('Permissão negada.', 'danger')
        return redirect(url_for('leader.index'))

    employee_id = request.form.get('employee_id')
    record_date = request.form.get('record_date')
    event_type = request.form.get('event_type')
    check_in = request.form.get('check_in', '')
    check_out = request.form.get('check_out', '')

    if not employee_id or not record_date or not event_type:
        flash('Dados obrigatórios ausentes.', 'warning')
        return redirect(url_for('leader.index'))

    emp = Employee.query.get(employee_id)
    if not emp:
        flash('Funcionário não encontrado.', 'danger')
        return redirect(url_for('leader.index'))

    alloc = Allocation.query.filter_by(
        employee_id=employee_id, end_date=None
    ).first()
    if not alloc:
        flash('Funcionário não possui alocação ativa.', 'warning')
        return redirect(url_for('leader.index'))

    # Parse times
    check_in_time = None
    check_out_time = None
    if check_in:
        try:
            check_in_time = datetime.strptime(check_in, '%H:%M').time()
        except ValueError:
            pass
    if check_out:
        try:
            check_out_time = datetime.strptime(check_out, '%H:%M').time()
        except ValueError:
            pass

    parsed_date = datetime.strptime(record_date, '%Y-%m-%d').date()

    # Check for existing attendance record
    existing = Attendance.query.filter_by(
        employee_id=employee_id,
        record_date=parsed_date
    ).first()

    effective_type, minutes_lost = calculate_lost_minutes(
        alloc.shift, event_type, check_in_time, check_out_time, current_app.config
    )

    if existing:
        old_value = {
            'event_type': existing.event_type,
            'check_in_time': existing.check_in_time.isoformat() if existing.check_in_time else None,
            'check_out_time': existing.check_out_time.isoformat() if existing.check_out_time else None,
            'minutes_lost': existing.minutes_lost
        }

        existing.event_type = effective_type
        existing.check_in_time = check_in_time
        existing.check_out_time = check_out_time
        existing.minutes_lost = minutes_lost
        existing.registered_by_id = current_user.id

        new_value = {
            'event_type': effective_type,
            'check_in_time': check_in_time.isoformat() if check_in_time else None,
            'check_out_time': check_out_time.isoformat() if check_out_time else None,
            'minutes_lost': minutes_lost
        }

        db.session.add(AuditLog(
            attendance_id=existing.id,
            user_id=current_user.id,
            action='ATTENDANCE_UPDATE',
            old_value=old_value,
            new_value=new_value
        ))

        flash(f'Registro atualizado para {emp.name}. Tipo: {effective_type}', 'success')
    else:
        attendance = Attendance(
            record_date=parsed_date,
            employee_id=employee_id,
            allocation_id=alloc.id,
            event_type=effective_type,
            check_in_time=check_in_time,
            check_out_time=check_out_time,
            minutes_lost=minutes_lost,
            registered_by_id=current_user.id
        )
        db.session.add(attendance)
        db.session.flush()

        db.session.add(AuditLog(
            attendance_id=attendance.id,
            user_id=current_user.id,
            action='ATTENDANCE_CREATE',
            old_value=None,
            new_value=attendance.to_dict()
        ))

        flash(f'Registro criado para {emp.name}. Tipo: {effective_type}', 'success')

    db.session.commit()
    return redirect(url_for('leader.index', date=record_date))


@leader_bp.route('/api/quick-action', methods=['POST'])
@login_required
def api_quick_action():
    """AJAX endpoint for inline quick actions (absence, vacation, present, delay, exit)."""
    if current_user.role not in ['LIDER', 'ADMIN']:
        return jsonify({'success': False, 'error': 'Permissão negada.'}), 403

    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'Dados inválidos.'}), 400

    employee_id = data.get('employee_id')
    record_date_str = data.get('record_date')
    event_type = data.get('event_type')
    check_in = data.get('check_in', '')
    check_out = data.get('check_out', '')

    if not employee_id or not record_date_str or not event_type:
        return jsonify({'success': False, 'error': 'Dados obrigatórios ausentes.'}), 400

    emp = Employee.query.get(employee_id)
    if not emp:
        return jsonify({'success': False, 'error': 'Funcionário não encontrado.'}), 404

    alloc = Allocation.query.filter_by(employee_id=employee_id, end_date=None).first()
    if not alloc:
        return jsonify({'success': False, 'error': 'Funcionário não possui alocação ativa.'}), 400

    # Shift time-lock: LIDER cannot modify closed shifts
    if current_user.role == 'LIDER' and _is_shift_closed(alloc.shift):
        return jsonify({'error': 'Apontamento bloqueado. Este turno já está encerrado. Alterações requerem perfil ADMIN.'}), 403

    check_in_time = None
    check_out_time = None
    if check_in:
        try:
            check_in_time = datetime.strptime(check_in, '%H:%M').time()
        except ValueError:
            return jsonify({'success': False, 'error': 'Horário de entrada inválido.'}), 400
    if check_out:
        try:
            check_out_time = datetime.strptime(check_out, '%H:%M').time()
        except ValueError:
            return jsonify({'success': False, 'error': 'Horário de saída inválido.'}), 400

    try:
        record_date = datetime.strptime(record_date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'success': False, 'error': 'Data inválida.'}), 400

    effective_type, minutes_lost = calculate_lost_minutes(
        alloc.shift, event_type, check_in_time, check_out_time, current_app.config
    )

    existing = Attendance.query.filter_by(
        employee_id=employee_id,
        record_date=record_date
    ).first()

    if existing:
        old_value = existing.to_dict()
        existing.event_type = effective_type
        existing.check_in_time = check_in_time
        existing.check_out_time = check_out_time
        existing.minutes_lost = minutes_lost
        existing.registered_by_id = current_user.id

        db.session.add(AuditLog(
            attendance_id=existing.id,
            user_id=current_user.id,
            action='ATTENDANCE_UPDATE',
            old_value=old_value,
            new_value=existing.to_dict()
        ))
        action = 'updated'
    else:
        attendance = Attendance(
            record_date=record_date,
            employee_id=employee_id,
            allocation_id=alloc.id,
            event_type=effective_type,
            check_in_time=check_in_time,
            check_out_time=check_out_time,
            minutes_lost=minutes_lost,
            registered_by_id=current_user.id
        )
        db.session.add(attendance)
        db.session.flush()

        db.session.add(AuditLog(
            attendance_id=attendance.id,
            user_id=current_user.id,
            action='ATTENDANCE_CREATE',
            old_value=None,
            new_value=attendance.to_dict()
        ))
        existing = attendance
        action = 'created'

    db.session.commit()

    # Build status badge HTML
    badge_html = _render_status_badge(existing)

    return jsonify({
        'success': True,
        'action': action,
        'event_type': effective_type,
        'minutes_lost': minutes_lost,
        'badge_html': badge_html
    })


@leader_bp.route('/api/reset-attendance', methods=['POST'])
@login_required
def api_reset_attendance():
    """Delete attendance record (reset to unregistered)."""
    if current_user.role not in ['LIDER', 'ADMIN']:
        return jsonify({'success': False, 'error': 'Permissão negada.'}), 403

    data = request.get_json()
    employee_id = data.get('employee_id')
    record_date_str = data.get('record_date')

    if not employee_id or not record_date_str:
        return jsonify({'success': False, 'error': 'Dados obrigatórios ausentes.'}), 400

    try:
        record_date = datetime.strptime(record_date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'success': False, 'error': 'Data inválida.'}), 400

    existing = Attendance.query.filter_by(
        employee_id=employee_id,
        record_date=record_date
    ).first()

    if existing:
        db.session.delete(existing)
        db.session.commit()

    return jsonify({
        'success': True,
        'badge_html': '<span class="badge bg-secondary">Não registrado</span>'
    })


@leader_bp.route('/api/validate-line', methods=['POST'])
@login_required
def api_validate_line():
    """Validate/finalize a line for the day."""
    if current_user.role not in ['LIDER', 'ADMIN']:
        return jsonify({'success': False, 'error': 'Permissão negada.'}), 403

    data = request.get_json()
    record_date_str = data.get('record_date')
    line = data.get('line')
    shift = data.get('shift')

    if not record_date_str or not line or not shift:
        return jsonify({'success': False, 'error': 'Dados obrigatórios ausentes.'}), 400

    try:
        record_date = datetime.strptime(record_date_str, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'success': False, 'error': 'Data inválida.'}), 400

    # Check if already validated
    existing = LineValidation.query.filter_by(
        record_date=record_date,
        line=line,
        shift=int(shift)
    ).first()

    if existing:
        return jsonify({'success': False, 'error': 'Esta linha já foi auditada para este dia.'}), 409

    validation = LineValidation(
        record_date=record_date,
        line=line,
        shift=int(shift),
        validated_by_id=current_user.id
    )
    db.session.add(validation)
    db.session.commit()

    return jsonify({
        'success': True,
        'message': 'Linha auditada com sucesso!',
        'validated_at': validation.validated_at.strftime('%d/%m/%Y %H:%M'),
        'validated_by': current_user.username
    })


@leader_bp.route('/api/employees', methods=['GET'])
@login_required
def api_employees():
    """JSON API to fetch employees filtered by shift/project/line — SQL JOIN eliminates N+1."""
    shift = request.args.get('shift', '')
    project = request.args.get('project', '')
    line = request.args.get('line', '')

    query = db.session.query(Allocation, Employee).join(
        Employee, Allocation.employee_id == Employee.id
    ).filter(
        Allocation.end_date.is_(None),
        Employee.status == 'ACTIVE'
    )

    if shift:
        query = query.filter(Allocation.shift == int(shift))
    if project:
        query = query.filter(Allocation.project == project)
    if line:
        query = query.filter(Allocation.line == line)

    rows = query.order_by(Employee.name).all()

    result = []
    for alloc, emp in rows:
        result.append({
            'employee_id': emp.id,
            'name': emp.name,
            'shift': alloc.shift,
            'project': alloc.project,
            'line': alloc.line
        })

    return jsonify({'employees': result})


@leader_bp.route('/api/attendance/<employee_id>/<record_date>', methods=['GET'])
@login_required
def get_attendance(employee_id, record_date):
    """Get existing attendance record for an employee on a specific date."""
    try:
        parsed_date = datetime.strptime(record_date, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Formato de data inválido'}), 400

    attendance = Attendance.query.filter_by(
        employee_id=employee_id,
        record_date=parsed_date
    ).first()

    if attendance:
        return jsonify(attendance.to_dict())
    return jsonify({'message': 'Registro não encontrado'}), 404


@leader_bp.route('/api/employee/<employee_id>/bradford', methods=['GET'])
@login_required
def get_employee_bradford(employee_id):
    """Get Bradford Factor for a specific employee."""
    emp = Employee.query.get(employee_id)
    if not emp:
        return jsonify({'error': 'Funcionário não encontrado.'}), 404

    bf = calculate_bradford_factor(employee_id)
    return jsonify({
        'employee_id': employee_id,
        'employee_name': emp.name,
        **bf
    })


# ─────────────────── QUICK HISTORY MODAL ───────────────────

@leader_bp.route('/api/employee-quick-history/<employee_id>', methods=['GET'])
@login_required
def api_employee_quick_history(employee_id):
    """Return last 15 absence records + Bradford Factor + current allocation for modal."""
    emp = Employee.query.get(employee_id)
    if not emp:
        return jsonify({'error': 'Funcionário não encontrado.'}), 404

    alloc = Allocation.query.filter_by(employee_id=employee_id, end_date=None).first()

    # Bradford Factor
    bf = calculate_bradford_factor(employee_id)

    # Last 15 non-present records
    recent = Attendance.query.filter(
        Attendance.employee_id == employee_id,
        Attendance.event_type != 'PRESENT'
    ).order_by(Attendance.record_date.desc()).limit(15).all()

    records = []
    for r in recent:
        records.append({
            'record_date': r.record_date.isoformat() if r.record_date else None,
            'event_type': r.event_type,
            'check_in_time': r.check_in_time.isoformat() if r.check_in_time else None,
            'check_out_time': r.check_out_time.isoformat() if r.check_out_time else None,
            'minutes_lost': r.minutes_lost,
            'registered_by': r.registered_by.username if r.registered_by else 'Desconhecido'
        })

    return jsonify({
        'employee': {
            'id': emp.id,
            'name': emp.name,
            'status': emp.status
        },
        'allocation': {
            'shift': alloc.shift if alloc else None,
            'project': alloc.project if alloc else None,
            'line': alloc.line if alloc else None
        } if alloc else None,
        'bradford': bf,
        'recent_absences': records
    })


# ─────────────────── EMPLOYEE HISTORY PAGE ───────────────────

@leader_bp.route('/employee/<employee_id>/history')
@login_required
def employee_history(employee_id):
    """Dedicated employee absence history and analytics page."""
    if current_user.role not in ['LIDER', 'ADMIN']:
        flash('Permissão negada.', 'danger')
        return redirect(url_for('leader.index'))

    emp = Employee.query.get(employee_id)
    if not emp:
        flash('Funcionário não encontrado.', 'danger')
        return redirect(url_for('leader.index'))

    alloc = Allocation.query.filter_by(employee_id=employee_id, end_date=None).first()
    bf = calculate_bradford_factor(employee_id)

    return render_template(
        'employee_history.html',
        employee=emp,
        allocation=alloc,
        bradford=bf
    )


@leader_bp.route('/api/employee-history-data/<employee_id>')
@login_required
def api_employee_history_data(employee_id):
    """Return filtered absence data for charts and table."""
    if current_user.role not in ['LIDER', 'ADMIN']:
        return jsonify({'error': 'Permissão negada.'}), 403

    emp = Employee.query.get(employee_id)
    if not emp:
        return jsonify({'error': 'Funcionário não encontrado.'}), 404

    # Parse date filters
    date_to = request.args.get('date_to', date.today().isoformat())
    date_from = request.args.get('date_from', (date.today() - timedelta(days=365)).isoformat())
    event_type_filter = request.args.get('event_type', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)

    try:
        start = datetime.strptime(date_from, '%Y-%m-%d').date()
        end = datetime.strptime(date_to, '%Y-%m-%d').date()
    except ValueError:
        return jsonify({'error': 'Datas inválidas.'}), 400

    # Base query — exclude PRESENT
    query = Attendance.query.filter(
        Attendance.employee_id == employee_id,
        Attendance.event_type != 'PRESENT',
        Attendance.record_date >= start,
        Attendance.record_date <= end
    )

    # Optional event_type filter
    if event_type_filter:
        query = query.filter(Attendance.event_type == event_type_filter)

    # Total counts for summary
    base_query = Attendance.query.filter(
        Attendance.employee_id == employee_id,
        Attendance.event_type != 'PRESENT',
        Attendance.record_date >= start,
        Attendance.record_date <= end
    )

    total_records = base_query.count()
    total_lost_minutes = db.session.query(
        db.func.coalesce(db.func.sum(Attendance.minutes_lost), 0)
    ).filter(
        Attendance.employee_id == employee_id,
        Attendance.event_type != 'PRESENT',
        Attendance.record_date >= start,
        Attendance.record_date <= end
    )
    if event_type_filter:
        total_lost_minutes = total_lost_minutes.filter(Attendance.event_type == event_type_filter)
    total_lost_minutes = total_lost_minutes.scalar() or 0

    count_absence = base_query.filter(Attendance.event_type == 'FULL_ABSENCE').count()
    count_delay = base_query.filter(Attendance.event_type == 'LATE_ARRIVAL').count()
    count_exit = base_query.filter(Attendance.event_type == 'EARLY_EXIT').count()

    # Monthly trend
    monthly_data = db.session.query(
        db.func.strftime('%Y-%m', Attendance.record_date).label('month'),
        db.func.sum(Attendance.minutes_lost).label('total_lost')
    ).filter(
        Attendance.employee_id == employee_id,
        Attendance.event_type != 'PRESENT',
        Attendance.record_date >= start,
        Attendance.record_date <= end
    )
    if event_type_filter:
        monthly_data = monthly_data.filter(Attendance.event_type == event_type_filter)
    monthly_data = monthly_data.group_by('month').order_by('month').all()

    monthly_trend = [{'month': m, 'lost_minutes': int(v or 0)} for m, v in monthly_data]

    # Paginated records
    total_pages = max(1, math.ceil(total_records / per_page))
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    records_query = query.order_by(Attendance.record_date.desc()).offset(offset).limit(per_page)
    records = records_query.all()

    records_list = []
    for r in records:
        records_list.append({
            'id': r.id,
            'record_date': r.record_date.isoformat() if r.record_date else None,
            'event_type': r.event_type,
            'check_in_time': r.check_in_time.isoformat() if r.check_in_time else None,
            'check_out_time': r.check_out_time.isoformat() if r.check_out_time else None,
            'minutes_lost': r.minutes_lost,
            'registered_by': r.registered_by.username if r.registered_by else 'Desconhecido'
        })

    return jsonify({
        'summary': {
            'total_records': total_records,
            'total_lost_minutes': total_lost_minutes,
            'total_lost_hours': round(total_lost_minutes / 60, 2),
            'count_absence': count_absence,
            'count_delay': count_delay,
            'count_exit': count_exit,
            'date_from': start.isoformat(),
            'date_to': end.isoformat()
        },
        'monthly_trend': monthly_trend,
        'records': records_list,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages,
            'total_items': total_records
        }
    })


def _render_status_badge(attendance):
    """Render HTML badge for attendance status (used by AJAX responses)."""
    if not attendance:
        return '<span class="badge bg-secondary">Não registrado</span>'

    event_type = attendance.event_type
    minutes_lost = attendance.minutes_lost

    if event_type == 'PRESENT':
        badge = '<span class="badge bg-success">Presente</span>'
    elif event_type == 'FULL_ABSENCE':
        badge = '<span class="badge bg-danger">Falta</span>'
    elif event_type == 'VACATION':
        badge = '<span class="badge bg-warning text-dark">Férias</span>'
    elif event_type == 'LATE_ARRIVAL':
        badge = '<span class="badge bg-warning">Atraso</span>'
    elif event_type == 'EARLY_EXIT':
        badge = '<span class="badge bg-info">Saída Antecipada</span>'
    else:
        badge = f'<span class="badge bg-secondary">{event_type}</span>'

    badge += f'<br><small class="text-muted">{minutes_lost} min perdidos</small>'
    return badge