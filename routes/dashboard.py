from flask import Blueprint, render_template, jsonify, request, current_app
from flask_login import login_required, current_user
from extensions import db
from models.employee import Employee
from models.allocation import Allocation
from models.attendance import Attendance
from models.line_validation import LineValidation
from services.metrics_service import calculate_bradford_bulk
from datetime import date, datetime, timedelta

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')


def _parse_date_range():
    """Extract and validate date_from and date_to from request args."""
    date_to = request.args.get('date_to', date.today().isoformat())
    date_from = request.args.get('date_from', (date.today() - timedelta(days=30)).isoformat())
    try:
        start = datetime.strptime(date_from, '%Y-%m-%d').date()
        end = datetime.strptime(date_to, '%Y-%m-%d').date()
    except ValueError:
        return None, None, jsonify({'error': 'Datas inválidas'}), 400
    return start, end, None, None


def _get_filter_params():
    """Extract optional shift, project, line filter params as lists (multi-value)."""
    shifts = request.args.getlist('shift')
    projects = request.args.getlist('project')
    lines = request.args.getlist('line')
    shifts = [s for s in shifts if s]
    projects = [p for p in projects if p]
    lines = [l for l in lines if l]
    return shifts, projects, lines


def _build_allocation_filter(query, shifts, projects, lines):
    """Apply multi-value allocation filters to a query. Returns the modified query."""
    if shifts:
        query = query.filter(Allocation.shift.in_([int(s) for s in shifts]))
    if projects:
        query = query.filter(Allocation.project.in_(projects))
    if lines:
        query = query.filter(Allocation.line.in_(lines))
    return query


def _get_filtered_emp_ids(shifts, projects, lines):
    """Return list of employee_ids matching the optional allocation filters (multi-value)."""
    query = db.session.query(Allocation.employee_id).filter(Allocation.end_date.is_(None))
    query = _build_allocation_filter(query, shifts, projects, lines)
    return [row[0] for row in query.distinct().all()]


def _has_filters(shifts, projects, lines):
    """Return True if any filter is active."""
    return bool(shifts or projects or lines)


def _build_attendance_subquery_label(start, end, emp_ids, line_label=None, project_label=None, shift_label=None):
    """
    Build the core subquery to join Allocation + Attendance for GROUP BY aggregations.
    Returns a SQLAlchemy subquery with columns for the grouping label.
    """
    cols = [
        Allocation.line,
        Allocation.shift,
        Allocation.project,
        Attendance.employee_id,
        Attendance.record_date,
        Attendance.event_type,
        Attendance.minutes_lost
    ]
    query = db.session.query(*cols).join(
        Attendance, Attendance.allocation_id == Allocation.id
    ).filter(
        Allocation.end_date.is_(None),
        Attendance.record_date >= start,
        Attendance.record_date <= end
    )
    if emp_ids is not None:
        query = query.filter(Attendance.employee_id.in_(emp_ids))
    return query.subquery()


@dashboard_bp.route('/')
@login_required
def index():
    """Analytics dashboard view."""
    return render_template('admin/dashboard.html')


@dashboard_bp.route('/api/filter-options')
@login_required
def api_filter_options():
    """Return distinct shift, project, and line values for filter dropdowns.
       Accepts query params ?shifts=1,2&projects=... to cascade filter options."""
    selected_shifts = request.args.getlist('shifts')
    selected_projects = request.args.getlist('projects')

    # Shifts
    shifts_query = db.session.query(Allocation.shift)
    if selected_shifts:
        shifts_query = shifts_query.filter(Allocation.shift.in_([int(s) for s in selected_shifts if s]))
    shifts_rows = shifts_query.filter(Allocation.end_date.is_(None)).distinct().order_by(Allocation.shift).all()
    shifts = [s[0] for s in shifts_rows if s[0] is not None]

    # Projects
    projects_query = db.session.query(Allocation.project).filter(Allocation.end_date.is_(None))
    if selected_shifts:
        projects_query = projects_query.filter(Allocation.shift.in_([int(s) for s in selected_shifts if s]))
    projects_rows = projects_query.distinct().order_by(Allocation.project).all()
    projects = sorted([p[0] for p in projects_rows if p[0]])

    # Lines
    lines_query = db.session.query(Allocation.line).filter(Allocation.end_date.is_(None))
    if selected_shifts:
        lines_query = lines_query.filter(Allocation.shift.in_([int(s) for s in selected_shifts if s]))
    if selected_projects:
        lines_query = lines_query.filter(Allocation.project.in_(selected_projects))
    lines_rows = lines_query.distinct().order_by(Allocation.line).all()
    lines = sorted([l[0] for l in lines_rows if l[0]])

    return jsonify({
        'shifts': shifts,
        'projects': projects,
        'lines': lines
    })


