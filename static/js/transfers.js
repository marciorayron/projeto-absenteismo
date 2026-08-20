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
    // Prefer the global injected by the template; fall back to the older JSON script tag.
    if (window.TRANSFER_DATA && typeof window.TRANSFER_DATA === 'object' && Object.keys(window.TRANSFER_DATA).length) {
        DATA = window.TRANSFER_DATA;
    } else if (dataScript) {
        try { DATA = JSON.parse(dataScript.textContent); } catch (e) { /* keep defaults */ }
    }

    // Type-safety hardening: cast every scoped line id to Number once, at load time,
    // so the scope-membership checks below never hit a silent string/number mismatch.
    DATA.scopeLineIds = (DATA.scopeLineIds || []).map(normalizeLineId).filter(function (v) { return v !== null; });
    DATA.scopeEmployees = (DATA.scopeEmployees || []).map(function (e) {
        return { id: e.id, name: e.name, line_id: normalizeLineId(e.line_id) };
    });
    DATA.allEmployees = (DATA.allEmployees || []).map(function (e) {
        return { id: e.id, name: e.name, line_id: normalizeLineId(e.line_id) };
    });

    var isStaff = !!DATA.isStaff;

    console.log('[Transfer Debug] DATA state:', DATA);

    // The leader's current line (first scoped line) — used as the PULL target.
    var currentLeaderLineId = (DATA.scopeLineIds && DATA.scopeLineIds.length) ? DATA.scopeLineIds[0] : '';

    // Tracks how the modal was opened: 'row' (locked to PUSH) or 'header'.
    var modalOrigin = 'header';

    // ── Normalized line-id helpers ─────────────────────────────────────────
    // Guards against silent string/number mismatches: a leader's scoped line ids
    // and an employee's `line_id` can come from different sources (HTML attributes,
    // JSON ints, form values) with different types. Every scope check below uses
    // these helpers so `.includes()`/`.indexOf()` never fails silently.
    function normalizeLineId(value) {
        if (value === null || value === undefined || value === '') return null;
        var n = Number(value);
        return Number.isFinite(n) ? n : null;
    }

    function isEmployeeLineInScope(lineId) {
        var n = normalizeLineId(lineId);
        if (n === null) return false;
        return DATA.scopeLineIds.includes(n);
    }

    function findEmployeeRecord(employeeId) {
        var all = DATA.scopeEmployees.concat(DATA.allEmployees);
        return all.find(function (e) { return String(e.id) === String(employeeId); }) || null;
    }

    function ensureScopeData() {
        // If the scope data is already present (template injection), nothing to do.
        if (DATA.scopeEmployees.length > 0 && DATA.scopeLineIds.length > 0) {
            console.log('[Transfer Debug] scope data already loaded.');
            return Promise.resolve();
        }
        console.log('[Transfer Debug] scope data missing — fetching /leader/api/employees/scope');
        // Fallback fetch so the dropdown is always populated even when the template
        // injection is empty/undefined. Also reconstruct scopeLineIds from the payload.
        return fetch('/leader/api/employees/scope')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                DATA.scopeEmployees = (data.employees || []).map(function (e) {
                    return { id: e.employee_id, name: e.name, line_id: normalizeLineId(e.line_id) };
                });
                if (DATA.scopeLineIds.length === 0) {
                    var lineIds = {};
                    DATA.scopeEmployees.forEach(function (e) {
                        if (e.line_id !== null) lineIds[e.line_id] = true;
                    });
                    DATA.scopeLineIds = Object.keys(lineIds).map(Number);
                }
                console.log('[Transfer Debug] scope data refreshed:', DATA.scopeEmployees);
            })
            .catch(function (err) { console.error('[Transfer Debug] scope fetch failed', err); });
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
            // Employee: the leader's scoped-line operators.
            // Explicitly enable "Enviar colaborador" when an in-scope operator is the target.
            var pushRadio = form.querySelector('input[name="request_type"][value="PUSH"]');
            if (pushRadio) pushRadio.disabled = false;

            populate(empSelect, DATA.scopeEmployees, selectedEmployeeId);

            // Line: all destination lines EXCEPT the selected employee's origin line,
            // so the target line always differs from the source line.
            var originLineId = null;
            if (selectedEmployeeId) {
                var empRec = findEmployeeRecord(selectedEmployeeId);
                if (empRec && empRec.line_id) originLineId = normalizeLineId(empRec.line_id);
            }
            if (lineSelect) {
                var destLines = DATA.lines.filter(function (l) {
                    return originLineId === null || normalizeLineId(l.id) !== originLineId;
                });
                populate(lineSelect, destLines, null, function (l) { return l.project + ' - ' + l.name; });
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
            type = isEmployeeLineInScope(employeeLineId) ? 'PUSH' : 'PULL';
        }
        var radio = form.querySelector('input[name="request_type"][value="' + type + '"]');
        if (radio) radio.checked = true;

        // Lock the type only for non-staff row triggers (staff keep both clickable).
        var pushRadio = document.getElementById('tfTypeSend');
        var pullRadio = document.getElementById('tfTypeReceive');
        var lockType = (modalOrigin === 'row' && !isStaff);
        if (pushRadio) pushRadio.disabled = (lockType && type !== 'PUSH');
        if (pullRadio) pullRadio.disabled = (lockType && type !== 'PULL');

        // Show the modal immediately with a "Carregando..." placeholder and block the
        // submit until the scope data has arrived, so the header trigger never shows
        // an empty/blank employee dropdown.
        var submitBtn = document.getElementById('transferSubmit');
        if (empSelect) {
            empSelect.innerHTML = '<option value="">Carregando...</option>';
            empSelect.disabled = true;
        }
        if (submitBtn) submitBtn.disabled = true;
        bootstrap.Modal.getOrCreateInstance(modalEl).show();

        // Block until scope data is ready, then populate the dynamic fields.
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
            if (submitBtn) submitBtn.disabled = false;
        }).catch(function () {
            if (submitBtn) submitBtn.disabled = false;
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
            var inScope = DATA.scopeEmployees.some(function (e) { return String(e.id) === String(empId); });

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
            var empLine = Number(btn.dataset.employeeLine);
            if (!Number.isFinite(empLine)) {
                // The data attribute may be empty for Excel-imported operators whose line
                // lives only in the Allocation; resolve it from the injected employee data.
                var empRec = findEmployeeRecord(empId);
                empLine = (empRec && empRec.line_id !== null) ? empRec.line_id : NaN;
            }
            if (isStaff) {
                // Staff: pre-select the employee but keep both types/fields editable.
                openTransferModal(empId, empName, 'PUSH', 'row', empLine);
            } else {
                var inScope = isEmployeeLineInScope(empLine);
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

