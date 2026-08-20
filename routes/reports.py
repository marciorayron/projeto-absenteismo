import math
from flask import Blueprint, render_template, request, send_file, current_app
from flask_login import login_required
from extensions import db
from sqlalchemy import func, case
from types import SimpleNamespace
from models.employee import Employee
from models.allocation import Allocation
from models.attendance import Attendance
from models.line import Line
from models.transfer import EmployeeMovementLog
from datetime import date, datetime, timedelta
import io
import pandas as pd

reports_bp = Blueprint('reports', __name__, url_prefix='/reports')

_ATTENDANCE_EVENT_TYPES = {'FULL_ABSENCE', 'LATE_ARRIVAL', 'EARLY_EXIT', 'VACATION'}

_EVENT_LABELS = {
    'FULL_ABSENCE': 'Falta',
    'LATE_ARRIVAL': 'Atraso',
    'EARLY_EXIT': 'Saída Antecipada',
    'VACATION': 'Férias',
    'ATESTADO': 'Atestado',
    'FALTA': 'Falta (importada)',
    'SITUACAO_LEGAL': 'Situação Legal',
    'SUSPENSAO': 'Suspensão',
}

_EVENT_COLORS = {
    'FULL_ABSENCE': 'danger',
    'LATE_ARRIVAL': 'warning',
    'EARLY_EXIT': 'info',
    'VACATION': 'warning text-dark',
    'ATESTADO': 'info',
    'FALTA': 'danger',
    'SITUACAO_LEGAL': 'warning',
    'SUSPENSAO': 'dark',
}


def _attendance_records(start, end, shift, project, line, event_type, search):
    """Return normalized absence records from Attendance matching the filters."""
    query = db.session.query(Attendance, Employee, Allocation).join(
        Employee, Attendance.employee_id == Employee.id
    ).join(
        Allocation, Attendance.allocation_id == Allocation.id
    ).filter(
        Attendance.record_date >= start,
        Attendance.record_date <= end,
        Attendance.event_type != 'PRESENT'  # Only absence/event records
    )

    if shift:
        query = query.filter(Allocation.shift == int(shift))
    if project:
        query = query.filter(Allocation.project == project)
    if line:
        query = query.filter(Allocation.line == line)
    if event_type:
        if event_type in _ATTENDANCE_EVENT_TYPES:
            query = query.filter(Attendance.event_type == event_type)
        else:
            return []  # an absence-type filter was selected; no attendance rows match
    if search:
        search_pattern = f'%{search}%'
        query = query.filter(
            db.or_(
                Employee.id.ilike(search_pattern),
                Employee.name.ilike(search_pattern)
            )
        )

    records = []
    for att, emp, alloc in query.all():
        records.append({
            'record_date': att.record_date.isoformat() if att.record_date else '',
            'employee_id': emp.id,
            'employee_name': emp.name,
            'shift': f"Turno {alloc.shift}" if alloc.shift is not None else '—',
            'project': alloc.project,
            'line': alloc.line,
            'event_type': att.event_type,
            'event_label': _EVENT_LABELS.get(att.event_type, att.event_type),
            'event_color': _EVENT_COLORS.get(att.event_type, 'secondary'),
            'check_in_time': att.check_in_time.isoformat() if att.check_in_time else '',
            'check_out_time': att.check_out_time.isoformat() if att.check_out_time else '',
            'minutes_lost': att.minutes_lost or 0,
            'source': 'ATTENDANCE',
        })
    return records


@reports_bp.route('/')
@login_required
def index():
    """Relatórios de Ausências — lista paginada e filtrável de todos os registros de ausência."""
    date_to = request.args.get('date_to', date.today().isoformat())
    date_from = request.args.get('date_from', (date.today() - timedelta(days=30)).isoformat())
    shift = request.args.get('shift', '')
    project = request.args.get('project', '')
    line = request.args.get('line', '')
    event_type = request.args.get('event_type', '')
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int)

    try:
        start = datetime.strptime(date_from, '%Y-%m-%d').date()
        end = datetime.strptime(date_to, '%Y-%m-%d').date()
    except ValueError:
        start = date.today() - timedelta(days=30)
        end = date.today()

    # Get filter options
    shifts_rows = db.session.query(Allocation.shift).distinct().order_by(Allocation.shift).all()
    shifts = [s[0] for s in shifts_rows if s[0] is not None]

    projects_rows = db.session.query(Allocation.project).distinct().all()
    projects = sorted([p[0] for p in projects_rows if p[0]])

    lines_rows = db.session.query(Allocation.line).distinct().all()
    lines = sorted([l[0] for l in lines_rows if l[0]])

    event_types = ['FULL_ABSENCE', 'LATE_ARRIVAL', 'EARLY_EXIT', 'VACATION',
                   'ATESTADO', 'FALTA', 'SITUACAO_LEGAL', 'SUSPENSAO']

    # Build the normalized Attendance records, then paginate in Python.
    records_all = _attendance_records(start, end, shift, project, line, event_type, search)
    records_all.sort(key=lambda r: r['record_date'], reverse=True)

    total = len(records_all)
    pages = max(1, math.ceil(total / per_page))
    page = max(1, min(page, pages))
    offset = (page - 1) * per_page
    records = records_all[offset:offset + per_page]

    pagination = SimpleNamespace(
        items=records, total=total, page=page, pages=pages, per_page=per_page
    )

    event_labels = _EVENT_LABELS

    return render_template(
        'reports/index.html',
        records=records,
        pagination=pagination,
        date_from=date_from,
        date_to=date_to,
        selected_shift=shift,
        selected_project=project,
        selected_line=line,
        selected_event_type=event_type,
        search=search,
        shifts=shifts,
        projects=projects,
        lines=lines,
        event_types=event_types,
        event_labels=event_labels
    )


