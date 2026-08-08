from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_login import login_required, current_user
from extensions import db, bcrypt
from models.user import User
from models.employee import Employee
from models.allocation import Allocation
from models.attendance import Attendance
from models.audit_log import AuditLog
from models.shift import Shift
from services.excel_service import process_excel_upload, generate_absenteeism_report
from services.metrics_service import calculate_shift_net_minutes
from functools import wraps
import os
from datetime import date, datetime

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def admin_required(f):
    """Decorator to restrict access to ADMIN role only."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != 'ADMIN':
            flash('Acesso restrito ao administrador.', 'danger')
            return redirect(url_for('leader.index'))
        return f(*args, **kwargs)
    return decorated_function


@admin_bp.route('/')
@admin_required
def admin_home():
    """Admin dashboard home."""
    total_employees = Employee.query.filter_by(status='ACTIVE').count()
    today = date.today()
    today_attendances = Attendance.query.filter_by(record_date=today).count()
    total_lost_minutes = db.session.query(db.func.coalesce(db.func.sum(Attendance.minutes_lost), 0)).scalar()
    active_allocations = Allocation.query.filter_by(end_date=None).count()

    stats = {
        'total_employees': total_employees,
        'today_attendances': today_attendances,
        'total_lost_minutes': total_lost_minutes,
        'total_lost_hours': round(total_lost_minutes / 60, 2),
        'active_allocations': active_allocations
    }

    return render_template('admin/admin_home.html', stats=stats)


@admin_bp.route('/upload', methods=['GET', 'POST'])
@admin_required
def upload():
    """Excel file upload handler."""
    if request.method == 'POST':
        file = request.files.get('excel_file')
        if not file or not file.filename:
            flash('Nenhum arquivo selecionado.', 'warning')
            return redirect(url_for('admin.upload'))

        if not file.filename.endswith('.xlsx'):
            flash('Formato inválido. Envie um arquivo .xlsx.', 'danger')
            return redirect(url_for('admin.upload'))

        # Save to temp location
        temp_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'temp_upload.xlsx')
        file.save(temp_path)

        try:
            result = process_excel_upload(temp_path, user_id=current_user.id)
            flash(
                f'Upload processado: {result["employees_inserted"]} novos, '
                f'{result["employees_updated"]} atualizados, '
                f'{result["allocations_created"]} novas alocações.',
                'success'
            )
        except ValueError as e:
            flash(f'Erro no arquivo: {str(e)}', 'danger')
        except Exception as e:
            flash(f'Erro ao processar arquivo: {str(e)}', 'danger')
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

        return redirect(url_for('admin.upload'))

    return render_template('admin/upload.html')


@admin_bp.route('/users', methods=['GET', 'POST'])
@admin_required
def users():
    """User management view."""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'LIDER')

        if not username or not password:
            flash('Usuário e senha são obrigatórios.', 'warning')
        elif User.query.filter_by(username=username).first():
            flash('Nome de usuário já existe.', 'danger')
        else:
            user = User(
                username=username,
                password_hash=bcrypt.generate_password_hash(password).decode('utf-8'),
                role=role,
                is_active=True
            )
            db.session.add(user)
            db.session.commit()
            flash(f'Usuário {username} criado com sucesso.', 'success')

        return redirect(url_for('admin.users'))

    all_users = User.query.all()
    return render_template('admin/users.html', users=all_users)


@admin_bp.route('/users/<int:user_id>/toggle', methods=['POST'])
@admin_required
def toggle_user(user_id):
    """Activate/deactivate a user."""
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('Você não pode desativar a si mesmo.', 'warning')
    else:
        user.is_active = not user.is_active
        db.session.commit()
        flash(f'Usuário {user.username} {"ativado" if user.is_active else "desativado"}.', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    """Delete a user."""
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('Você não pode excluir a si mesmo.', 'warning')
    else:
        db.session.delete(user)
        db.session.commit()
        flash(f'Usuário {user.username} excluído.', 'success')
    return redirect(url_for('admin.users'))


# ─────────────────── AUDIT LOG HELPERS ───────────────────

def _translate_action(action):
    """Translate raw action enum to human-readable Portuguese."""
    translations = {
        'ATTENDANCE_CREATE': 'Apontamento Criado',
        'ATTENDANCE_UPDATE': 'Apontamento Alterado',
        'ATTENDANCE_DELETE': 'Apontamento Removido',
        'EMPLOYEE_CREATE': 'Funcionário Criado',
        'EMPLOYEE_UPDATE': 'Funcionário Atualizado',
        'EMPLOYEE_DELETE': 'Funcionário Removido',
    }
    return translations.get(action, action.replace('_', ' ').title())


def _translate_event_type(event_type):
    """Translate raw event type to human-readable Portuguese."""
    translations = {
        'PRESENT': 'Presente',
        'FULL_ABSENCE': 'Falta Integral',
        'LATE_ARRIVAL': 'Atraso',
        'EARLY_EXIT': 'Saída Antecipada',
        'VACATION': 'Férias',
    }
    return translations.get(event_type, event_type)


def _format_field_label(key):
    """Translate internal field keys to human-readable labels."""
    labels = {
        'employee_id': 'Matrícula',
        'record_date': 'Data',
        'event_type': 'Status',
        'check_in_time': 'Entrada',
        'check_out_time': 'Saída',
        'minutes_lost': 'Minutos Perdidos',
        'registered_by_id': 'Registrado por (ID)',
        'allocation_id': 'Alocação (ID)',
        'id': 'ID Interno',
    }
    return labels.get(key, key.replace('_', ' ').title())


def _format_field_value(key, value):
    """Format a field value for human display."""
    if value is None:
        return '—'

    if key == 'record_date' and isinstance(value, str):
        try:
            dt = datetime.strptime(value, '%Y-%m-%d').date()
            return dt.strftime('%d/%m/%Y')
        except ValueError:
            return value

    if key == 'event_type':
        return _translate_event_type(value)

    if key in ('check_in_time', 'check_out_time') and isinstance(value, str):
        return value[:5] if len(value) >= 5 else value

    if key == 'minutes_lost':
        return f'{value} min'

    if isinstance(value, bool):
        return 'Sim' if value else 'Não'

    return str(value)


def _build_audit_log_vm(log):
    """
    Build a view-model dict for a single audit log entry.
    Returns a dict with translated action, formatted changes, and modal-ready data.
    """
    vm = {
        'id': log.id,
        'created_at': log.created_at,
        'username': log.user.username if log.user else 'Sistema',
        'action_raw': log.action,
        'action_label': _translate_action(log.action),
        'action_badge': _action_badge_color(log.action),
        'attendance_id': log.attendance_id,
        'has_changes': bool(log.old_value or log.new_value),
        'summary': '',
        'changes': [],  # list of {field, before, after} for modal
        'is_create': 'CREATE' in log.action,
        'is_update': 'UPDATE' in log.action,
    }

    old = log.old_value or {}
    new = log.new_value or {}

    # Build summary line
    if vm['is_create']:
        et = _translate_event_type(new.get('event_type', ''))
        vm['summary'] = f'Criado: {et}'
    elif vm['is_update']:
        old_et = _translate_event_type(old.get('event_type', ''))
        new_et = _translate_event_type(new.get('event_type', ''))
        parts = []
        if old_et != new_et:
            parts.append(f'{old_et} → {new_et}')
        old_min = old.get('minutes_lost', 0) or 0
        new_min = new.get('minutes_lost', 0) or 0
        if old_min != new_min:
            parts.append(f'{old_min} → {new_min} min')
        vm['summary'] = ' | '.join(parts) if parts else 'Campos alterados'

    # Build changes table (merge keys from both old and new)
    all_keys = set()
    if old:
        all_keys.update(old.keys())
    if new:
        all_keys.update(new.keys())

    # Hide internal technical IDs
    hidden_keys = {'id', 'allocation_id', 'registered_by_id'}

    for key in sorted(all_keys):
        if key in hidden_keys:
            continue
        old_val = old.get(key)
        new_val = new.get(key)
        # Skip if both are None/empty
        if not old_val and not new_val and old_val != 0 and new_val != 0:
            continue
        # Skip if unchanged (for UPDATES)
        if vm['is_update'] and old_val == new_val:
            continue

        vm['changes'].append({
            'field': _format_field_label(key),
            'before': _format_field_value(key, old_val),
            'after': _format_field_value(key, new_val),
            'changed': old_val != new_val,
        })

    return vm


def _action_badge_color(action):
    """Return Bootstrap badge color class for an action."""
    if 'CREATE' in action:
        return 'success'
    if 'UPDATE' in action:
        return 'warning'
    if 'DELETE' in action:
        return 'danger'
    return 'secondary'


@admin_bp.route('/audit', methods=['GET'])
@admin_required
def audit():
    """Audit log view — humanized with translations and formatted changes."""
    logs = AuditLog.query.order_by(AuditLog.id.desc()).limit(100).all()
    log_vms = [_build_audit_log_vm(log) for log in logs]
    return render_template('admin/audit.html', logs=log_vms)


@admin_bp.route('/export-excel', methods=['GET'])
@admin_required
def export_excel():
    """Generate and download multi-tab absenteeism Excel report."""
    start_date_str = request.args.get('start_date', '')
    end_date_str = request.args.get('end_date', '')
    shift = request.args.get('shift', '')
    project = request.args.get('project', '')
    line = request.args.get('line', '')

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else None
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else None
    except ValueError:
        flash('Formato de data inválido. Use AAAA-MM-DD.', 'danger')
        return redirect(url_for('dashboard.index'))

    if not start_date or not end_date:
        flash('Datas de início e fim são obrigatórias.', 'warning')
        return redirect(url_for('dashboard.index'))

    try:
        buffer = generate_absenteeism_report(
            start_date=start_date,
            end_date=end_date,
            shift=shift if shift else None,
            project=project if project else None,
            line=line if line else None
        )
    except Exception as e:
        flash(f'Erro ao gerar relatório: {str(e)}', 'danger')
        return redirect(url_for('dashboard.index'))

    filename = f'absenteeism_report_{start_date_str}_to_{end_date_str}.xlsx'
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


# ─────────────────── SHIFT MANAGEMENT CRUD ───────────────────

@admin_bp.route('/shifts', methods=['GET'])
@admin_required
def shifts():
    """List all configured shifts."""
    all_shifts = Shift.query.order_by(Shift.id).all()
    return render_template('admin/shifts.html', shifts=all_shifts)


@admin_bp.route('/shifts/add', methods=['POST'])
@admin_required
def shifts_add():
    """Add a new shift."""
    shift_id = request.form.get('id', type=int)
    name = request.form.get('name', '').strip()
    start_time = request.form.get('start_time', '').strip()
    end_time = request.form.get('end_time', '').strip()
    break_minutes = request.form.get('break_minutes', 0, type=int)

    if not shift_id or not name or not start_time or not end_time:
        flash('Todos os campos são obrigatórios.', 'warning')
        return redirect(url_for('admin.shifts'))

    if Shift.query.get(shift_id):
        flash(f'Já existe um turno com ID {shift_id}.', 'danger')
        return redirect(url_for('admin.shifts'))

    net, overnight = calculate_shift_net_minutes(start_time, end_time, break_minutes)

    shift = Shift(
        id=shift_id,
        name=name,
        start_time=start_time,
        end_time=end_time,
        break_minutes=break_minutes,
        net_work_minutes=net,
        is_overnight=overnight,
        is_active=True
    )
    db.session.add(shift)
    db.session.commit()
    flash(f'Turno "{name}" criado com sucesso.', 'success')
    return redirect(url_for('admin.shifts'))


@admin_bp.route('/shifts/edit/<int:shift_id>', methods=['POST'])
@admin_required
def shifts_edit(shift_id):
    """Update an existing shift."""
    shift = Shift.query.get_or_404(shift_id)

    shift.name = request.form.get('name', shift.name).strip()
    shift.start_time = request.form.get('start_time', shift.start_time).strip()
    shift.end_time = request.form.get('end_time', shift.end_time).strip()
    shift.break_minutes = request.form.get('break_minutes', shift.break_minutes, type=int)

    net, overnight = calculate_shift_net_minutes(
        shift.start_time, shift.end_time, shift.break_minutes
    )
    shift.net_work_minutes = net
    shift.is_overnight = overnight

    db.session.commit()
    flash(f'Turno "{shift.name}" atualizado com sucesso.', 'success')
    return redirect(url_for('admin.shifts'))


@admin_bp.route('/shifts/toggle/<int:shift_id>', methods=['POST'])
@admin_required
def shifts_toggle(shift_id):
    """Activate or deactivate a shift."""
    shift = Shift.query.get_or_404(shift_id)
    shift.is_active = not shift.is_active
    db.session.commit()
    status = 'ativado' if shift.is_active else 'desativado'
    flash(f'Turno "{shift.name}" {status}.', 'success')
    return redirect(url_for('admin.shifts'))
