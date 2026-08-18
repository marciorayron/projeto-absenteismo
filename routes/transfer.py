from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from extensions import db
from models.employee import Employee
from models.line import Line
from models.shift import Shift
from models.leader_scope import LeaderScope
from models.transfer import TransferRequest, EmployeeMovementLog

transfer_bp = Blueprint('transfer', __name__, url_prefix='/transfers')


def _line_owner_users(line_id, shift_id=None):
    """Return the list of Users whose LeaderScope matches the line (and optionally shift)."""
    if line_id is None:
        return []
    q = LeaderScope.query.filter_by(line_id=line_id)
    if shift_id is not None:
        q = q.filter_by(shift_id=shift_id)
    return list({ls.leader for ls in q.all() if ls.leader})


def _leader_scope_line_ids(user):
    """Return the set of line_ids assigned to a leader via LeaderScope."""
    return {s.line_id for s in user.managed_scopes if s.line_id}


def pending_transfer_count():
    """Number of pending transfer requests visible to the current user.

    - LIDER: requests involving lines in their assigned scope.
    - ADMIN / SUPERVISOR: all pending requests.
    """
    if current_user.role in ('ADMIN', 'SUPERVISOR'):
        return TransferRequest.query.filter_by(status='PENDING').count()

    scope_lines = _leader_scope_line_ids(current_user)
    count = TransferRequest.query.filter(
        TransferRequest.status == 'PENDING',
        db.or_(
            TransferRequest.requester_id == current_user.id,
            TransferRequest.target_leader_id == current_user.id,
            TransferRequest.target_line_id.in_(scope_lines) if scope_lines else False,
        )
    ).count()
    return count


def _can_respond(request_obj):
    """A user may respond (approve/reject) a pending transfer request if they are
    ADMIN/SUPERVISOR, or the designated approver. A requester can never respond to
    their own request.

    Approver by type:
      - PUSH (send): the owner of the TARGET (destination) line.
      - PULL (receive): the owner of the SOURCE line (where the operator currently is).
    """
    if request_obj.status != 'PENDING':
        return False
    if request_obj.requester_id == current_user.id:
        return False  # users cannot approve/reject self-generated requests
    if current_user.role in ('ADMIN', 'SUPERVISOR'):
        return True
    if request_obj.target_leader_id is not None and request_obj.target_leader_id == current_user.id:
        return True

    if request_obj.request_type == 'PUSH':
        line_id = request_obj.target_line_id
    else:  # PULL -> owner of the source line
        line_id = request_obj.origin_line_id or request_obj.employee.line_id
    owners = _line_owner_users(line_id, None)
    return any(u.id == current_user.id for u in owners)


@transfer_bp.route('/')
@login_required
def index():
    """List transfer requests visible to the current user, with the creation form."""
    is_staff = current_user.role in ('ADMIN', 'SUPERVISOR')

    if is_staff:
        requests = TransferRequest.query.order_by(TransferRequest.created_at.desc()).all()
    else:
        scope_lines = _leader_scope_line_ids(current_user)
        requests = TransferRequest.query.filter(
            TransferRequest.status == 'PENDING',
            db.or_(
                TransferRequest.requester_id == current_user.id,
                TransferRequest.target_leader_id == current_user.id,
                TransferRequest.target_line_id.in_(scope_lines) if scope_lines else False,
            )
        ).order_by(TransferRequest.created_at.desc()).all()

    # Form data
    employees = Employee.query.filter_by(is_active=True).order_by(Employee.name).all()
    lines = Line.query.filter_by(is_active=True).order_by(Line.project, Line.name).all()
    shifts = Shift.query.filter_by(is_active=True).order_by(Shift.id).all()
    scope_lines = _leader_scope_line_ids(current_user)
    respondable_ids = {r.id for r in requests if _can_respond(r)}

    return render_template(
        'transfers/index.html',
        requests=requests,
        employees=employees,
        lines=lines,
        shifts=shifts,
        is_staff=is_staff,
        scope_lines=scope_lines,
        pending_count=pending_transfer_count(),
        respondable_ids=respondable_ids,
        can_cancel={r.id for r in requests if r.status == 'PENDING' and (is_staff or r.requester_id == current_user.id)},
    )