@dashboard_bp.route('/api/overview')
@login_required
def api_overview():
    """Overview metrics: total employees, absenteeism %, lost hours, etc. — fully SQL-aggregated."""
    start, end, error, code = _parse_date_range()
    if error:
        return error, code

    shifts, projects, lines = _get_filter_params()
    has_filters = _has_filters(shifts, projects, lines)

    # Total active employees
    if has_filters:
        emp_ids = _get_filtered_emp_ids(shifts, projects, lines)
        total_employees = len(emp_ids)
        if not emp_ids:
            return jsonify({
                'total_employees': 0, 'total_records': 0, 'absent_employees': 0,
                'absent_records': 0, 'absenteeism_rate': 0,
                'total_lost_minutes': 0, 'total_lost_hours': 0,
                'date_from': start.isoformat(), 'date_to': end.isoformat(),
                'pending_validations': 0
            })
    else:
        total_employees = Employee.query.filter_by(status='ACTIVE').count()
        emp_ids = None

    # Build base attendance filter
    att_base = db.session.query(Attendance).filter(
        Attendance.record_date >= start,
        Attendance.record_date <= end
    )
    if emp_ids is not None:
        att_base = att_base.filter(Attendance.employee_id.in_(emp_ids))

    total_records_subq = att_base.subquery()

    # Total records count
    total_records = db.session.query(db.func.count()).select_from(total_records_subq).scalar() or 0

    # Aggregate all metrics in a single query using conditional aggregation
    agg = db.session.query(
        db.func.count(db.distinct(Attendance.employee_id)).filter(Attendance.minutes_lost > 0).label('absent_employees'),
        db.func.count(Attendance.id).filter(Attendance.minutes_lost > 0).label('absent_records'),
        db.func.coalesce(db.func.sum(Attendance.minutes_lost), 0).label('total_lost_minutes')
    ).filter(
        Attendance.record_date >= start,
        Attendance.record_date <= end
    )
    if emp_ids is not None:
        agg = agg.filter(Attendance.employee_id.in_(emp_ids))

    agg_row = agg.first()
    absent_employees = agg_row.absent_employees or 0
    absent_records = agg_row.absent_records or 0
    total_lost_minutes = agg_row.total_lost_minutes or 0

    absenteeism_rate = round((absent_employees / total_employees * 100), 2) if total_employees > 0 else 0

    # --- Pending validations: SQL-side aggregation ---
    # Count line/shift/date combos from Allocation+Attendance that have no LineValidation
    pending = db.session.query(db.func.count(db.distinct(
        db.func.concat(Allocation.line, '|', Allocation.shift, '|', Attendance.record_date)
    ))).join(
        Attendance, Attendance.allocation_id == Allocation.id
    ).filter(
        Allocation.end_date.is_(None),
        Allocation.line.isnot(None),
        Allocation.line != '',
        Attendance.record_date >= start,
        Attendance.record_date <= end
    ).outerjoin(
        LineValidation,
        db.and_(
            LineValidation.record_date == Attendance.record_date,
            LineValidation.line == Allocation.line,
            LineValidation.shift == Allocation.shift
        )
    ).filter(
        LineValidation.id.is_(None)  # No matching validation
    )
    if emp_ids is not None:
        pending = pending.filter(Allocation.employee_id.in_(emp_ids))
    pending = pending.scalar() or 0

    return jsonify({
        'total_employees': total_employees,
        'total_records': total_records,
        'absent_records': absent_records,
        'absent_employees': absent_employees,
        'absenteeism_rate': absenteeism_rate,
        'total_lost_minutes': total_lost_minutes,
        'total_lost_hours': round(total_lost_minutes / 60, 2),
        'date_from': start.isoformat(),
        'date_to': end.isoformat(),
        'pending_validations': pending
    })


