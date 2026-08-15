// Dashboard Chart.js initialization with multi-select and cascading filters

document.addEventListener('DOMContentLoaded', function() {
    // ─── DIRECT DATA LABELS + TREND SUPPORT ───
    const dataLabelPlugin = {
        id: 'customDataLabels',
        afterDatasetsDraw(chart) {
            if (!chart._showDataLabels) return;
            const meta = chart.getDatasetMeta(0);
            if (!meta || !meta.data || meta.data.length === 0) return;
            const ds = chart.data.datasets[0];
            const ctx = chart.ctx;
            ctx.save();
            ctx.font = 'bold 11px Segoe UI, Tahoma, sans-serif';
            ctx.fillStyle = '#0f172a';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'bottom';
            meta.data.forEach(function(bar, i) {
                const txt = metricValueLabel(ds.data[i], chart._showDataMetric);
                ctx.fillText(txt, bar.x, bar.y - 4);
            });
            ctx.restore();
        }
    };
    Chart.register(dataLabelPlugin);

    function computeTrend(values) {
        const n = values.length;
        if (n < 2) return values.slice();
        let sumX = 0, sumY = 0, sumXY = 0, sumXX = 0;
        for (let i = 0; i < n; i++) {
            sumX += i;
            sumY += values[i];
            sumXY += i * values[i];
            sumXX += i * i;
        }
        const denom = n * sumXX - sumX * sumX;
        const slope = denom !== 0 ? (n * sumXY - sumX * sumY) / denom : 0;
        const intercept = (sumY - slope * sumX) / n;
        return values.map(function(_, i) { return Math.round((intercept + slope * i) * 100) / 100; });
    }

    function metricValueLabel(val, metric) {
        if (metric === 'absences') return val + ' faltas';
        if (metric === 'hours') return val + 'h';
        return val + '%';
    }

    function updateTrendDataset(chart) {
        const trendDs = chart.data.datasets.find(function(d) { return d._isTrend; });
        if (trendDs) trendDs.data = computeTrend(chart.data.datasets[0].data);
    }

    function setTrendMode(chart, on) {
        if (!chart) return;
        if (on) {
            const existingIdx = chart.data.datasets.findIndex(function(d) { return d._isTrend; });
            if (existingIdx !== -1) {
                chart.data.datasets[existingIdx].data = computeTrend(chart.data.datasets[0].data);
            } else {
                chart.data.datasets.push({
                    _isTrend: true,
                    type: 'line',
                    label: 'Tendência',
                    data: computeTrend(chart.data.datasets[0].data),
                    borderColor: '#3B82F6',
                    borderDash: [5, 5],
                    fill: false,
                    tension: 0.3,
                    pointRadius: 0,
                    borderWidth: 2
                });
            }
            chart.options.plugins.tooltip.enabled = false;
            chart._showDataLabels = true;
        } else {
            const idx = chart.data.datasets.findIndex(function(d) { return d._isTrend; });
            if (idx !== -1) chart.data.datasets.splice(idx, 1);
            chart.options.plugins.tooltip.enabled = true;
            chart._showDataLabels = false;
        }
        chart._trendActive = on;
        chart.update();
    }

    // Set default date range (last 30 days)
    const today = new Date();
    const thirtyDaysAgo = new Date(today);
    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);

    const dateFrom = document.getElementById('dateFrom');
    const dateTo = document.getElementById('dateTo');

    if (dateFrom) dateFrom.value = formatDate(thirtyDaysAgo);
    if (dateTo) dateTo.value = formatDate(today);

    // Load filter options initially (no cascade yet)
    loadFilterOptions();

    // Charts
    let chartByLine, chartByProject, chartByShift, chartDailyTrend;
    let lineDataCache = [];
    let projectDataCache = [];
    let shiftDataCache = [];
    let currentLineSort = 'rate';
    let currentProjectSort = 'rate';
    let currentShiftSort = 'rate';

    // Load data
    loadDashboardData();

    // Apply filter button
    const applyFilterBtn = document.getElementById('applyFilter');
    if (applyFilterBtn) {
        applyFilterBtn.addEventListener('click', loadDashboardData);
    }

    // Chart sorting switchers (Linha, Projeto, Turno)
    function bindSortButtons(groupId, setMetric, renderFn) {
        const buttons = document.querySelectorAll(groupId + ' [data-sort]');
        buttons.forEach(function(btn) {
            btn.addEventListener('click', function() {
                setMetric(btn.getAttribute('data-sort'));
                buttons.forEach(function(b) { b.classList.remove('active'); });
                btn.classList.add('active');
                renderFn();
            });
        });
    }
    bindSortButtons('#lineSortGroup', function(v) { currentLineSort = v; }, renderLineChart);
    bindSortButtons('#projectSortGroup', function(v) { currentProjectSort = v; }, renderProjectChart);
    bindSortButtons('#shiftSortGroup', function(v) { currentShiftSort = v; }, renderShiftChart);

    // Trend toggle buttons (Linha, Projeto, Turno)
    const trendButtons = document.querySelectorAll('.btn-toggle-trend');
    trendButtons.forEach(function(btn) {
        btn.addEventListener('click', function() {
            const chartKey = btn.getAttribute('data-chart');
            const chart = chartKey === 'line' ? chartByLine : (chartKey === 'project' ? chartByProject : chartByShift);
            if (!chart) return;
            const on = !chart._trendActive;
            setTrendMode(chart, on);
            btn.classList.toggle('active', on);
        });
    });

    // ─── COLLAPSIBLE FILTERS ───
    const filterBody = document.getElementById('filterBody');
    const btnToggleFilters = document.getElementById('btnToggleFilters');
    if (filterBody && btnToggleFilters) {
        const collapsedPref = localStorage.getItem('dashFiltersCollapsed') === '1';
        if (collapsedPref) {
            filterBody.classList.remove('show');
            btnToggleFilters.innerHTML = '🔍 Mostrar Filtros' + filterBadgeHtml();
            btnToggleFilters.setAttribute('aria-expanded', 'false');
        }
        filterBody.addEventListener('hidden.bs.collapse', function() {
            btnToggleFilters.innerHTML = '🔍 Mostrar Filtros' + filterBadgeHtml();
            localStorage.setItem('dashFiltersCollapsed', '1');
        });
        filterBody.addEventListener('shown.bs.collapse', function() {
            btnToggleFilters.innerHTML = '🔽 Ocultar Filtros';
            localStorage.setItem('dashFiltersCollapsed', '0');
        });
    }

    function getActiveFilterCount() {
        let count = 0;
        ['filterShift', 'filterProject', 'filterLine'].forEach(function(id) {
            const el = document.getElementById(id);
            if (el) count += [...el.selectedOptions].filter(o => o.value).length;
        });
        return count;
    }

    function filterBadgeHtml() {
        const n = getActiveFilterCount();
        if (n > 0) return ' <span class="badge rounded-pill bg-primary">' + n + ' ativos</span>';
        return '';
    }

    // Export Excel button
    const exportBtn = document.getElementById('exportExcelBtn');
    if (exportBtn) {
        exportBtn.addEventListener('click', function() {
            const params = getFilterValues();
            const exportParams = new URLSearchParams();
            if (params.date_from) exportParams.set('start_date', params.date_from);
            if (params.date_to) exportParams.set('end_date', params.date_to);
            // Multi-value params
            params.shift.forEach(s => exportParams.append('shift', s));
            params.project.forEach(p => exportParams.append('project', p));
            params.line.forEach(l => exportParams.append('line', l));
            window.location.href = '/admin/export-excel?' + exportParams.toString();
        });
    }

    // Load Bradford Factor risks
    loadBradfordData();

    // ─── CASCADING FILTER EVENTS ───
    const filterShift = document.getElementById('filterShift');
    const filterProject = document.getElementById('filterProject');

    if (filterShift) {
        filterShift.addEventListener('change', function() {
            loadFilterOptions(); // Re-fetch projects & lines based on selected shifts
        });
    }
    if (filterProject) {
        filterProject.addEventListener('change', function() {
            loadFilterOptions(); // Re-fetch lines based on selected shifts + projects
        });
    }

    // ─── HELPER FUNCTIONS ───

    function formatDate(date) {
        return date.toISOString().split('T')[0];
    }

    function getFilterValues() {
        const filterShift = document.getElementById('filterShift');
        const filterProject = document.getElementById('filterProject');
        const filterLine = document.getElementById('filterLine');

        const shifts = filterShift ? [...filterShift.selectedOptions].map(o => o.value).filter(v => v) : [];
        const projects = filterProject ? [...filterProject.selectedOptions].map(o => o.value).filter(v => v) : [];
        const lines = filterLine ? [...filterLine.selectedOptions].map(o => o.value).filter(v => v) : [];

        return {
            date_from: dateFrom ? dateFrom.value : '',
            date_to: dateTo ? dateTo.value : '',
            shift: shifts,
            project: projects,
            line: lines
        };
    }

    function buildQueryString(params) {
        const queryParams = new URLSearchParams();
        if (params.date_from) queryParams.set('date_from', params.date_from);
        if (params.date_to) queryParams.set('date_to', params.date_to);
        // Multi-value params — send as repeated keys
        params.shift.forEach(s => queryParams.append('shift', s));
        params.project.forEach(p => queryParams.append('project', p));
        params.line.forEach(l => queryParams.append('line', l));
        return queryParams.toString();
    }

    async function loadFilterOptions() {
        try {
            const shifts = getSelectedValues('filterShift');
            const projects = getSelectedValues('filterProject');

            // Build query string for cascade
            const cascadeParams = new URLSearchParams();
            shifts.forEach(s => cascadeParams.append('shifts', s));
            projects.forEach(p => cascadeParams.append('projects', p));

            const res = await fetch('/dashboard/api/filter-options?' + cascadeParams.toString());
            const data = await res.json();

            populateDropdownMulti('filterShift', data.shifts || []);
            populateDropdownMulti('filterProject', data.projects || []);
            populateDropdownMulti('filterLine', data.lines || []);

            // Restore previous selections after re-populating
            restoreSelections('filterShift', shifts);
            restoreSelections('filterProject', projects);
            // Lines get reset when projects change — that's the cascading behavior
            restoreSelections('filterLine', getSelectedValues('filterLine'));
        } catch (error) {
            console.error('Error loading filter options:', error);
        }
    }

    function getSelectedValues(elementId) {
        const select = document.getElementById(elementId);
        if (!select) return [];
        return [...select.selectedOptions].map(o => o.value).filter(v => v);
    }

    function restoreSelections(elementId, values) {
        const select = document.getElementById(elementId);
        if (!select || !values.length) return;
        for (const option of select.options) {
            if (values.includes(option.value)) {
                option.selected = true;
            }
        }
    }

    function populateDropdownMulti(elementId, options) {
        const select = document.getElementById(elementId);
        if (!select) return;

        // Remember current selections before clearing
        const currentSelections = getSelectedValues(elementId);

        select.innerHTML = '';
        options.forEach(opt => {
            const option = document.createElement('option');
            option.value = opt;
            option.textContent = typeof opt === 'number' ? 'Turno ' + opt : opt;
            // Restore selection if previously selected
            if (currentSelections.includes(String(opt))) {
                option.selected = true;
            }
            select.appendChild(option);
        });
    }

    // ─── DATA LOADING ───

    async function loadDashboardData() {
        try {
            const params = getFilterValues();
            const queryString = buildQueryString(params);

            // Load overview KPIs
            const overviewRes = await fetch(`/dashboard/api/overview?${queryString}`);
            const overview = await overviewRes.json();

            // Update KPI cards
            document.getElementById('kpiTotalEmployees').textContent = overview.total_employees || 0;
            document.getElementById('kpiVacationCount').textContent = overview.vacation_count || 0;
            const presenceRate = (overview.presence_rate != null) ? overview.presence_rate : (100 - (overview.absenteeism_rate || 0));
            document.getElementById('kpiPresenceRate').textContent = Number(presenceRate).toFixed(2) + '%';
            document.getElementById('kpiAbsenteeismRate').textContent = overview.absenteeism_rate + '%';
            document.getElementById('kpiLostHours').textContent = overview.total_lost_hours + 'h';

            // Pending validations alert
            const pendingAlert = document.getElementById('pendingAlert');
            const pendingText = document.getElementById('pendingText');
            if (overview.pending_validations && overview.pending_validations > 0) {
                pendingAlert.style.display = 'block';
                pendingText.textContent =
                    `⚠️ ${overview.pending_validations} linha(s)/turno(s) possuem registros de presença que ainda não foram auditados pelo líder no período.`;
            } else {
                pendingAlert.style.display = 'none';
            }

            // Load by-line chart
            const lineRes = await fetch(`/dashboard/api/by-line?${queryString}`);
            const lineData = await lineRes.json();
            lineDataCache = lineData.lines || [];
            renderLineChart();

            // Load by-project chart
            const projectRes = await fetch(`/dashboard/api/by-project?${queryString}`);
            const projectData = await projectRes.json();
            projectDataCache = projectData.projects || [];
            renderProjectChart();

            // Load by-shift chart
            const shiftRes = await fetch(`/dashboard/api/by-shift?${queryString}`);
            const shiftData = await shiftRes.json();
            shiftDataCache = shiftData.shifts || [];
            renderShiftChart();

            // Load daily trend
            const trendRes = await fetch(`/dashboard/api/daily-trend?${queryString}`);
            const trendData = await trendRes.json();
            renderDailyTrend(trendData);

            // Charts are re-created on fresh data load — reset trend toggles.
            document.querySelectorAll('.btn-toggle-trend').forEach(function(b) { b.classList.remove('active'); });

        } catch (error) {
            console.error('Error loading dashboard data:', error);
        }
    }

    // ─── CHART RENDERING ───

    function setEmptyState(canvasId, isEmpty) {
        const canvas = document.getElementById(canvasId);
        if (!canvas) return;
        const container = canvas.parentElement;
        let msg = container.querySelector('.chart-empty-message');
        if (isEmpty) {
            canvas.style.display = 'none';
            if (!msg) {
                msg = document.createElement('div');
                msg.className = 'chart-empty-message text-center text-muted py-5';
                msg.textContent = 'Nenhum registro encontrado para o filtro aplicado';
                container.appendChild(msg);
            }
        } else {
            canvas.style.display = '';
            if (msg) msg.remove();
        }
    }

    function sortValue(item, metric) {
        if (metric === 'absences') return item.absences_count || 0;
        if (metric === 'hours') return item.lost_hours || 0;
        return item.rate || 0;
    }

    function axisLabel(metric) {
        if (metric === 'absences') return 'Faltas';
        if (metric === 'hours') return 'Horas';
        return 'Taxa (%)';
    }

    function sortLabel(metric) {
        if (metric === 'absences') return 'Quantidade de Faltas';
        if (metric === 'hours') return 'Horas Perdidas (h)';
        return 'Taxa de Absenteísmo (%)';
    }

    function buildSortedBarChart(canvasId, currentChart, cache, sortMetric, labelFn, tooltipFn) {
        const ctx = document.getElementById(canvasId);
        if (!ctx) return null;

        const items = cache.slice().sort(function(a, b) {
            return sortValue(b, sortMetric) - sortValue(a, sortMetric);
        });
        const labels = items.map(labelFn);
        const data = items.map(function(it) { return sortValue(it, sortMetric); });

        setEmptyState(canvasId, labels.length === 0);

        // Update in place when the chart already exists (keeps trend/data-label state).
        if (currentChart) {
            if (labels.length === 0) {
                currentChart.destroy();
                return null;
            }
            currentChart.data.labels = labels;
            currentChart.data.datasets[0].data = data;
            currentChart.data.datasets[0]._rawMeta = items;
            currentChart.options.scales.y.title.text = axisLabel(sortMetric);
            currentChart._showDataMetric = sortMetric;
            updateTrendDataset(currentChart);
            currentChart.update();
            return currentChart;
        }

        if (labels.length === 0) return null;

        const chart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: sortLabel(sortMetric),
                    data: data,
                    backgroundColor: '#EF4444',
                    hoverBackgroundColor: '#DC2626',
                    borderColor: '#EF4444',
                    borderWidth: 1,
                    borderRadius: 6,
                    _rawMeta: items
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            title: function() { return ''; },
                            label: function(context) {
                                const raw = context.dataset._rawMeta ? context.dataset._rawMeta[context.dataIndex] : null;
                                if (!raw) return context.label + ': ' + context.raw;
                                return tooltipFn(raw);
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: axisLabel(sortMetric) }
                    }
                }
            }
        });
        chart._showDataLabels = false;
        chart._showDataMetric = sortMetric;
        return chart;
    }

    function renderLineChart() {
        chartByLine = buildSortedBarChart(
            'chartByLine', chartByLine, lineDataCache, currentLineSort,
            l => l.line,
            l => l.line + ': ' + l.rate + '% | ' + l.absences_count + ' ausência(s) | ' + l.lost_hours + 'h perdidas'
        );
    }

    function renderProjectChart() {
        chartByProject = buildSortedBarChart(
            'chartByProject', chartByProject, projectDataCache, currentProjectSort,
            p => p.project,
            p => p.project + ': ' + p.rate + '% | ' + p.absences_count + ' ausência(s) | ' + p.lost_hours + 'h perdidas'
        );
    }

    function renderShiftChart() {
        chartByShift = buildSortedBarChart(
            'chartByShift', chartByShift, shiftDataCache, currentShiftSort,
            s => 'Turno ' + s.shift,
            s => 'Turno ' + s.shift + ': ' + s.rate + '% | ' + s.absences_count + ' ausência(s) | ' + s.lost_hours + 'h perdidas'
        );
    }

    function renderDailyTrend(trendData) {
        const ctxEl = document.getElementById('chartDailyTrend');
        if (!ctxEl) return;

        const labels = trendData.dates || [];
        const absentCounts = trendData.absent_counts || [];
        const lostMinutes = trendData.lost_minutes || [];

        setEmptyState('chartDailyTrend', labels.length === 0);
        if (chartDailyTrend) chartDailyTrend.destroy();
        if (labels.length === 0) return;

        const ctx2d = ctxEl.getContext('2d');
        const gradient = ctx2d.createLinearGradient(0, 0, 0, 300);
        gradient.addColorStop(0, 'rgba(59, 130, 246, 0.15)');
        gradient.addColorStop(1, 'rgba(59, 130, 246, 0)');

        chartDailyTrend = new Chart(ctxEl, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Ausências por Dia',
                        data: absentCounts,
                        borderColor: '#3B82F6',
                        backgroundColor: gradient,
                        tension: 0.35,
                        fill: true,
                        pointRadius: 3,
                        pointBackgroundColor: '#3B82F6'
                    },
                    {
                        label: 'Minutos Perdidos',
                        data: lostMinutes,
                        borderColor: '#EF4444',
                        backgroundColor: 'rgba(239, 68, 68, 0.1)',
                        tension: 0.35,
                        fill: true,
                        pointRadius: 3,
                        pointBackgroundColor: '#EF4444'
                    }
                ]
            },
            options: {
                responsive: true,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { display: true },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                return context.dataset.label + ': ' + context.parsed.y;
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: 'Quantidade' }
                    }
                }
            }
        });
    }

    // ─── BRADFORD ───

    async function loadBradfordData() {
        try {
            const res = await fetch('/dashboard/api/bradford-top-risks');
            const data = await res.json();

            const risks = data.risks || [];
            const row = document.getElementById('bradfordRisksRow');
            const tbody = document.getElementById('bradfordTableBody');

            if (!row || !tbody) return;

            if (risks.length === 0) {
                row.style.display = 'none';
                return;
            }

            row.style.display = 'flex';
            tbody.innerHTML = '';

            risks.forEach(r => {
                let badgeHtml = '';
                if (r.risk_level === 'high') {
                    badgeHtml = '<span class="badge bg-danger">🔴 Alto</span>';
                } else if (r.risk_level === 'moderate') {
                    badgeHtml = '<span class="badge bg-warning text-dark">🟡 Moderado</span>';
                } else {
                    badgeHtml = '<span class="badge bg-success">🟢 Baixo</span>';
                }

                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><strong>${r.employee_name}</strong> <small class="text-muted">(${r.employee_id})</small></td>
                    <td><strong>${r.bradford_score}</strong></td>
                    <td>${r.spells}</td>
                    <td>${r.total_days}</td>
                    <td>${badgeHtml}</td>
                `;
                tbody.appendChild(tr);
            });
        } catch (error) {
            console.error('Error loading Bradford data:', error);
        }
    }

    // ─── PENDING AUDITS MODAL ───

    const pendingAlertBox = document.getElementById('pendingAlertBox');
    if (pendingAlertBox) {
        pendingAlertBox.addEventListener('click', function() {
            const modalEl = document.getElementById('modalPendingAudits');
            const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
            modal.show();
            loadPendingAudits();
        });
    }

    async function loadPendingAudits() {
        const body = document.getElementById('pendingAuditsBody');
        if (!body) return;
        try {
            const params = getFilterValues();
            const qs = buildQueryString(params);
            const res = await fetch(`/dashboard/api/pending-audits?${qs}`);
            const data = await res.json();
            const items = data.pending || [];

            if (items.length === 0) {
                body.innerHTML = '<tr><td colspan="5" class="text-center text-muted">Nenhum registro pendente de auditoria.</td></tr>';
                return;
            }

            body.innerHTML = '';
            items.forEach(item => {
                const dateFmt = item.date ? item.date.split('-').reverse().join('/') : '—';
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${dateFmt}</td>
                    <td>Turno ${item.shift}</td>
                    <td>${item.line}</td>
                    <td>${item.leader}</td>
                    <td><span class="badge bg-warning text-dark">${item.status}</span></td>
                `;
                body.appendChild(tr);
            });
        } catch (error) {
            console.error('Error loading pending audits:', error);
            body.innerHTML = '<tr><td colspan="5" class="text-center text-danger">Erro ao carregar pendências.</td></tr>';
        }
    }
});