@transfer_bp.route('/create', methods=['POST'])
@login_required
def create():
    """Create a PUSH or PULL transfer request.

    Supports both regular form posts (returns a redirect) and async JSON
    submissions (returns a JSON response used by the operation screen modal).
    """
    is_json = request.is_json
    if is_json:
        data = request.get_json(silent=True) or {}
        employee_id = str(data.get('employee_id', '') or '').strip()
        request_type = str(data.get('request_type', '') or '').strip().upper()
        target_line_id = str(data.get('target_line_id', '') or '').strip()
        target_shift_id = str(data.get('target_shift_id', '') or '').strip()
        notes = str(data.get('notes', '') or '').strip()
    else:
        employee_id = request.form.get('employee_id', '').strip()
        request_type = request.form.get('request_type', '').strip().upper()
        target_line_id = request.form.get('target_line_id', '').strip()
        target_shift_id = request.form.get('target_shift_id', '').strip()
        notes = request.form.get('notes', '').strip()

    req, error = _create_transfer(
        employee_id, request_type, target_line_id, target_shift_id, notes
    )

    if is_json:
        if error:
            return jsonify({'success': False, 'error': error}), 400
        auto_approved = (req.status == 'APPROVED')
        return jsonify({
            'success': True,
            'message': ('Transferência concluída imediatamente (permissão elevada).'
                        if auto_approved else 'Solicitação de transferência criada com sucesso.'),
            'error': None,
            'request_id': req.id,
            'auto_approved': auto_approved,
        })

    if error:
        flash(error, 'danger')
        return redirect(url_for('transfer.index'))
    flash('Solicitação de transferência criada com sucesso.', 'success')
    return redirect(url_for('transfer.index'))


def _create_transfer(employee_id, request_type, target_line_id, target_shift_id, notes):
    """Validate and create a transfer request.

    Returns:
        tuple: (TransferRequest, None) on success, or (None, error_message) on failure.
    """
    if not employee_id:
        return None, 'Selecione o funcionário'
    if not target_line_id:
        return None, 'Selecione a linha de destino'
    if request_type not in ('PUSH', 'PULL'):
        return None, 'Tipo de solicitação inválido'

    try:
        target_line_id = int(target_line_id)
    except (TypeError, ValueError):
        return None, 'Linha de destino inválida.'

    if target_shift_id:
        try:
            target_shift_id = int(target_shift_id)
        except (TypeError, ValueError):
            target_shift_id = None
    else:
        target_shift_id = None

    employee = Employee.query.get(employee_id)
    if not employee:
        return None, 'Funcionário não encontrado.'
    if not employee.is_active:
        return None, 'Funcionário inativo não pode ser transferido.'

    target_line = Line.query.get(target_line_id)
    if not target_line or not target_line.is_active:
        return None, 'Linha de destino inválida.'

    scope_lines = _leader_scope_line_ids(current_user)
    is_staff = current_user.role in ('ADMIN', 'SUPERVISOR')

    # Origin = the operator's current line/shift. Fall back to the active Allocation
    # when Employee.line_id is not populated (Excel-imported).
    origin_line_id = employee.line_id
    origin_shift_id = employee.shift_id
    if origin_line_id is None:
        active_alloc = employee.get_active_allocation()
        if active_alloc:
            line_ref = Line.query.filter_by(
                name=active_alloc.line, project=active_alloc.project or ''
            ).first()
            origin_line_id = line_ref.id if line_ref else None

    if request_type == 'PUSH':
        # Sending one of MY employees: the employee must belong to my scope.
        # ADMIN/SUPERVISOR bypass the leader scope restriction.
        if not is_staff:
            current_app.logger.debug(
                f"[PUSH] scope_lines={sorted(scope_lines)} employee_line_id={origin_line_id} "
                f"target_line_id={target_line_id}"
            )
            if origin_line_id is None or origin_line_id not in scope_lines:
                current_app.logger.warning(
                    f"[PUSH REJECTED] user={current_user.id} scope_lines={sorted(scope_lines)} "
                    f"employee_line_id={origin_line_id} target_line_id={target_line_id} "
                    f"reason='employee not in leader scope'"
                )
                return None, 'Você só pode enviar colaboradores das linhas do seu escopo.'
        # Target leader = owner of the destination line.
        owners = _line_owner_users(target_line_id, target_shift_id)
    else:  # PULL
        # Requesting an employee from another line: destination (my target) line must be in my scope.
        if not is_staff and target_line_id not in scope_lines:
            current_app.logger.warning(
                f"[PULL REJECTED] user={current_user.id} scope_lines={sorted(scope_lines)} "
                f"target_line_id={target_line_id}"
            )
            return None, 'Você só pode solicitar para linhas do seu escopo.'
        # Target leader = current owner leader of the employee (no scope check on the
        # employee, since they are being pulled from another line).
        current_app.logger.debug(
            f"[PULL] user={current_user.id} scope_lines={sorted(scope_lines)} "
            f"employee_line_id={origin_line_id} target_line_id={target_line_id}"
        )
        owners = _line_owner_users(origin_line_id, employee.shift_id)

    # Prevent a no-op transfer: identical line AND identical shift is invalid,
    # but the same line with a DIFFERENT shift (intra-line shift change) is allowed.
    effective_shift = target_shift_id if target_shift_id is not None else employee.shift_id
    if origin_line_id == target_line_id and employee.shift_id == effective_shift:
        current_app.logger.debug(
            f"[PUSH/PULL] no-op rejected: employee_line={origin_line_id} "
            f"target_line={target_line_id} effective_shift={effective_shift}"
        )
        return None, 'O colaborador já está nesta linha e turno.'

    target_leader_id = owners[0].id if owners else None

    req = TransferRequest(
        employee_id=employee.id,
        requester_id=current_user.id,
        target_leader_id=target_leader_id,
        origin_line_id=origin_line_id,
        origin_shift_id=origin_shift_id,
        target_line_id=target_line_id,
        target_shift_id=target_shift_id,
        request_type=request_type,
        status='PENDING',
        notes=notes or None,
    )
    db.session.add(req)
    db.session.flush()

    if is_staff:
        # Elevated privilege (ADMIN/SUPERVISOR): skip the approval queue and execute
        # the allocation change immediately (updates employee + active Allocation and
        # logs the movement), leaving the request as APPROVED.
        _approve(req)
        return req, None

    db.session.commit()
    return req, None


