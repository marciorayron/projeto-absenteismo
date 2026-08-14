from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, current_app, session
from flask_login import login_required, current_user
from extensions import db, bcrypt
from models.user import User
from models.employee import Employee
from models.allocation import Allocation
from models.attendance import Attendance
from models.audit_log import AuditLog
from models.shift import Shift
from models.line import Line
from models.leader_scope import LeaderScope
from services.excel_service import process_excel_upload, generate_absenteeism_report
from services.metrics_service import calculate_shift_net_minutes
from functools import wraps
import io
import os
import json
import sqlite3
import pandas as pd
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
            session['upload_summary'] = result
            flash('Upload processado com sucesso.', 'success')
        except ValueError as e:
            flash(f'Erro no arquivo: {str(e)}', 'danger')
        except Exception as e:
            flash(f'Erro ao processar arquivo: {str(e)}', 'danger')
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

        return redirect(url_for('admin.upload'))

    summary = session.pop('upload_summary', None)
    return render_template('admin/upload.html', summary=summary)


def _parse_managed_scope(form):
    """Parse the managed-scope JSON into a list of {shift, line_id} dicts."""
    raw = form.get('managed_scope', '')
    items = []
    if not raw:
        return items
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return items
    if not isinstance(data, list):
        return items
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            shift = int(item.get('shift'))
            line_id = int(item.get('line_id'))
        except (TypeError, ValueError):
            continue
        key = (shift, line_id)
        if key in seen:
            continue
        seen.add(key)
        items.append({'shift': shift, 'line_id': line_id})
    return items


def _apply_scopes(user, items):
    """Replace the user's managed scopes with the given {shift, line_id} items."""
    user.managed_scopes.clear()
    for item in items:
        user.managed_scopes.append(LeaderScope(line_id=item['line_id'], shift_id=item['shift']))


def _get_available_scopes():
    """Return sorted distinct (shift, line, project) combos resolved against Line/Allocation."""
    lines_by_key = {(l.name, l.project): l.id for l in Line.query.filter_by(is_active=True).all()}
    rows = db.session.query(
        Allocation.shift, Allocation.project, Allocation.line
    ).filter(Allocation.end_date.is_(None)).distinct().all()
    scopes = []
    for shift, project, line_name in rows:
        if not line_name:
            continue
        line_id = lines_by_key.get((line_name, project or ''))
        if line_id is None:
            continue
        scopes.append({
            'shift': shift,
            'line_id': line_id,
            'line': line_name,
            'project': project or ''
        })
    scopes.sort(key=lambda x: (x['shift'] or 0, x['project'], x['line']))
    return scopes


@admin_bp.route('/users', methods=['GET', 'POST'])
@admin_required
def users():
    """User management view."""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'LIDER')
        scope_items = _parse_managed_scope(request.form)

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
            db.session.flush()
            _apply_scopes(user, scope_items)
            db.session.commit()
            flash(f'Usuário {username} criado com sucesso.', 'success')

        return redirect(url_for('admin.users'))

    all_users = User.query.all()
    available_scopes = _get_available_scopes()
    return render_template('admin/users.html', users=all_users, available_scopes=available_scopes)


@admin_bp.route('/users/<int:user_id>/lines', methods=['POST'])
@admin_required
def edit_user_lines(user_id):
    """Update the managed scope (shift+project+line) assigned to a leader."""
    user = User.query.get_or_404(user_id)
    scope_items = _parse_managed_scope(request.form)
    _apply_scopes(user, scope_items)
    db.session.commit()
    flash(f'Escopo de {user.username} atualizado.', 'success')
    return redirect(url_for('admin.users'))


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
        'USER_PASSWORD_CHANGE': 'Alteração de Senha',
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


# ─────────────────── DATABASE MANAGEMENT CENTER ───────────────────

_TABLE_EXPORT_MAP = {
    'employees': Employee,
    'allocations': Allocation,
    'attendances': Attendance,
    'shifts': Shift,
    'users': User,
    'audit_logs': AuditLog,
}

_TABLE_LABELS = {
    'employees': 'Funcionários',
    'allocations': 'Alocações',
    'attendances': 'Apontamentos',
    'shifts': 'Turnos',
    'users': 'Usuários',
    'audit_logs': 'Auditoria',
}

SYSTEM_VERSION = '2.0.0'


def _format_file_size(num_bytes):
    """Return a human-readable file size string."""
    if num_bytes < 1024:
        return f'{num_bytes} B'
    for unit in ('KB', 'MB', 'GB'):
        num_bytes /= 1024.0
        if num_bytes < 1024:
            return f'{num_bytes:.2f} {unit}'
    return f'{num_bytes:.2f} GB'


