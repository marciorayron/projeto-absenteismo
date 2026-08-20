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
from models.calendar import CompanyCalendar
from services.excel_service import process_excel_upload, analyze_excel_upload, generate_absenteeism_report
from services.absence_history_service import process_absence_history_upload, AbsenceImportError
from services.metrics_service import calculate_shift_net_minutes
from services.employee_service import migrate_employee_id
from functools import wraps
import io
import os
import json
import calendar as pycalendar
import sqlite3
import uuid
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


def staff_required(f):
    """Decorator to allow access to ADMIN and SUPERVISOR roles only."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role not in ('ADMIN', 'SUPERVISOR'):
            flash('Acesso restrito ao administrador ou supervisor.', 'danger')
            return redirect(url_for('leader.index'))
        return f(*args, **kwargs)
    return decorated_function


def admin_only_required(f):
    """Decorator that returns HTTP 403 for non-ADMIN roles (used on protected routes).

    Unlike ``admin_required`` (which redirects), this enforces a hard block so that
    a SUPERVISOR accessing the route directly receives a 403 response.
    """
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != 'ADMIN':
            return jsonify({'error': 'Acesso restrito ao administrador.'}), 403
        return f(*args, **kwargs)
    return decorated_function


@admin_bp.route('/')
@staff_required
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


@admin_bp.route('/upload', methods=['GET'])
@staff_required
def upload():
    """Excel upload page (two-step: analyze then confirm)."""
    summary = session.pop('upload_summary', None)
    return render_template('admin/upload.html', summary=summary)


@admin_bp.route('/import/absence-history', methods=['POST'])
@staff_required
def import_absence_history():
    """Import a historical absence Excel (.xlsx/.xls) file.

    Non-destructive: existing employees are never modified, missing ones are
    auto-created as inactive, and one ``Attendance`` record is stored per valid row.
    Returns JSON when called via AJAX (the admin-home modal), otherwise flashes a
    summary and redirects back to the admin home.
    """
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    file = request.files.get('absence_history_file')

    def _err_response(message):
        if is_ajax:
            return jsonify({'success': False, 'error': message}), 400
        flash(message, 'danger')
        return redirect(url_for('admin.admin_home'))

    if file is None or not file.filename:
        return _err_response('Nenhum arquivo enviado.')

    try:
        summary = process_absence_history_upload(file, file.filename, current_user.id)
    except AbsenceImportError as exc:
        return _err_response(str(exc))

    summary['success'] = True
    if is_ajax:
        return jsonify(summary)

    flash(
        f"Importação concluída: {summary['attendances_created']} registro(s) de "
        f"ausência criado(s), {summary['attendances_updated']} atualizado(s), "
        f"{summary['employees_created']} funcionário(s) inativo(s) criado(s), "
        f"{summary['skipped_rows']} linha(s) ignorada(s).",
        'success',
    )
    return redirect(url_for('admin.admin_home'))


def _upload_dir():
    """Directory used to stage uploaded files awaiting confirmation."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _upload_path(token):
    return os.path.join(_upload_dir(), f'upload_{token}.xlsx')


def _clear_upload(token):
    """Remove a staged temp file and clear its session token."""
    path = _upload_path(token)
    if os.path.exists(path):
        os.remove(path)
    if session.get('upload_token') == token:
        session.pop('upload_token', None)


@admin_bp.route('/upload/analyze', methods=['POST'])
@staff_required
def upload_analyze():
    """Dry-run: stage the file and return a preview summary WITHOUT writing to DB."""
    file = request.files.get('excel_file')
    if not file or not file.filename:
        return jsonify({'status': 'error', 'message': 'Nenhum arquivo selecionado.'}), 400

    if not file.filename.endswith('.xlsx'):
        return jsonify({'status': 'error', 'message': 'Formato inválido. Envie um arquivo .xlsx.'}), 400

    token = uuid.uuid4().hex
    temp_path = _upload_path(token)
    file.save(temp_path)
    session['upload_token'] = token

    try:
        summary = analyze_excel_upload(temp_path)
    except ValueError as e:
        _clear_upload(token)
        return jsonify({'status': 'error', 'message': f'Erro no arquivo: {e}'}), 400
    except Exception as e:
        _clear_upload(token)
        return jsonify({'status': 'error', 'message': f'Erro ao processar arquivo: {e}'}), 500

    # Guarantee no partial writes leak into a subsequent transaction.
    db.session.rollback()

    return jsonify({'status': 'success', 'summary': summary})