@transfer_bp.route('/api/pending-count')
@login_required
def api_pending_count():
    """Return the pending transfer count visible to the current user (for the badge)."""
    return jsonify({'count': pending_transfer_count()})



@transfer_bp.route('/<int:transfer_id>/respond', methods=['POST'])
@login_required
def respond(transfer_id):
    """Approve or reject a pending transfer request."""
    request_obj = TransferRequest.query.get_or_404(transfer_id)
    if request_obj.status != 'PENDING':
        flash('Esta solicitação já foi respondida.', 'warning')
        return redirect(url_for('transfer.index'))

    if not _can_respond(request_obj):
        flash('Você não tem permissão para responder a esta solicitação.', 'danger')
        return redirect(url_for('transfer.index'))

    decision = request.form.get('decision', '').strip().upper()
    if decision == 'APPROVE':
        _approve(request_obj)
        flash(f'Transferência aprovada para {request_obj.employee.name}.', 'success')
    elif decision == 'REJECT':
        request_obj.status = 'REJECTED'
        db.session.commit()
        flash('Solicitação rejeitada.', 'info')
    else:
        flash('Decisão inválida.', 'warning')

    return redirect(url_for('transfer.index'))


def _approve(request_obj):
    """Apply the transfer: update Employee + active Allocation and log the movement."""
    employee = request_obj.employee
    origin_line_id = employee.line_id
    origin_shift_id = employee.shift_id

    # Update Employee
    employee.line_id = request_obj.target_line_id
    if request_obj.target_shift_id is not None:
        employee.shift_id = request_obj.target_shift_id

    # Keep the active Allocation in sync so KPI/attendance queries stay consistent.
    employee.sync_allocation(commit=False)

    # Movement audit log
    db.session.add(EmployeeMovementLog(
        employee_id=employee.id,
        origin_line_id=origin_line_id,
        target_line_id=request_obj.target_line_id,
        origin_shift_id=origin_shift_id,
        target_shift_id=request_obj.target_shift_id,
        approved_by_id=current_user.id,
    ))

    request_obj.status = 'APPROVED'
    db.session.commit()


@transfer_bp.route('/<int:transfer_id>/cancel', methods=['POST'])
@login_required
def cancel(transfer_id):
    """Cancel a pending request (only the requester or staff)."""
    request_obj = TransferRequest.query.get_or_404(transfer_id)
    is_staff = current_user.role in ('ADMIN', 'SUPERVISOR')
    if not (is_staff or request_obj.requester_id == current_user.id):
        flash('Você não tem permissão para cancelar esta solicitação.', 'danger')
        return redirect(url_for('transfer.index'))
    if request_obj.status != 'PENDING':
        flash('Apenas solicitações pendentes podem ser canceladas.', 'warning')
        return redirect(url_for('transfer.index'))

    request_obj.status = 'CANCELLED'
    db.session.commit()
    flash('Solicitação cancelada.', 'info')
    return redirect(url_for('transfer.index'))

