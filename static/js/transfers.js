/**
 * transfers.js — Transfer request modal on the Operation screen.
 *
 * Opens the #transferModal (header button or per-row .open-transfer-modal),
 * drives the PUSH (Enviar) / PULL (Receber) dynamic field state and submits the
 * request asynchronously via POST /transfers/create (JSON).
 */
document.addEventListener('DOMContentLoaded', function () {
    var modalEl = document.getElementById('transferModal');
    var form = document.getElementById('transferForm');
    var empSelect = document.getElementById('tfEmployee');
    var lineSelect = form ? form.querySelector('select[name="target_line_id"]') : null;
    var empHint = document.getElementById('tfEmployeeHint');
    var toastContainer = document.getElementById('transferToastContainer');
    var dataScript = document.getElementById('transferData');

    if (!modalEl || !form) return;

    var DATA = { scopeEmployees: [], allEmployees: [], lines: [], scopeLineIds: [], isStaff: false };
    if (dataScript) {
        try { DATA = JSON.parse(dataScript.textContent); } catch (e) { /* keep defaults */ }
    }
    var isStaff = !!DATA.isStaff;

    // The leader's current line (first scoped line) — used as the PULL target.
    var currentLeaderLineId = (DATA.scopeLineIds && DATA.scopeLineIds.length) ? DATA.scopeLineIds[0] : '';

    // Tracks how the modal was opened: 'row' (locked to PUSH) or 'header'.
    var modalOrigin = 'header';

    function ensureScopeData() {
        // If the scope employee list is empty, fetch it from the backend so the
        // employee dropdown is always populated (e.g. header trigger).
        if (DATA.scopeEmployees.length > 0) return Promise.resolve();
        return fetch('/leader/api/employees/scope')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                DATA.scopeEmployees = (data.employees || []).map(function (e) {
                    return { id: e.employee_id, name: e.name };
                });
            })
            .catch(function () { /* ignore */ });
    }

    function showToast(message, type) {
        if (!toastContainer) { alert(message); return; }
        var colors = { success: 'bg-success', danger: 'bg-danger', info: 'bg-info' };
        var el = document.createElement('div');
        el.className = 'toast align-items-center text-white border-0 ' + (colors[type] || colors.info);
        el.setAttribute('role', 'alert');
        el.innerHTML =
            '<div class="d-flex"><div class="toast-body">' + message + '</div>' +
            '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>';
        toastContainer.appendChild(el);
        var toast = new bootstrap.Toast(el, { delay: 3500 });
        toast.show();
        el.addEventListener('hidden.bs.toast', function () { el.remove(); });
    }

    function populate(select, items, selectedId, format) {
        if (!select) return;
        select.innerHTML = '';
        var ph = document.createElement('option');
        ph.value = '';
        ph.textContent = 'Selecione...';
        select.appendChild(ph);
        items.forEach(function (it) {
            var o = document.createElement('option');
            o.value = it.id;
            o.textContent = format ? format(it) : (it.name + ' (' + it.id + ')');
            select.appendChild(o);
        });
        if (selectedId) select.value = selectedId;
    }

    function getSelectedType() {
        var checked = form.querySelector('input[name="request_type"]:checked');
        return checked ? checked.value : 'PUSH';
    }

    function refreshByType(selectedEmployeeId) {
        var type = getSelectedType();
        var scopeIds = DATA.scopeEmployees.map(function (e) { return e.id; });

        if (isStaff) {
            // ADMIN/SUPERVISOR: full manual selection of any employee / line / shift.
            populate(empSelect, DATA.allEmployees, selectedEmployeeId);
            if (lineSelect) {
                populate(lineSelect, DATA.lines, null, function (l) { return l.project + ' - ' + l.name; });
                lineSelect.disabled = false;
            }
            if (empSelect && empSelect.value === '') empSelect.disabled = false;
            return;
        }

        if (type === 'PUSH') {
            // Employee: the leader's current line operators.
            // Line: ALL destination lines (including the current one) so intra-line
            // shift transfers (same line, different shift) are possible.
            populate(empSelect, DATA.scopeEmployees, selectedEmployeeId);
            if (lineSelect) {
                populate(lineSelect, DATA.lines, null, function (l) { return l.project + ' - ' + l.name; });
                lineSelect.disabled = false;
            }
        } else { // PULL
            // Employee: operators from other lines; Line: locked to the leader's line.
            var others = DATA.allEmployees.filter(function (e) { return scopeIds.indexOf(e.id) === -1; });
            populate(empSelect, others, selectedEmployeeId);
            if (lineSelect) {
                var myLines = DATA.lines.filter(function (l) { return DATA.scopeLineIds.indexOf(l.id) !== -1; });
                populate(lineSelect, myLines, DATA.scopeLineIds[0] || '', function (l) { return l.project + ' - ' + l.name; });
                lineSelect.disabled = true;
            }
        }

        if (empSelect && empSelect.value === '') {
            empSelect.disabled = false;
        }
    }

    function updatePreviewLabel(employeeId, employeeName) {
        if (!empHint) return;
        if (!employeeId || !employeeName) { empHint.textContent = ''; return; }
        var type = getSelectedType();
        empHint.textContent = (type === 'PUSH' ? 'Enviando: ' : 'Recebendo: ') + employeeName + ' (' + employeeId + ')';
    }

    function findEmployeeName(employeeId) {
        var all = DATA.scopeEmployees.concat(DATA.allEmployees);
        var found = all.find(function (e) { return e.id === employeeId; });
        return found ? found.name : '';
    }

    function ensureSelectOption(select, value, text) {
        if (!select || !value) return;
        if (!select.querySelector('option[value="' + value + '"]')) {
            var opt = document.createElement('option');
            opt.value = value;
            opt.textContent = text || value;
            select.appendChild(opt);
        }
    }

    function openTransferModal(employeeId, employeeName, requestType, origin, employeeLineId) {
        modalOrigin = origin || 'header';
        form.reset();
        if (empSelect) empSelect.classList.remove('is-invalid');

        var type = requestType || 'PUSH';
        if (modalOrigin === 'row' && !requestType && employeeLineId) {
            // Row trigger without explicit type: decide by scope membership.
            type = (DATA.scopeLineIds.indexOf(employeeLineId) !== -1) ? 'PUSH' : 'PULL';
        }
        var radio = form.querySelector('input[name="request_type"][value="' + type + '"]');
        if (radio) radio.checked = true;

        // Lock the type only for non-staff row triggers (staff keep both clickable).
        var pushRadio = document.getElementById('tfTypeSend');
        var pullRadio = document.getElementById('tfTypeReceive');
        var lockType = (modalOrigin === 'row' && !isStaff);
        if (pushRadio) pushRadio.disabled = (lockType && type !== 'PUSH');
        if (pullRadio) pullRadio.disabled = (lockType && type !== 'PULL');

        // Ensure the scope employee data is loaded before populating the dropdown,
        // so the header trigger never opens with an empty employee list.
        ensureScopeData().then(function () {
            refreshByType(employeeId || '');

            if (empSelect) {
                // Staff keep the employee select editable; non-staff row triggers lock it.
                empSelect.disabled = (!isStaff && (Boolean(employeeId) || modalOrigin === 'row'));
                if (employeeId) {
                    // Ensure the <option> exists so the value actually binds in the DOM.
                    ensureSelectOption(empSelect, employeeId, (employeeName || findEmployeeName(employeeId)) + ' (' + employeeId + ')');
                    empSelect.value = employeeId;
                }
            }
            updatePreviewLabel(employeeId, employeeName || findEmployeeName(employeeId));
            bootstrap.Modal.getOrCreateInstance(modalEl).show();
        });
    }

    // Radio change -> refresh dynamic state + preview label
    form.querySelectorAll('input[name="request_type"]').forEach(function (radio) {
        radio.addEventListener('change', function () {
            refreshByType('');
            updatePreviewLabel('', '');
        });
    });

    // Employee change -> auto-switch PUSH/PULL based on scope membership.
    if (empSelect) {
        empSelect.addEventListener('change', function () {
            var empId = empSelect.value;
            if (!empId) return;
            var inScope = DATA.scopeEmployees.some(function (e) { return e.id === empId; });

            if (!inScope) {
                // Outside the leader's scope -> PULL (Receber), target = leader's line.
                var pullRadio = form.querySelector('input[name="request_type"][value="PULL"]');
                if (pullRadio && !pullRadio.disabled) pullRadio.checked = true;
                refreshByType(empId);
                if (lineSelect) {
                    lineSelect.value = DATA.scopeLineIds[0] || '';
                    lineSelect.disabled = true;
                }
            } else {
                // Inside the leader's scope -> PUSH (Enviar).
                var pushRadio = form.querySelector('input[name="request_type"][value="PUSH"]');
                if (pushRadio) pushRadio.checked = true;
                refreshByType(empId);
            }
            updatePreviewLabel(empId, findEmployeeName(empId));
        });
    }

    function refreshPendingBadge() {
        fetch('/transfers/api/pending-count')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var count = data.count || 0;
                var badge = document.getElementById('pendingTransferBadge');
                if (badge) {
                    if (count > 0) { badge.textContent = count; badge.style.display = ''; }
                    else { badge.remove(); }
                    return;
                }
                if (count > 0) {
                    var link = document.querySelector('a[href="/transfers/"]');
                    if (link) {
                        var b = document.createElement('span');
                        b.id = 'pendingTransferBadge';
                        b.className = 'badge bg-danger rounded-pill ms-1';
                        b.textContent = count;
                        link.appendChild(b);
                    }
                }
            })
            .catch(function () { /* ignore */ });
    }

    // Header button: open empty, default PUSH, both types selectable
    var openBtn = document.getElementById('transferModalOpen');
    if (openBtn) {
        openBtn.addEventListener('click', function () { openTransferModal(null, null, 'PUSH', 'header'); });
    }

    // Per-row buttons: pre-select the employee; auto-set PUSH/PULL by scope (leaders only).
    document.querySelectorAll('.open-transfer-modal').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var empId = btn.getAttribute('data-employee-id');
            var empName = btn.getAttribute('data-employee-name');
            var empLine = parseInt(btn.getAttribute('data-employee-line'), 10);
            if (isStaff) {
                // Staff: pre-select the employee but keep both types/fields editable.
                openTransferModal(empId, empName, 'PUSH', 'row', empLine);
            } else {
                var inScope = DATA.scopeLineIds.indexOf(empLine) !== -1;
                openTransferModal(empId, empName, inScope ? 'PUSH' : 'PULL', 'row', empLine);
            }
        });
    });

    // Async submit
    form.addEventListener('submit', async function (e) {
        e.preventDefault();
        var type = getSelectedType();
        var data = Object.fromEntries(new FormData(form).entries());
        // A disabled <select> is not included in FormData; use the preselected values.
        if (empSelect && empSelect.disabled && empSelect.value) {
            data.employee_id = empSelect.value;
        }
        if (lineSelect && lineSelect.disabled && lineSelect.value) {
            data.target_line_id = lineSelect.value;
        }
        // PULL ("Receber") must always send the leader's scoped line as target.
        if (type === 'PULL') {
            data.target_line_id = currentLeaderLineId || (lineSelect ? lineSelect.value : '');
        }

        // Client-side validation: employee_id and target_line_id are required.
        if (!data.employee_id || String(data.employee_id).trim() === '') {
            if (empSelect) {
                empSelect.classList.add('is-invalid');
                empSelect.focus();
            }
            showToast('Selecione o funcionário', 'danger');
            return;
        }
        if (!data.target_line_id || String(data.target_line_id).trim() === '') {
            if (lineSelect) lineSelect.classList.add('is-invalid');
            showToast('Selecione a linha de destino', 'danger');
            return;
        }
        if (empSelect) empSelect.classList.remove('is-invalid');
        if (lineSelect) lineSelect.classList.remove('is-invalid');

        var submitBtn = document.getElementById('transferSubmit');
        if (submitBtn) submitBtn.disabled = true;
        try {
            var res = await fetch('/transfers/create', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            var result = await res.json();
            if (result.success) {
                bootstrap.Modal.getOrCreateInstance(modalEl).hide();
                showToast(result.message, 'success');
                refreshPendingBadge();
            } else {
                showToast(result.error || result.message || 'Erro ao criar a solicitação.', 'danger');
            }
        } catch (err) {
            showToast('Erro de conexão ao criar a solicitação.', 'danger');
        } finally {
            if (submitBtn) submitBtn.disabled = false;
        }
    });
});