@admin_bp.route('/upload/confirm', methods=['POST'])
@staff_required
def upload_confirm():
    """Commit the previously analyzed file to the database."""
    token = session.get('upload_token')
    if not token:
        return jsonify({'status': 'error', 'message': 'Nenhuma planilha aguardando confirmação.'}), 400

    temp_path = _upload_path(token)
    if not os.path.exists(temp_path):
        return jsonify({'status': 'error', 'message': 'Sessão de análise expirada. Reenvie a planilha.'}), 400

    payload = request.get_json(silent=True) or {}

    # Execute confirmed matricula migrations before importing allocations.
    for m in payload.get('migrations', []):
        try:
            migrate_employee_id(m.get('old_id'), m.get('new_id'))
        except ValueError as e:
            return jsonify({'status': 'error', 'message': str(e)}), 400

    try:
        result = process_excel_upload(temp_path, user_id=current_user.id)
        session['upload_summary'] = result
        flash('Importação confirmada com sucesso.', 'success')
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Erro ao importar: {e}'}), 500
    finally:
        _clear_upload(token)

    return jsonify({'status': 'success', 'summary': result})


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
@admin_only_required
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
@admin_only_required
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
@admin_only_required
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
@admin_only_required
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
@staff_required
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
@staff_required
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
@staff_required
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
@staff_required
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
@staff_required
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
@staff_required
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
@staff_required
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
@staff_required
def lines_toggle(line_id):
    """Activate or deactivate a line."""
    line = Line.query.get_or_404(line_id)
    line.is_active = not line.is_active
    db.session.commit()
    status = 'ativada' if line.is_active else 'desativada'
    flash(f'Linha "{line.name}" {status}.', 'success')
    return redirect(url_for('admin.lines'))


# ─────────────────── COMPANY CALENDAR (HOLIDAYS / BRIDGE DAYS) ───────────────────

_CALENDAR_TYPE_LABELS = {
    'FERIADO': 'Feriado',
    'FOLGA_COMPENSADA': 'Folga Compensada (ponte)',
    'SABADO_LETIVO': 'Sábado Letivo',
}


@admin_bp.route('/calendar', methods=['GET'])
@staff_required
def calendar():
    """Company calendar management: mark/unmark holidays, bridge days and make-up Saturdays."""
    today = date.today()
    try:
        year = int(request.args.get('year', today.year))
        month = int(request.args.get('month', today.month))
    except (ValueError, TypeError):
        year, month = today.year, today.month
    if not (1 <= month <= 12):
        month = today.month
    if year < 2000 or year > 2100:
        year = today.year

    first_weekday, days_in_month = pycalendar.monthrange(year, month)

    entries = CompanyCalendar.query.filter(
        CompanyCalendar.date >= date(year, month, 1),
        CompanyCalendar.date <= date(year, month, days_in_month)
    ).order_by(CompanyCalendar.date).all()
    entry_map = {e.date: e for e in entries}

    weeks = []
    week = []
    for _ in range(first_weekday):
        week.append(None)
    for day in range(1, days_in_month + 1):
        week.append(day)
        if len(week) == 7:
            weeks.append(week)
            week = []
    if week:
        while len(week) < 7:
            week.append(None)
        weeks.append(week)

    # Previous / next month navigation
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year
    if month == 12:
        next_month, next_year = 1, year + 1
    else:
        next_month, next_year = month + 1, year

    return render_template(
        'admin/calendar.html',
        year=year,
        month=month,
        today=today,
        weeks=weeks,
        entry_map=entry_map,
        type_labels=_CALENDAR_TYPE_LABELS,
        month_names=pycalendar.month_name,
        prev_year=prev_year,
        prev_month=prev_month,
        next_year=next_year,
        next_month=next_month,
    )