@reports_bp.route('/export-excel')
@login_required
def export_excel():
    """Export filtered absence records (Attendance) to Excel (.xlsx)."""
    date_to = request.args.get('date_to', date.today().isoformat())
    date_from = request.args.get('date_from', (date.today() - timedelta(days=30)).isoformat())
    shift = request.args.get('shift', '')
    project = request.args.get('project', '')
    line = request.args.get('line', '')
    event_type = request.args.get('event_type', '')
    search = request.args.get('search', '').strip()

    try:
        start = datetime.strptime(date_from, '%Y-%m-%d').date()
        end = datetime.strptime(date_to, '%Y-%m-%d').date()
    except ValueError:
        start = date.today() - timedelta(days=30)
        end = date.today()

    # Combine the same normalized records shown on the reports page.
    records = _attendance_records(start, end, shift, project, line, event_type, search)
    records.sort(key=lambda r: r['record_date'], reverse=True)

    data = [{
        'Data': r['record_date'],
        'ID Funcionário': r['employee_id'],
        'Nome': r['employee_name'],
        'Turno': r['shift'] if r['shift'] is not None else '',
        'Projeto': r['project'] or '',
        'Linha': r['line'] or '',
        'Tipo': r['event_label'],
        'Minutos Perdidos': r['minutes_lost'],
        'Fonte': r['source'],
    } for r in records]

    df = pd.DataFrame(data)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Ausências', index=False)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'relatorio_ausencias_{start.isoformat()}_a_{end.isoformat()}.xlsx'
    )


def _movement_filter_query():
    """Build the filtered EmployeeMovementLog query from request args."""
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    origin_line = request.args.get('origin_line', '').strip()
    target_line = request.args.get('target_line', '').strip()
    employee = request.args.get('employee', '').strip()

    query = db.session.query(EmployeeMovementLog)
    if date_from:
        try:
            start = datetime.strptime(date_from, '%Y-%m-%d')
            query = query.filter(EmployeeMovementLog.timestamp >= start)
        except ValueError:
            pass
    if date_to:
        try:
            end = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1) - timedelta(seconds=1)
            query = query.filter(EmployeeMovementLog.timestamp <= end)
        except ValueError:
            pass
    if origin_line:
        try:
            query = query.filter(EmployeeMovementLog.origin_line_id == int(origin_line))
        except ValueError:
            pass
    if target_line:
        try:
            query = query.filter(EmployeeMovementLog.target_line_id == int(target_line))
        except ValueError:
            pass
    if employee:
        query = query.filter(EmployeeMovementLog.employee_id == employee)

    return query.order_by(EmployeeMovementLog.timestamp.desc())


@reports_bp.route('/transfers')
@login_required
def transfers():
    """Movement history report from EmployeeMovementLog with filters."""
    lines = Line.query.filter_by(is_active=True).order_by(Line.project, Line.name).all()
    query = _movement_filter_query()
    logs = query.all()

    return render_template(
        'reports/transfers.html',
        logs=logs,
        lines=lines,
        date_from=request.args.get('date_from', ''),
        date_to=request.args.get('date_to', ''),
        origin_line=request.args.get('origin_line', ''),
        target_line=request.args.get('target_line', ''),
        employee=request.args.get('employee', ''),
    )


@reports_bp.route('/transfers/export')
@login_required
def transfers_export():
    """Export the movement history to Excel (.xlsx)."""
    query = _movement_filter_query()
    logs = query.all()

    rows = []
    for log in logs:
        rows.append({
            'Data': log.timestamp.strftime('%d/%m/%Y %H:%M') if log.timestamp else '',
            'ID Funcionário': log.employee_id,
            'Nome': log.employee.name if log.employee else '',
            'Linha Origem': log.origin_line.name if log.origin_line else '—',
            'Turno Origem': log.origin_shift_id,
            'Linha Destino': log.target_line.name if log.target_line else '',
            'Projeto Destino': log.target_line.project if log.target_line else '',
            'Turno Destino': log.target_shift_id,
            'Aprovado por': log.approved_by.username if log.approved_by else '',
        })

    df = pd.DataFrame(rows)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Movimentações', index=False)
    buffer.seek(0)

    return send_file(
        buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='historico_movimentacoes.xlsx'
    )