/**
 * scope-picker.js — Cascading Shift → Project → Line scope picker for
 * assigning managed scopes to Leaders. Reads a global `SCOPES` array
 * of {shift, line_id, line, project} objects and renders removable badges into
 * a hidden JSON input.
 */
function initScopePicker(config) {
    const root = document.getElementById(config.rootId);
    const hidden = document.getElementById(config.hiddenId);
    if (!root || !hidden) return;

    let selected = [];
    try {
        selected = (config.initial || []).map(function (s) {
            return { shift: parseInt(s.shift), line_id: parseInt(s.line_id), project: s.project || '', line: s.line };
        }).filter(function (s) { return s.line_id && s.line; });
    } catch (e) { selected = []; }

    root.innerHTML = `
        <div class="row g-2 mb-2">
            <div class="col-md-4">
                <label class="form-label">Turno</label>
                <select class="form-select" data-role="shift"><option value="">Selecione o Turno</option></select>
            </div>
            <div class="col-md-4">
                <label class="form-label">Projeto</label>
                <select class="form-select" data-role="project"><option value="">Selecione o Projeto</option></select>
            </div>
            <div class="col-md-4 d-flex align-items-end">
                <button type="button" class="btn btn-outline-primary w-100" data-role="addAll">
                    <i class="bi bi-plus-square"></i> Marcar Todas do Projeto
                </button>
            </div>
        </div>
        <div class="mb-2">
            <label class="form-label">Linhas</label>
            <div class="border rounded p-2" data-role="lines" style="max-height:200px; overflow-y:auto;"></div>
        </div>
        <div>
            <label class="form-label">Atribuições Selecionadas</label>
            <div data-role="selected"></div>
        </div>
    `;

    const shiftSel = root.querySelector('[data-role="shift"]');
    const projSel = root.querySelector('[data-role="project"]');
    const linesBox = root.querySelector('[data-role="lines"]');
    const selectedBox = root.querySelector('[data-role="selected"]');
    const addAllBtn = root.querySelector('[data-role="addAll"]');

    function distinctShifts() {
        return [...new Set(SCOPES.map(function (s) { return s.shift; }))].sort(function (a, b) { return a - b; });
    }
    function projectsFor(shift) {
        return [...new Set(SCOPES.filter(function (s) { return s.shift === shift; }).map(function (s) { return s.project; }))].sort();
    }
    function linesFor(shift, project) {
        return SCOPES.filter(function (s) { return s.shift === shift && s.project === project; });
    }

    function renderShifts() {
        let html = '<option value="">Selecione o Turno</option>';
        distinctShifts().forEach(function (sh) {
            html += '<option value="' + sh + '">Turno ' + sh + '</option>';
        });
        shiftSel.innerHTML = html;
        renderProjects();
    }
    function renderProjects() {
        const sh = parseInt(shiftSel.value);
        let html = '<option value="">Selecione o Projeto</option>';
        if (sh) {
            projectsFor(sh).forEach(function (pj) {
                html += '<option value="' + pj + '">' + (pj || '(sem projeto)') + '</option>';
            });
        }
        projSel.innerHTML = html;
        renderLines();
    }
    function renderLines() {
        const sh = parseInt(shiftSel.value);
        const pj = projSel.value;
        const lines = (sh && pj !== '') ? linesFor(sh, pj) : [];
        if (!lines.length) {
            linesBox.innerHTML = '<span class="text-muted">Nenhuma linha disponível.</span>';
            return;
        }
        linesBox.innerHTML = lines.map(function (s, i) {
            const cid = config.rootId + '_cb_' + i;
            const checked = selected.some(function (x) { return x.shift === s.shift && x.line_id === s.line_id; });
            return '<div class="form-check">' +
                '<input class="form-check-input scope-line-cb" type="checkbox" id="' + cid + '" value="' + s.line + '" data-shift="' + s.shift + '" data-line-id="' + s.line_id + '" data-project="' + (s.project || '') + '"' + (checked ? ' checked' : '') + '>' +
                '<label class="form-check-label" for="' + cid + '">' + s.line + '</label>' +
                '</div>';
        }).join('');
    }

    function addScope(shift, lineId, project, line) {
        if (selected.some(function (x) { return x.shift === shift && x.line_id === lineId; })) return;
        selected.push({ shift: shift, line_id: lineId, project: project, line: line });
        sync();
    }
    function removeScope(shift, lineId) {
        selected = selected.filter(function (x) { return !(x.shift === shift && x.line_id === lineId); });
        sync();
    }
    function sync() {
        hidden.value = JSON.stringify(selected.map(function (s) { return { shift: s.shift, line_id: s.line_id }; }));
        renderSelected();
    }
    function renderSelected() {
        if (!selected.length) {
            selectedBox.innerHTML = '<span class="text-muted">Nenhuma atribuição.</span>';
            return;
        }
        selectedBox.innerHTML = selected.map(function (s) {
            return '<span class="badge bg-primary me-1 mb-1">Turno ' + s.shift + (s.project ? ' - ' + s.project : '') + ' - ' + s.line +
                ' <button type="button" class="btn-close btn-close-white ms-1" style="font-size:0.6rem;" data-remove-shift="' + s.shift + '" data-remove-line-id="' + s.line_id + '"></button></span>';
        }).join('');
        selectedBox.querySelectorAll('[data-remove-shift]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                removeScope(parseInt(btn.getAttribute('data-remove-shift')), parseInt(btn.getAttribute('data-remove-line-id')));
            });
        });
    }

    shiftSel.addEventListener('change', renderProjects);
    projSel.addEventListener('change', renderLines);
    addAllBtn.addEventListener('click', function () {
        const sh = parseInt(shiftSel.value);
        const pj = projSel.value;
        if (!sh || pj === '') return;
        linesFor(sh, pj).forEach(function (s) { addScope(s.shift, s.line_id, s.project || '', s.line); });
    });
    linesBox.addEventListener('change', function (e) {
        if (e.target && e.target.classList.contains('scope-line-cb')) {
            const shift = parseInt(e.target.getAttribute('data-shift'));
            const lineId = parseInt(e.target.getAttribute('data-line-id'));
            const project = e.target.getAttribute('data-project') || '';
            const line = e.target.value;
            if (e.target.checked) addScope(shift, lineId, project, line);
            else removeScope(shift, lineId);
        }
    });

    renderShifts();
    sync();
}