@admin_bp.route('/database', methods=['GET'])
@admin_required
def database():
    """Database management panel: record counts, exports, and protected resets."""
    counts = {
        'employees': Employee.query.count(),
        'allocations': Allocation.query.count(),
        'attendances': Attendance.query.count(),
        'shifts': Shift.query.count(),
        'users': User.query.count(),
        'audit_logs': AuditLog.query.count(),
    }

    db_path = current_app.config.get('DB_PATH')
    db_size = os.path.getsize(db_path) if db_path and os.path.exists(db_path) else 0

    system_info = {
        'system_version': SYSTEM_VERSION,
        'sqlite_version': sqlite3.sqlite_version,
        'db_path': db_path,
        'db_size': _format_file_size(db_size),
    }

    return render_template(
        'admin/database.html',
        counts=counts,
        labels=_TABLE_LABELS,
        system_info=system_info
    )


@admin_bp.route('/database/export/<table_name>', methods=['GET'])
@admin_required
def database_export(table_name):
    """Export all records of a table to an Excel (.xlsx) file."""
    model = _TABLE_EXPORT_MAP.get(table_name)
    if model is None:
        flash('Tabela não encontrada.', 'danger')
        return redirect(url_for('admin.database'))

    records = model.query.all()
    rows = [r.to_dict() for r in records]

    df = pd.DataFrame(rows)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name=table_name[:31], index=False)
    buffer.seek(0)

    filename = f'{table_name}_{date.today().isoformat()}.xlsx'
    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@admin_bp.route('/database/clear-attendance', methods=['POST'])
@admin_required
def database_clear_attendance():
    """Delete all attendance records (requires admin password re-entry)."""
    password = request.form.get('password', '')
    if not bcrypt.check_password_hash(current_user.password_hash, password):
        flash('Senha incorreta. Ação cancelada.', 'danger')
        return redirect(url_for('admin.database'))

    deleted = Attendance.query.delete()
    db.session.commit()
    flash(f'{deleted} registro(s) de apontamento removido(s).', 'success')
    return redirect(url_for('admin.database'))


@admin_bp.route('/database/clear-employees', methods=['POST'])
@admin_required
def database_clear_employees():
    """Delete Employee & Allocation records (requires admin password re-entry).

    Attendance records are also removed because they reference employees and
    allocations via foreign keys — leaving them would create orphaned rows.
    """
    password = request.form.get('password', '')
    if not bcrypt.check_password_hash(current_user.password_hash, password):
        flash('Senha incorreta. Ação cancelada.', 'danger')
        return redirect(url_for('admin.database'))

    deleted_att = Attendance.query.delete()
    deleted_alloc = Allocation.query.delete()
    deleted_emp = Employee.query.delete()
    db.session.commit()
    flash(
        f'Removidos: {deleted_emp} funcionário(s), {deleted_alloc} alocação(ões) '
        f'e {deleted_att} apontamento(s).',
        'success'
    )
    return redirect(url_for('admin.database'))


# ─────────────────── SHIFT MANAGEMENT CRUD ───────────────────

@admin_bp.route('/shifts', methods=['GET'])
@admin_required
def shifts():
    """List all configured shifts."""
    all_shifts = Shift.query.order_by(Shift.id).all()
    return render_template('admin/shifts.html', shifts=all_shifts)


def _parse_work_days(form):
    """Parse weekday checkboxes into a comma-separated string (0=Monday .. 6=Sunday).

    Falls back to Monday-Friday (``0,1,2,3,4``) when nothing valid is selected.
    """
    selected = form.getlist('work_days')
    days = set()
    for raw in selected:
        try:
            idx = int(raw)
        except (ValueError, TypeError):
            continue
        if 0 <= idx <= 6:
            days.add(idx)
    if not days:
        return '0,1,2,3,4'
    return ','.join(str(d) for d in sorted(days))


@admin_bp.route('/shifts/add', methods=['POST'])
@admin_required
def shifts_add():
    """Add a new shift."""
    shift_id = request.form.get('id', type=int)
    name = request.form.get('name', '').strip()
    start_time = request.form.get('start_time', '').strip()
    end_time = request.form.get('end_time', '').strip()
    break_minutes = request.form.get('break_minutes', 0, type=int)
    work_days = _parse_work_days(request.form)

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
        is_active=True,
        work_days=work_days
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
    shift.work_days = _parse_work_days(request.form)

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


# ─────────────────── LINE & PROJECT MANAGEMENT CRUD ───────────────────

