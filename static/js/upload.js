// Two-step dry-run upload: analyze the spreadsheet first, then confirm import.
document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('uploadForm');
    const fileInput = document.getElementById('excel_file');
    const analyzeBtn = document.getElementById('analyzeBtn');
    const modalEl = document.getElementById('modalPreviewUpload');
    const confirmBtn = document.getElementById('confirmImportBtn');
    const cancelBtn = document.getElementById('cancelImportBtn');

    if (!form || !modalEl) return;

    const modal = new bootstrap.Modal(modalEl);

    const elTotalRows = document.getElementById('pv_total_rows');
    const elNewEmp = document.getElementById('pv_new_employees');
    const elExistingEmp = document.getElementById('pv_existing_employees');
    const elAllocations = document.getElementById('pv_allocations');
    const elNewLines = document.getElementById('pv_new_lines');

    let currentWarnings = [];

    form.addEventListener('submit', async function (e) {
        e.preventDefault();

        if (!fileInput.files.length) {
            alert('Selecione um arquivo .xlsx primeiro.');
            return;
        }

        setAnalyzeLoading(true);
        const fd = new FormData(form);

        try {
            const res = await fetch('/admin/upload/analyze', { method: 'POST', body: fd });
            const data = await res.json();

            if (!res.ok || data.status !== 'success') {
                alert(data.message || 'Erro ao analisar a planilha.');
                return;
            }

            populateModal(data.summary);
            modal.show();
        } catch (err) {
            console.error('Erro ao analisar planilha:', err);
            alert('Erro ao analisar a planilha.');
        } finally {
            setAnalyzeLoading(false);
        }
    });

    function populateModal(s) {
        if (elTotalRows) elTotalRows.textContent = s.total_rows ?? 0;
        if (elNewEmp) elNewEmp.textContent = s.new_employees_count ?? 0;
        if (elExistingEmp) elExistingEmp.textContent = s.existing_employees_count ?? 0;
        if (elAllocations) elAllocations.textContent = s.allocations_count ?? 0;

        if (elNewLines) {
            const lines = s.new_lines_detected || [];
            if (lines.length === 0) {
                elNewLines.innerHTML = '<li class="text-muted">Nenhuma linha nova detectada</li>';
            } else {
                elNewLines.innerHTML = lines.map(function (l) {
                    return '<li>' + escapeHtml(l.project) + ' &mdash; ' + escapeHtml(l.line) + '</li>';
                }).join('');
            }
        }

        renderMatriculaWarnings(s.matricula_warnings || []);
    }

    function renderMatriculaWarnings(warnings) {
        currentWarnings = warnings || [];
        const warnBox = document.getElementById('matriculaWarningBox');
        const warnList = document.getElementById('pv_matricula_warnings');
        if (!warnBox || !warnList) return;

        if (currentWarnings.length === 0) {
            warnBox.classList.add('d-none');
            warnList.innerHTML = '';
        } else {
            warnBox.classList.remove('d-none');
            warnList.innerHTML = currentWarnings.map(function (w, i) {
                return '<li class="mb-2">' +
                    '<div>O funcionário <strong>' + escapeHtml(w.employee_name) +
                    '</strong> já existe com a matrícula <strong>' + escapeHtml(w.existing_id) +
                    '</strong>, mas a planilha enviou <strong>' + escapeHtml(w.spreadsheet_id) + '</strong>.</div>' +
                    '<div class="mt-1">' +
                    '<div class="form-check form-check-inline">' +
                    '<input class="form-check-input" type="radio" name="warn_' + i + '" id="warn_' + i + '_migrate" value="migrate" checked>' +
                    '<label class="form-check-label" for="warn_' + i + '_migrate">Migrar ' + escapeHtml(w.existing_id) + ' &rarr; ' + escapeHtml(w.spreadsheet_id) + '</label>' +
                    '</div>' +
                    '<div class="form-check form-check-inline">' +
                    '<input class="form-check-input" type="radio" name="warn_' + i + '" id="warn_' + i + '_skip" value="skip">' +
                    '<label class="form-check-label" for="warn_' + i + '_skip">Ignorar (manter ' + escapeHtml(w.existing_id) + ')</label>' +
                    '</div>' +
                    '</div>' +
                    '</li>';
            }).join('');
        }

        updateConfirmState();
    }

    function collectMigrations() {
        const migrations = [];
        currentWarnings.forEach(function (w, i) {
            const radio = document.getElementById('warn_' + i + '_migrate');
            if (radio && radio.checked) {
                migrations.push({ old_id: w.existing_id, new_id: w.spreadsheet_id });
            }
        });
        return migrations;
    }

    function updateConfirmState() {
        if (!confirmBtn) return;
        confirmBtn.disabled = false;
        confirmBtn.innerHTML = '<i class="bi bi-check-lg"></i> Confirmar e Importar';
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str == null ? '' : String(str);
        return div.innerHTML;
    }

    function setAnalyzeLoading(loading) {
        if (!analyzeBtn) return;
        analyzeBtn.disabled = loading;
        analyzeBtn.innerHTML = loading
            ? '<span class="spinner-border spinner-border-sm me-1"></span> Analisando...'
            : '<i class="bi bi-search"></i> Analisar Planilha';
    }

    if (cancelBtn) {
        cancelBtn.addEventListener('click', function () {
            modal.hide();
            form.reset();
        });
    }

    // Reset the file input whenever the modal is dismissed (X button, etc.).
    modalEl.addEventListener('hidden.bs.modal', function () {
        form.reset();
    });

    if (confirmBtn) {
        confirmBtn.addEventListener('click', async function () {
            confirmBtn.disabled = true;
            confirmBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Importando...';

            try {
                const res = await fetch('/admin/upload/confirm', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ migrations: collectMigrations() })
                });
                const data = await res.json();

                if (!res.ok || data.status !== 'success') {
                    alert(data.message || 'Erro ao importar.');
                    updateConfirmState();
                    return;
                }

                modal.hide();
                window.location.href = '/admin/upload';
            } catch (err) {
                console.error('Erro ao importar:', err);
                alert('Erro ao importar.');
                updateConfirmState();
            }
        });
    }
});
