import math
from flask import Blueprint, render_template, request, send_file, current_app
from flask_login import login_required
from extensions import db
from sqlalchemy import func, case
from models.employee import Employee
from models.allocation import Allocation
from models.attendance import Attendance
from services.excel_service import export_absence_report
from datetime import date, datetime, timedelta
import io

reports_bp = Blueprint('reports', __name__, url_prefix='/reports')


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

    event_types = ['FULL_ABSENCE', 'LATE_ARRIVAL', 'EARLY_EXIT', 'VACATION']

    # Build the paginated query
    base_query = db.session.query(Attendance, Employee, Allocation).join(
        Employee, Attendance.employee_id == Employee.id
    ).join(
        Allocation, Attendance.allocation_id == Allocation.id
    ).filter(
        Attendance.record_date >= start,
        Attendance.record_date <= end,
        Attendance.event_type != 'PRESENT'  # Only absence/event records
    )

    if shift:
        base_query = base_query.filter(Allocation.shift == int(shift))
    if project:
        base_query = base_query.filter(Allocation.project == project)
    if line:
        base_query = base_query.filter(Allocation.line == line)
    if event_type:
        base_query = base_query.filter(Attendance.event_type == event_type)
    if search:
        search_pattern = f'%{search}%'
        base_query = base_query.filter(
            db.or_(
                Employee.id.ilike(search_pattern),
                Employee.name.ilike(search_pattern)
            )
        )

    # Order by date descending (most recent first)
    pagination = base_query.order_by(
        Attendance.record_date.desc(), Employee.name
    ).paginate(page=page, per_page=per_page, error_out=False)

    # Build records list
    records = []
    event_labels = {
        'FULL_ABSENCE': 'Falta',
        'LATE_ARRIVAL': 'Atraso',
        'EARLY_EXIT': 'Saída Antecipada',
        'VACATION': 'Férias'
    }
    event_colors = {
        'FULL_ABSENCE': 'danger',
        'LATE_ARRIVAL': 'warning',
        'EARLY_EXIT': 'info',
        'VACATION': 'warning text-dark'
    }

    for att, emp, alloc in pagination.items:
        label = event_labels.get(att.event_type, att.event_type)
        color = event_colors.get(att.event_type, 'secondary')
        records.append({
            'record_date': att.record_date.isoformat() if att.record_date else '',
            'employee_id': emp.id,
            'employee_name': emp.name,
            'shift': alloc.shift,
            'project': alloc.project,
            'line': alloc.line,
            'event_type': att.event_type,
            'event_label': label,
            'event_color': color,
            'check_in_time': att.check_in_time.isoformat() if att.check_in_time else '',
            'check_out_time': att.check_out_time.isoformat() if att.check_out_time else '',
            'minutes_lost': att.minutes_lost or 0
        })

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
    """Export filtered absence records to Excel (.xlsx)."""
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

    # Build same filters as index
    base_query = db.session.query(Attendance, Employee, Allocation).join(
        Employee, Attendance.employee_id == Employee.id
    ).join(
        Allocation, Attendance.allocation_id == Allocation.id
    ).filter(
        Attendance.record_date >= start,
        Attendance.record_date <= end,
        Attendance.event_type != 'PRESENT'
    )

    if shift:
        base_query = base_query.filter(Allocation.shift == int(shift))
    if project:
        base_query = base_query.filter(Allocation.project == project)
    if line:
        base_query = base_query.filter(Allocation.line == line)
    if event_type:
        base_query = base_query.filter(Attendance.event_type == event_type)
    if search:
        search_pattern = f'%{search}%'
        base_query = base_query.filter(
            db.or_(
                Employee.id.ilike(search_pattern),
                Employee.name.ilike(search_pattern)
            )
        )

    rows = base_query.order_by(Attendance.record_date.desc(), Employee.name).all()

    # Use excel_service to generate the file
    try:
        buffer = export_absence_report(rows)
    except Exception:
        # Fallback: minimal CSV export if excel_service fails
        import csv
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(['Data', 'ID', 'Nome', 'Turno', 'Projeto', 'Linha', 'Tipo', 'Entrada', 'Saída', 'Minutos Perdidos'])
        event_labels_local = {
            'FULL_ABSENCE': 'Falta', 'LATE_ARRIVAL': 'Atraso',
            'EARLY_EXIT': 'Saída Antecipada', 'VACATION': 'Férias'
        }
        for att, emp, alloc in rows:
            writer.writerow([
                att.record_date.isoformat() if att.record_date else '',
                emp.id, emp.name, alloc.shift, alloc.project, alloc.line,
                event_labels_local.get(att.event_type, att.event_type),
                att.check_in_time.isoformat() if att.check_in_time else '',
                att.check_out_time.isoformat() if att.check_out_time else '',
                att.minutes_lost or 0
            ])
        buffer.seek(0)
        return send_file(
            io.BytesIO(buffer.getvalue().encode('utf-8-sig')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'relatorio_ausencias_{start.isoformat()}_a_{end.isoformat()}.csv'
        )

    return send_file(
        buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'relatorio_ausencias_{start.isoformat()}_a_{end.isoformat()}.xlsx'
    )