@admin_bp.route('/lines', methods=['GET'])
@admin_required
def lines():
    """List all lines with allocation and assignment counts."""
    search = request.args.get('search', '').strip()
    project_filter = request.args.get('project', '').strip()

    query = Line.query
    if search:
        query = query.filter(Line.name.ilike(f'%{search}%'))
    if project_filter:
        query = query.filter(Line.project.ilike(f'%{project_filter}%'))

    all_lines = query.order_by(Line.project, Line.name).all()
    projects = sorted({l.project for l in Line.query.all() if l.project})

    for line in all_lines:
        line.allocated_employees = Allocation.query.filter_by(
            line=line.name, project=line.project, end_date=None
        ).count()
        line.assigned_shifts = LeaderScope.query.filter_by(line_id=line.id).count()

    return render_template(
        'admin/lines.html',
        lines=all_lines,
        projects=projects,
        search=search,
        project_filter=project_filter
    )


@admin_bp.route('/lines/add', methods=['POST'])
@admin_required
def lines_add():
    """Create a new line."""
    name = request.form.get('name', '').strip()
    project = request.form.get('project', '').strip()

    if not name or not project:
        flash('Nome da linha e projeto são obrigatórios.', 'warning')
        return redirect(url_for('admin.lines'))

    if Line.query.filter_by(name=name, project=project).first():
        flash('Já existe uma linha com este nome e projeto.', 'danger')
        return redirect(url_for('admin.lines'))

    db.session.add(Line(name=name, project=project, is_active=True))
    db.session.commit()
    flash(f'Linha "{name}" criada com sucesso.', 'success')
    return redirect(url_for('admin.lines'))


@admin_bp.route('/lines/edit/<int:line_id>', methods=['POST'])
@admin_required
def lines_edit(line_id):
    """Edit a line's name and project."""
    line = Line.query.get_or_404(line_id)
    name = request.form.get('name', '').strip()
    project = request.form.get('project', '').strip()

    if not name or not project:
        flash('Nome da linha e projeto são obrigatórios.', 'warning')
        return redirect(url_for('admin.lines'))

    conflict = Line.query.filter(
        Line.name == name, Line.project == project, Line.id != line_id
    ).first()
    if conflict:
        flash('Já existe uma linha com este nome e projeto.', 'danger')
        return redirect(url_for('admin.lines'))

    line.name = name
    line.project = project
    db.session.commit()
    flash(f'Linha "{name}" atualizada com sucesso.', 'success')
    return redirect(url_for('admin.lines'))


@admin_bp.route('/lines/toggle/<int:line_id>', methods=['POST'])
@admin_required
def lines_toggle(line_id):
    """Activate or deactivate a line."""
    line = Line.query.get_or_404(line_id)
    line.is_active = not line.is_active
    db.session.commit()
    status = 'ativada' if line.is_active else 'desativada'
    flash(f'Linha "{line.name}" {status}.', 'success')
    return redirect(url_for('admin.lines'))


# ─────────────────── EMPLOYEE VACATION MANAGEMENT ───────────────────

@admin_bp.route('/employees', methods=['GET'])
@admin_required
def employees():
    """Employee management view with vacation period editing."""
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 50

    query = Employee.query
    if search:
        pattern = f'%{search}%'
        query = query.filter(
            db.or_(Employee.id.ilike(pattern), Employee.name.ilike(pattern))
        )

    pagination = query.order_by(Employee.name).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template(
        'admin/employees.html',
        pagination=pagination,
        search=search
    )


@admin_bp.route('/employees/<employee_id>/vacation', methods=['POST'])
@admin_required
def employee_vacation(employee_id):
    """Set or clear an employee's vacation period."""
    emp = Employee.query.get_or_404(employee_id)

    vacation_start = request.form.get('vacation_start', '').strip()
    vacation_end = request.form.get('vacation_end', '').strip()

    def _parse_date(value):
        if not value:
            return None
        try:
            return datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            return False

    start = _parse_date(vacation_start)
    end = _parse_date(vacation_end)

    back = url_for('admin.employees', search=request.args.get('search', ''))

    if start is False or end is False:
        flash('Data inválida. Use o formato AAAA-MM-DD.', 'danger')
        return redirect(back)

    if (start and not end) or (end and not start):
        flash('Informe ambas as datas (início e fim) ou deixe ambas vazias para limpar.', 'warning')
        return redirect(back)

    if start and end and start > end:
        flash('A data de início não pode ser posterior à data de fim.', 'danger')
        return redirect(back)

    emp.vacation_start = start
    emp.vacation_end = end
    db.session.commit()

    if start and end:
        flash(f'Férias de {emp.name} definidas de {start} a {end}.', 'success')
    else:
        flash(f'Período de férias de {emp.name} removido.', 'success')
    return redirect(back)