@dashboard_bp.route('/api/by-line')
@login_required
def api_by_line():
    """Breakdown of absenteeism by production line — single SQL GROUP BY query."""
    start, end, error, code = _parse_date_range()
    if error:
        return error, code

    shifts, projects, lines_filter = _get_filter_params()

    # Build the allocation filter for the subquery
    emp_ids = None
    if _has_filters(shifts, projects, lines_filter):
        emp_ids = _get_filtered_emp_ids(shifts, projects, lines_filter)
        if not emp_ids:
            return jsonify({'lines': []})

    # Single aggregated query: GROUP BY Allocation.line
    agg_query = db.session.query(
        Allocation.line,
        db.func.count(db.distinct(Allocation.employee_id)).label('headcount'),
        db.func.count(Attendance.id).label('total_records'),
        db.func.coalesce(db.func.sum(Attendance.minutes_lost), 0).label('lost_minutes'),
        db.func.sum(
            db.case((Attendance.minutes_lost > 0, 1), else_=0)
        ).label('absent_records')
    ).join(
        Attendance, Attendance.allocation_id == Allocation.id
    ).filter(
        Allocation.end_date.is_(None),
        Allocation.line.isnot(None),
        Allocation.line != '',
        Attendance.record_date >= start,
        Attendance.record_date <= end
    )

    if emp_ids is not None:
        agg_query = agg_query.filter(Allocation.employee_id.in_(emp_ids))
    if lines_filter:
        agg_query = agg_query.filter(Allocation.line.in_(lines_filter))

    agg_query = agg_query.group_by(Allocation.line).all()

    result = []
    for line, headcount, total_records, lost_minutes, absent_records in agg_query:
        lost_minutes = lost_minutes or 0
        headcount = headcount or 0
        total_records = total_records or 0
        absent_records = absent_records or 0
        rate = round(absent_records / total_records * 100, 2) if total_records > 0 else 0

        result.append({
            'line': line,
            'headcount': headcount,
            'total_records': total_records,
            'absent_records': absent_records,
            'lost_minutes': lost_minutes,
            'lost_hours': round(lost_minutes / 60, 2),
            'absenteeism_rate': rate
        })

    result.sort(key=lambda x: x['absenteeism_rate'], reverse=True)
    return jsonify({'lines': result})


@dashboard_bp.route('/api/by-project')
@login_required
def api_by_project():
    """Breakdown of absenteeism by project — single SQL GROUP BY query."""
    start, end, error, code = _parse_date_range()
    if error:
        return error, code

    shifts, projects_filter, lines_filter = _get_filter_params()

    emp_ids = None
    if _has_filters(shifts, [], lines_filter) or lines_filter:
        emp_ids = _get_filtered_emp_ids(shifts, [], lines_filter)
        if emp_ids is not None and not emp_ids:
            return jsonify({'projects': []})

    agg_query = db.session.query(
        Allocation.project,
        db.func.count(db.distinct(Allocation.employee_id)).label('headcount'),
        db.func.count(Attendance.id).label('total_records'),
        db.func.coalesce(db.func.sum(Attendance.minutes_lost), 0).label('lost_minutes'),
        db.func.sum(
            db.case((Attendance.minutes_lost > 0, 1), else_=0)
        ).label('absent_records')
    ).join(
        Attendance, Attendance.allocation_id == Allocation.id
    ).filter(
        Allocation.end_date.is_(None),
        Allocation.project.isnot(None),
        Allocation.project != '',
        Attendance.record_date >= start,
        Attendance.record_date <= end
    )

    if emp_ids is not None:
        agg_query = agg_query.filter(Allocation.employee_id.in_(emp_ids))
    if projects_filter:
        agg_query = agg_query.filter(Allocation.project.in_(projects_filter))
    if lines_filter:
        agg_query = agg_query.filter(Allocation.line.in_(lines_filter))

    agg_query = agg_query.group_by(Allocation.project).all()

    result = []
    for project, headcount, total_records, lost_minutes, absent_records in agg_query:
        lost_minutes = lost_minutes or 0
        headcount = headcount or 0
        total_records = total_records or 0
        absent_records = absent_records or 0
        rate = round(absent_records / total_records * 100, 2) if total_records > 0 else 0

        result.append({
            'project': project,
            'headcount': headcount,
            'total_records': total_records,
            'absent_records': absent_records,
            'lost_minutes': lost_minutes,
            'lost_hours': round(lost_minutes / 60, 2),
            'absenteeism_rate': rate
        })

    result.sort(key=lambda x: x['absenteeism_rate'], reverse=True)
    return jsonify({'projects': result})