@admin_bp.route('/calendar/add', methods=['POST'])
@staff_required
def calendar_add():
    """Add or update a calendar exception for a given date (unique per date)."""
    date_str = request.form.get('date', '').strip()
    cal_type = request.form.get('type', '').strip()
    description = request.form.get('description', '').strip()

    back = url_for('admin.calendar')

    try:
        rec_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        flash('Data inválida. Use o formato AAAA-MM-DD.', 'danger')
        return redirect(back)

    if cal_type not in _CALENDAR_TYPE_LABELS:
        flash('Tipo de calendário inválido.', 'danger')
        return redirect(back)

    entry = CompanyCalendar.query.filter_by(date=rec_date).first()
    if entry:
        entry.type = cal_type
        entry.description = description or None
        db.session.commit()
        flash(f'Exceção atualizada para {rec_date.isoformat()}.', 'success')
    else:
        db.session.add(CompanyCalendar(
            date=rec_date,
            type=cal_type,
            description=description or None
        ))
        db.session.commit()
        flash(f'Exceção de calendário adicionada em {rec_date.isoformat()}.', 'success')

    return redirect(url_for('admin.calendar', year=rec_date.year, month=rec_date.month))


@admin_bp.route('/calendar/remove', methods=['POST'])
@staff_required
def calendar_remove():
    """Remove a calendar exception for a given date."""
    date_str = request.form.get('date', '').strip()
    try:
        rec_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        flash('Data inválida.', 'danger')
        return redirect(url_for('admin.calendar'))

    entry = CompanyCalendar.query.filter_by(date=rec_date).first()
    if entry:
        db.session.delete(entry)
        db.session.commit()
        flash(f'Exceção removida para {rec_date.isoformat()}.', 'success')
    else:
        flash('Nenhuma exceção encontrada para esta data.', 'warning')

    return redirect(url_for('admin.calendar', year=rec_date.year, month=rec_date.month))



@admin_bp.route('/calendar/sync-official', methods=['POST'])
@staff_required
def calendar_sync_official():
    """Reload official national/state/municipal holidays for the configured years."""
    from services.calendar_service import seed_official_holidays
    added = seed_official_holidays()
    if added:
        flash(f'{added} feriado(s) oficial(is) carregado(s) no calendário da empresa.', 'success')
    else:
        flash('Calendário já estava atualizado (nenhum feriado novo).', 'info')
    return redirect(url_for('admin.calendar'))



# ─────────────────── EMPLOYEE VACATION MANAGEMENT ───────────────────

