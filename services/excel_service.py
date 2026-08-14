import io
import difflib
import pandas as pd
import logging
from datetime import date, datetime
from extensions import db
from models.employee import Employee
from models.allocation import Allocation
from models.attendance import Attendance
from models.user import User
from models.audit_log import AuditLog
from models.line import Line

logger = logging.getLogger(__name__)

# Fuzzy-match threshold for auto-correcting line-name typos.
# Set slightly below 0.88 to also catch single-character typos in short names
# (e.g. "Consoli" vs "Console" ≈ 0.857).
FUZZY_MATCH_THRESHOLD = 0.85


def _strip_text(value):
    """Return a stripped string, treating NaN/None as an empty string."""
    if value is None:
        return ''
    try:
        if pd.isna(value):
            return ''
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _resolve_line(lines, line_name, project):
    """Resolve (line_name, project) to a canonical Line, auto-creating or fuzzy-correcting.

    Args:
        lines (list): in-memory list of existing Line objects (updated on creation).
        line_name (str): raw line name from the spreadsheet.
        project (str): raw project name from the spreadsheet.

    Returns:
        tuple: (Line, action) where action is 'exact', 'fuzzy', or 'created'.
    """
    line_name = _strip_text(line_name)
    project = _strip_text(project)
    name_key = line_name.casefold()
    proj_key = project.casefold()

    # 1. Exact match (case-insensitive)
    for l in lines:
        if l.name.casefold() == name_key and l.project.casefold() == proj_key:
            return l, 'exact'

    # 2. Fuzzy match within the same project
    best = None
    best_ratio = 0.0
    for l in lines:
        if l.project.casefold() != proj_key:
            continue
        ratio = difflib.SequenceMatcher(None, name_key, l.name.casefold()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best = l
    if best and best_ratio >= FUZZY_MATCH_THRESHOLD:
        return best, 'fuzzy'

    # 3. Auto-provision a new line
    new_line = Line(name=line_name, project=project)
    db.session.add(new_line)
    db.session.flush()
    lines.append(new_line)
    return new_line, 'created'


def _clean_dataframe(df):
    """
    Sanitize the DataFrame:
    1. Drop rows where ID or Nome are NaN/None.
    2. Convert ID to string and strip whitespace.
    3. Remove rows where ID becomes empty, 'nan', 'None', or 'NaT'.
    4. Strip whitespace from all text columns.
    """
    # Step 1: Drop rows with null ID or Nome
    before = len(df)
    df.dropna(subset=['ID', 'Nome'], inplace=True)
    dropped_nan = before - len(df)
    if dropped_nan:
        logger.info(f"Dropped {dropped_nan} rows with null ID or Nome.")

    # Step 2: Convert ID to string and strip
    df['ID'] = df['ID'].astype(str).str.strip()

    # Step 3: Remove rows where ID is invalid after conversion
    invalid_ids = {'', 'nan', 'none', 'nat', 'null', 'na'}
    mask_invalid = df['ID'].str.lower().isin(invalid_ids) | df['ID'].isna()
    invalid_count = mask_invalid.sum()
    if invalid_count:
        logger.warning(f"Dropped {invalid_count} rows with invalid ID after sanitization.")
        df.drop(df[mask_invalid].index, inplace=True)

    # Step 4: Strip all text columns (NaN/None -> '')
    for col in ['Nome', 'Projeto', 'Linha']:
        if col in df.columns:
            df[col] = df[col].apply(_strip_text)

    # Also strip Nome after ensuring it exists
    if 'Nome' in df.columns:
        df['Nome'] = df['Nome'].apply(_strip_text)

    # Remove rows where Nome became empty/invalid after stripping
    invalid_names = {'', 'nan', 'none', 'nat', 'null', 'na'}
    mask_bad_name = df['Nome'].str.lower().isin(invalid_names) | df['Nome'].isna()
    bad_name_count = mask_bad_name.sum()
    if bad_name_count:
        logger.warning(f"Dropped {bad_name_count} rows with invalid Nome after sanitization.")
        df.drop(df[mask_bad_name].index, inplace=True)

    logger.info(f"DataFrame sanitized. {len(df)} valid rows remain.")
    return df


def process_excel_upload(file_path, user_id=None):
    """
    Process an Excel file (.xlsx) with columns: ID, Nome, Turno, Projeto, Linha.
    Performs upsert into employees and manages allocation_history records.
    
    Args:
        file_path (str): Path to the .xlsx file.
        user_id (int, optional): ID of the user performing the upload.

    Returns:
        dict: Summary with counts of inserted/updated employees.
    """
    try:
        df = pd.read_excel(file_path, engine='openpyxl')
        logger.info(f"Excel file read successfully: {file_path} ({len(df)} raw rows).")
    except Exception as e:
        logger.error(f"Failed to read Excel file {file_path}: {e}")
        raise ValueError(f"Erro ao ler o arquivo Excel: {e}")

    # Expected columns
    required_cols = ['ID', 'Nome', 'Turno', 'Projeto', 'Linha']
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Colunas obrigatórias ausentes: {missing}")

    # Sanitize DataFrame
    try:
        df = _clean_dataframe(df)
    except Exception as e:
        logger.error(f"Error during DataFrame sanitization: {e}")
        raise ValueError(f"Erro ao processar os dados da planilha: {e}")

    # Convert shift to int safely
    df['Turno'] = pd.to_numeric(df['Turno'], errors='coerce').fillna(0).astype(int)

    today = date.today()
    inserted_count = 0
    updated_count = 0
    allocation_created = 0
    created_lines = []
    auto_corrected_lines = []

    # Load existing lines once; new lines are appended as they are auto-provisioned.
    lines = Line.query.all()

    for _, row in df.iterrows():
        emp_id = _strip_text(row['ID'])
        name = _strip_text(row['Nome'])
        shift = int(row['Turno'])
        project = _strip_text(row['Projeto'])
        line = _strip_text(row['Linha'])

        # Redundant safety check — _clean_dataframe should have removed these already
        if not emp_id or not name or emp_id.lower() in ('nan', 'none', 'nat', 'null', 'na', ''):
            logger.warning(f"Skipping row with invalid ID='{emp_id}', Nome='{name}'.")
            continue

        # Resolve the line (exact / fuzzy / auto-create) before allocation
        line_obj, action = _resolve_line(lines, line, project)
        canonical_line = line_obj.name
        canonical_project = line_obj.project
        if action == 'created':
            created_lines.append(canonical_line)
        elif action == 'fuzzy':
            auto_corrected_lines.append({'from': line, 'to': canonical_line})

        # Upsert employee
        employee = Employee.query.get(emp_id)
        if employee:
            # Update name if changed
            if employee.name != name:
                old_value = {'name': employee.name}
                employee.name = name
                new_value = {'name': name}
                updated_count += 1
                db.session.add(AuditLog(
                    user_id=user_id or 1,
                    action='EMPLOYEE_UPDATE',
                    old_value=old_value,
                    new_value=new_value
                ))
        else:
            employee = Employee(id=emp_id, name=name)
            db.session.add(employee)
            db.session.flush()  # Flush to ensure employee is persisted
            inserted_count += 1

        # Manage allocation history using the canonical (sanitized) line/project
        current_alloc = Allocation.query.filter_by(employee_id=emp_id, end_date=None).first()

        if current_alloc:
            # Check if shift/project/line changed
            if (current_alloc.shift != shift or
                current_alloc.project != canonical_project or
                current_alloc.line != canonical_line):
                # Close current allocation
                current_alloc.end_date = today

                # Create new allocation
                new_alloc = Allocation(
                    employee_id=emp_id,
                    shift=shift,
                    project=canonical_project,
                    line=canonical_line,
                    start_date=today,
                    end_date=None
                )
                db.session.add(new_alloc)
                allocation_created += 1
        else:
            # No active allocation, create one
            new_alloc = Allocation(
                employee_id=emp_id,
                shift=shift,
                project=canonical_project,
                line=canonical_line,
                start_date=today,
                end_date=None
            )
            db.session.add(new_alloc)
            allocation_created += 1

        # Flush to ensure allocation IDs are generated before next iteration
        db.session.flush()

    db.session.commit()
    logger.info(
        f"Upload complete: {inserted_count} inserted, {updated_count} updated, "
        f"{allocation_created} allocations created, {len(created_lines)} lines created, "
        f"{len(auto_corrected_lines)} lines auto-corrected."
    )

    return {
        'status': 'success',
        'processed_rows': len(df),
        'employees_inserted': inserted_count,
        'employees_updated': updated_count,
        'allocations_created': allocation_created,
        'created_lines': created_lines,
        'auto_corrected_lines': auto_corrected_lines
    }


def _filter_employee_ids_for_export(shift, project, line):
    """Return list of employee_ids matching optional allocation filters."""
    query = db.session.query(Allocation.employee_id).filter(Allocation.end_date.is_(None))
    if shift:
        query = query.filter(Allocation.shift == int(shift))
    if project:
        query = query.filter(Allocation.project == project)
    if line:
        query = query.filter(Allocation.line == line)
    return [row[0] for row in query.distinct().all()]


def generate_absenteeism_report(start_date, end_date, shift=None, project=None, line=None):
    """
    Generate a multi-tab Excel report (.xlsx) in memory.

    Args:
        start_date (date): Start of the reporting period.
        end_date (date): End of the reporting period.
        shift (str|int, optional): Filter by shift.
        project (str, optional): Filter by project.
        line (str, optional): Filter by line.

    Returns:
        io.BytesIO: In-memory .xlsx file buffer ready for download.
    """
    logger.info(f"Generating absenteeism report {start_date} to {end_date}. "
                f"Filters - shift:{shift}, project:{project}, line:{line}")

    # Determine filtered employee IDs
    has_filters = bool(shift or project or line)
    emp_ids = None
    if has_filters:
        emp_ids = _filter_employee_ids_for_export(shift, project, line)

    # ---- Build Attendance base query ----
    att_base = db.session.query(Attendance).filter(
        Attendance.record_date >= start_date,
        Attendance.record_date <= end_date
    )
    if emp_ids is not None:
        att_base = att_base.filter(Attendance.employee_id.in_(emp_ids))

    # ---- Build detailed records query (join with allocation, employee, user) ----
    detail_query = (
        db.session.query(
            Attendance.record_date,
            Attendance.employee_id,
            Employee.name,
            Allocation.shift,
            Allocation.project,
            Allocation.line,
            Attendance.event_type,
            Attendance.check_in_time,
            Attendance.check_out_time,
            Attendance.minutes_lost,
            User.username
        )
        .join(Allocation, Attendance.allocation_id == Allocation.id)
        .join(Employee, Attendance.employee_id == Employee.id)
        .join(User, Attendance.registered_by_id == User.id)
        .filter(Attendance.record_date >= start_date, Attendance.record_date <= end_date)
    )
    if emp_ids is not None:
        detail_query = detail_query.filter(Attendance.employee_id.in_(emp_ids))

    detail_rows = detail_query.order_by(Attendance.record_date.desc(), Attendance.employee_id).all()

    # ---- Tab 1: Summary Indicators ----
    total_employees = len(emp_ids) if emp_ids is not None else Employee.query.filter_by(status='ACTIVE').count()

    total_records = att_base.count()
    absent_records = att_base.filter(Attendance.minutes_lost > 0).count()
    late_arrivals = att_base.filter(Attendance.event_type == 'LATE_ARRIVAL').count()
    early_exits = att_base.filter(Attendance.event_type == 'EARLY_EXIT').count()
    full_absences = att_base.filter(Attendance.event_type == 'FULL_ABSENCE').count()

    total_lost_minutes = db.session.query(
        db.func.coalesce(db.func.sum(Attendance.minutes_lost), 0)
    ).filter(
        Attendance.record_date >= start_date,
        Attendance.record_date <= end_date
    )
    if emp_ids is not None:
        total_lost_minutes = total_lost_minutes.filter(Attendance.employee_id.in_(emp_ids))
    total_lost_minutes = total_lost_minutes.scalar() or 0

    # CORRECTED: Count distinct employees with absence instead of total records
    absent_employees_count = db.session.query(
        db.func.count(db.distinct(Attendance.employee_id))
    ).filter(
        Attendance.record_date >= start_date,
        Attendance.record_date <= end_date,
        Attendance.minutes_lost > 0
    )
    if emp_ids is not None:
        absent_employees_count = absent_employees_count.filter(Attendance.employee_id.in_(emp_ids))
    absent_employees_count = absent_employees_count.scalar() or 0

    absenteeism_rate = round((absent_employees_count / total_employees * 100), 2) if total_employees > 0 else 0

    summary_data = {
        'Indicador': [
            'Total de Funcionários',
            'Total de Registros',
            'Total de Ausências (FULL_ABSENCE)',
            'Total de Atrasos (LATE_ARRIVAL)',
            'Total de Saídas Antecipadas (EARLY_EXIT)',
            'Total de Registros com Minutos Perdidos',
            'Minutos Perdidos Totais',
            'Horas Perdidas Totais',
            'Taxa de Absenteísmo (%)'
        ],
        'Valor': [
            total_employees,
            total_records,
            full_absences,
            late_arrivals,
            early_exits,
            absent_records,
            total_lost_minutes,
            round(total_lost_minutes / 60, 2),
            absenteeism_rate
        ]
    }
    df_summary = pd.DataFrame(summary_data)

    # ---- Tab 2: Absence by Line ----
    line_breakdown = (
        db.session.query(
            Allocation.line,
            Allocation.project,
            Allocation.shift,
            db.func.count(db.distinct(Attendance.employee_id)).label('headcount'),
            db.func.count(Attendance.id).label('total_records'),
            db.func.sum(
                db.case((Attendance.minutes_lost > 0, 1), else_=0)
            ).label('absent_records'),
            db.func.coalesce(db.func.sum(Attendance.minutes_lost), 0).label('lost_minutes')
        )
        .join(Attendance, Attendance.allocation_id == Allocation.id)
        .filter(Attendance.record_date >= start_date, Attendance.record_date <= end_date)
    )
    if emp_ids is not None:
        line_breakdown = line_breakdown.filter(Attendance.employee_id.in_(emp_ids))
    line_breakdown = line_breakdown.group_by(
        Allocation.line, Allocation.project, Allocation.shift
    ).order_by(Allocation.line).all()

    line_rows = []
    for row in line_breakdown:
        rate = round((row.absent_records / row.total_records * 100), 2) if row.total_records > 0 else 0
        line_rows.append({
            'Linha': row.line,
            'Projeto': row.project,
            'Turno': row.shift,
            'Headcount': row.headcount,
            'Total Registros': row.total_records,
            'Ausências (min > 0)': row.absent_records,
            'Minutos Perdidos': int(row.lost_minutes),
            'Horas Perdidas': round(row.lost_minutes / 60, 2),
            'Taxa de Absenteísmo (%)': rate
        })
    df_lines = pd.DataFrame(line_rows)

    # ---- Tab 3: Detailed Log ----
    detailed_data = []
    for row in detail_rows:
        detailed_data.append({
            'Data': row.record_date.isoformat() if row.record_date else '',
            'ID Funcionário': row.employee_id,
            'Nome': row.name,
            'Turno': row.shift,
            'Projeto': row.project,
            'Linha': row.line,
            'Tipo de Evento': row.event_type,
            'Entrada': row.check_in_time.isoformat() if row.check_in_time else '',
            'Saída': row.check_out_time.isoformat() if row.check_out_time else '',
            'Minutos Perdidos': row.minutes_lost,
            'Registrado por': row.username
        })
    df_detailed = pd.DataFrame(detailed_data)

    # ---- Write to in-memory Excel buffer ----
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df_summary.to_excel(writer, sheet_name='Summary Indicators', index=False)
        df_lines.to_excel(writer, sheet_name='Absence by Line', index=False)
        df_detailed.to_excel(writer, sheet_name='Detailed Log', index=False)

    buffer.seek(0)
    logger.info(f"Excel report generated: {len(detailed_data)} detail rows, "
                f"{len(line_rows)} line rows, 1 summary sheet.")
    return buffer


def export_absence_report(rows):
    """
    Generate an Excel (.xlsx) report from a list of (Attendance, Employee, Allocation) tuples.

    Args:
        rows: list of (Attendance, Employee, Allocation) from a joined SQLAlchemy query.

    Returns:
        io.BytesIO buffer containing the .xlsx file, ready for Flask's send_file().
    """
    event_labels = {
        'FULL_ABSENCE': 'Falta',
        'LATE_ARRIVAL': 'Atraso',
        'EARLY_EXIT': 'Saída Antecipada',
        'VACATION': 'Férias'
    }

    data = []
    for att, emp, alloc in rows:
        data.append({
            'Data': att.record_date.isoformat() if att.record_date else '',
            'ID Funcionário': emp.id,
            'Nome': emp.name,
            'Turno': alloc.shift,
            'Projeto': alloc.project,
            'Linha': alloc.line,
            'Tipo de Evento': event_labels.get(att.event_type, att.event_type),
            'Entrada': att.check_in_time.isoformat() if att.check_in_time else '',
            'Saída': att.check_out_time.isoformat() if att.check_out_time else '',
            'Minutos Perdidos': att.minutes_lost or 0
        })

    df = pd.DataFrame(data)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Relatório de Ausências', index=False)

    buffer.seek(0)
    logger.info(f"Absence report exported: {len(data)} rows.")
    return buffer