@dashboard_bp.route('/api/by-shift')
@login_required
def api_by_shift():
    """Breakdown of absenteeism by shift — single SQL GROUP BY query."""
    start, end, error, code = _parse_date_range()
    if error:
        return error, code

    shifts_filter, projects, lines = _get_filter_params()

    emp_ids = None
    if _has_filters([], projects, lines):
        emp_ids = _get_filtered_emp_ids([], projects, lines)
        if emp_ids is not None and not emp_ids:
            return jsonify({'shifts': []})

    agg_query = db.session.query(
        Allocation.shift,
        db.func.count(db.distinct(Allocation.employee_id)).label('headcount'),
        db.func.count(Attendance.id).label('total_records'),
        db.func.coalesce(db.func.sum(Attendance.minutes_lost), 0).label('lost_minutes'),
        db.func.sum(
            db.case((Attendance.minutes_lost > 0, 1), else_=0)
        ).label('absent_records')
    ).join(
        Attendance, Attendance.allocation_id == Allocation.id
    ).filter(
        Allocation.end_date.is_(None),
        Attendance.record_date >= start,
        Attendance.record_date <= end
    )

    if emp_ids is not None:
        agg_query = agg_query.filter(Allocation.employee_id.in_(emp_ids))
    if shifts_filter:
        agg_query = agg_query.filter(Allocation.shift.in_([int(s) for s in shifts_filter]))
    if projects:
        agg_query = agg_query.filter(Allocation.project.in_(projects))
    if lines:
        agg_query = agg_query.filter(Allocation.line.in_(lines))

    agg_query = agg_query.group_by(Allocation.shift).all()

    result = []
    for shift_val, headcount, total_records, lost_minutes, absent_records in agg_query:
        lost_minutes = lost_minutes or 0
        headcount = headcount or 0
        total_records = total_records or 0
        absent_records = absent_records or 0
        rate = round(absent_records / total_records * 100, 2) if total_records > 0 else 0

        result.append({
            'shift': shift_val,
            'headcount': headcount,
            'total_records': total_records,
            'absent_records': absent_records,
            'lost_minutes': lost_minutes,
            'lost_hours': round(lost_minutes / 60, 2),
            'absenteeism_rate': rate
        })

    return jsonify({'shifts': result})


@dashboard_bp.route('/api/daily-trend')
@login_required
def api_daily_trend():
    """Daily absenteeism trend — single SQL GROUP BY record_date query."""
    start, end, error, code = _parse_date_range()
    if error:
        return error, code

    shifts, projects, lines = _get_filter_params()
    has_filters = _has_filters(shifts, projects, lines)
    emp_ids = None
    if has_filters:
        emp_ids = _get_filtered_emp_ids(shifts, projects, lines)

    # Single aggregated query by date
    trend_query = db.session.query(
        Attendance.record_date,
        db.func.sum(
            db.case((Attendance.minutes_lost > 0, 1), else_=0)
        ).label('absent_count'),
        db.func.coalesce(db.func.sum(Attendance.minutes_lost), 0).label('lost_minutes')
    ).filter(
        Attendance.record_date >= start,
        Attendance.record_date <= end
    )

    if emp_ids is not None:
        trend_query = trend_query.filter(Attendance.employee_id.in_(emp_ids))

    trend_rows = trend_query.group_by(Attendance.record_date).order_by(Attendance.record_date).all()

    # Build ordered result, filling gaps for dates with no data
    trend_map = {row[0]: (row[1] or 0, row[2] or 0) for row in trend_rows}

    dates = []
    absent_counts = []
    lost_minutes_list = []

    current = start
    while current <= end:
        dates.append(current.isoformat())
        ac, lm = trend_map.get(current, (0, 0))
        absent_counts.append(ac)
        lost_minutes_list.append(lm)
        current += timedelta(days=1)

    return jsonify({
        'dates': dates,
        'absent_counts': absent_counts,
        'lost_minutes': lost_minutes_list
    })


@dashboard_bp.route('/api/bradford-top-risks')
@login_required
def api_bradford_top_risks():
    """Return employees with high Bradford Factor scores using bulk SQL computation."""
    active_employees = Employee.query.filter_by(status='ACTIVE').all()
    if not active_employees:
        return jsonify({'risks': []})

    active_ids = [emp.id for emp in active_employees]
    name_map = {emp.id: emp.name for emp in active_employees}

    # Single SQL round-trip for all employees
    bradford_results = calculate_bradford_bulk(employee_ids=active_ids)

    risks = []
    for emp_id, emp_name in name_map.items():
        bf = bradford_results.get(emp_id, {})
        score = bf.get('bradford_score', 0)
        risk_level = bf.get('risk_level', 'low')

        if risk_level == 'high' or risk_level == 'moderate':
            risks.append({
                'employee_id': emp_id,
                'employee_name': emp_name,
                'bradford_score': score,
                'spells': bf.get('spells', 0),
                'total_days': bf.get('total_days', 0),
                'risk_level': risk_level
            })

    # Sort by score descending, high-risk first
    risks.sort(key=lambda x: (x['risk_level'] != 'high', -x['bradford_score']))
    return jsonify({'risks': risks[:20]})