@admin_bp.route('/employees', methods=['GET'])
@staff_required
def employees():
    """Employee management view: list with search + line/shift filters, CRUD."""
    search = request.args.get('search', '').strip()
    line_filter = request.args.get('line', '').strip()
    shift_filter = request.args.get('shift', '').strip()
    status_filter = request.args.get('status', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 50

    query = Employee.query
    if search:
        pattern = f'%{search}%'
        query = query.filter(
            db.or_(Employee.id.ilike(pattern), Employee.name.ilike(pattern))
        )
    if line_filter:
        try:
            query = query.filter(Employee.line_id == int(line_filter))
        except ValueError:
            pass
    if shift_filter:
        try:
            query = query.filter(Employee.shift_id == int(shift_filter))
        except ValueError:
            pass
    if status_filter == 'active':
        query = query.filter(Employee.is_active.is_(True))
    elif status_filter == 'inactive':
        query = query.filter(Employee.is_active.is_(False))

    pagination = query.order_by(Employee.name).paginate(
        page=page, per_page=per_page, error_out=False
    )

    lines = Line.query.filter_by(is_active=True).order_by(Line.project, Line.name).all()
    shifts = Shift.query.filter_by(is_active=True).order_by(Shift.id).all()

    return render_template(
        'admin/employees.html',
        pagination=pagination,
        search=search,
        line_filter=line_filter,
        shift_filter=shift_filter,
        status_filter=status_filter,
        lines=lines,
        shifts=shifts,
    )


@admin_bp.route('/employees/create', methods=['POST'])
@staff_required
def employee_create():
    """Create a new employee and provision its active Allocation."""
    emp_id = request.form.get('id', '').strip()
    name = request.form.get('name', '').strip()
    line_id = request.form.get('line_id', '').strip()
    shift_id = request.form.get('shift_id', '').strip()

    if not emp_id or not name:
        flash('Matrícula e nome são obrigatórios.', 'warning')
        return redirect(url_for('admin.employees'))

    if Employee.query.get(emp_id):
        flash(f'Já existe um funcionário com a matrícula {emp_id}.', 'danger')
        return redirect(url_for('admin.employees'))

    line = Line.query.get(int(line_id)) if line_id.isdigit() else None
    shift = Shift.query.get(int(shift_id)) if shift_id.isdigit() else None
    if not line or not shift:
        flash('Linha e turno são obrigatórios.', 'warning')
        return redirect(url_for('admin.employees'))

    emp = Employee(
        id=emp_id,
        name=name,
        status='ACTIVE',
        is_active=True,
        line_id=line.id,
        shift_id=shift.id,
        project_id=None,
    )
    db.session.add(emp)
    db.session.flush()
    emp.sync_allocation(commit=False)
    db.session.commit()

    flash(f'Funcionário {name} criado com sucesso.', 'success')
    return redirect(url_for('admin.employees'))


@admin_bp.route('/employees/<employee_id>/edit', methods=['POST'])
@staff_required
def employee_edit(employee_id):
    """Update an employee's name, line, shift and status (syncs active Allocation)."""
    emp = Employee.query.get_or_404(employee_id)

    name = request.form.get('name', '').strip()
    line_id = request.form.get('line_id', '').strip()
    shift_id = request.form.get('shift_id', '').strip()
    is_active = request.form.get('is_active')

    if name:
        emp.name = name

    line = Line.query.get(int(line_id)) if line_id.isdigit() else None
    if line:
        emp.line_id = line.id
    shift = Shift.query.get(int(shift_id)) if shift_id.isdigit() else None
    if shift:
        emp.shift_id = shift.id

    if is_active is not None:
        emp.is_active = (is_active == 'on')
    emp.status = 'ACTIVE' if emp.is_active else 'INACTIVE'

    emp.sync_allocation(commit=False)
    db.session.commit()

    flash(f'Dados de {emp.name} atualizados.', 'success')
    return redirect(url_for('admin.employees'))


@admin_bp.route('/employees/<employee_id>/toggle-status', methods=['POST'])
@staff_required
def employee_toggle_status(employee_id):
    """Soft-delete / inactivate an employee, preserving historical attendance records."""
    emp = Employee.query.get_or_404(employee_id)
    emp.is_active = not emp.is_active
    emp.status = 'ACTIVE' if emp.is_active else 'INACTIVE'
    db.session.commit()

    if emp.is_active:
        flash(f'Funcionário {emp.name} reativado.', 'success')
    else:
        flash(f'Funcionário {emp.name} inativado. O histórico de apontamentos foi preservado.', 'info')
    return redirect(url_for('admin.employees'))



@admin_bp.route('/employees/<employee_id>/vacation', methods=['POST'])
@staff_required
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


@admin_bp.route('/employees/<employee_id>/migrate-id', methods=['POST'])
@staff_required
def employee_migrate_id(employee_id):
    """Migrate an employee's registration number (matrícula) to a new ID."""
    new_id = request.form.get('new_id', '').strip()
    try:
        new_emp = migrate_employee_id(employee_id, new_id)
        flash(
            f'Matrícula de {new_emp.name} alterada de {employee_id} para {new_emp.id} com sucesso.',
            'success'
        )
    except ValueError as e:
        flash(str(e), 'danger')

    back = url_for('admin.employees', search=request.args.get('search', ''))
    return redirect(back)
