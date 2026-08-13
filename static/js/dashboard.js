// Dashboard Chart.js initialization with multi-select and cascading filters

document.addEventListener('DOMContentLoaded', function() {
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

    // Load data
    loadDashboardData();

    // Apply filter button
    const applyFilterBtn = document.getElementById('applyFilter');
    if (applyFilterBtn) {
        applyFilterBtn.addEventListener('click', loadDashboardData);
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
            document.getElementById('kpiAbsenteeismRate').textContent = overview.absenteeism_rate + '%';
            document.getElementById('kpiLostHours').textContent = overview.total_lost_hours + 'h';
            document.getElementById('kpiTotalRecords').textContent = overview.total_records || 0;

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
            renderLineChart(lineData.lines || []);

            // Load by-project chart
            const projectRes = await fetch(`/dashboard/api/by-project?${queryString}`);
            const projectData = await projectRes.json();
            renderProjectChart(projectData.projects || []);

            // Load by-shift chart
            const shiftRes = await fetch(`/dashboard/api/by-shift?${queryString}`);
            const shiftData = await shiftRes.json();
            renderShiftChart(shiftData.shifts || []);

            // Load daily trend
            const trendRes = await fetch(`/dashboard/api/daily-trend?${queryString}`);
            const trendData = await trendRes.json();
            renderDailyTrend(trendData);

        } catch (error) {
            console.error('Error loading dashboard data:', error);
        }
    }

    // ─── CHART RENDERING ───

    function renderLineChart(lines) {
        const ctx = document.getElementById('chartByLine');
        if (!ctx) return;

        const labels = lines.map(l => l.line);
        const data = lines.map(l => l.absenteeism_rate);
        const lostHours = lines.map(l => l.lost_hours);

        if (chartByLine) chartByLine.destroy();

        chartByLine = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Taxa de Absenteísmo (%)',
                        data: data,
                        backgroundColor: 'rgba(220, 53, 69, 0.7)',
                        borderColor: 'rgba(220, 53, 69, 1)',
                        borderWidth: 2,
                        yAxisID: 'y'
                    },
                    {
                        label: 'Horas Perdidas',
                        data: lostHours,
                        backgroundColor: 'rgba(255, 193, 7, 0.7)',
                        borderColor: 'rgba(255, 193, 7, 1)',
                        borderWidth: 2,
                        yAxisID: 'y1'
                    }
                ]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: {
                        display: true,
                        position: 'top'
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                if (context.dataset.yAxisID === 'y') {
                                    return context.dataset.label + ': ' + context.parsed.y.toFixed(2) + '%';
                                }
                                return context.dataset.label + ': ' + context.parsed.y + 'h';
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: 'Taxa (%)' }
                    },
                    y1: {
                        beginAtZero: true,
                        position: 'right',
                        grid: { drawOnChartArea: false },
                        title: { display: true, text: 'Horas' }
                    }
                }
            }
        });
    }

    function renderProjectChart(projects) {
        const ctx = document.getElementById('chartByProject');
        if (!ctx) return;

        const labels = projects.map(p => p.project);
        const data = projects.map(p => p.absenteeism_rate);

        if (chartByProject) chartByProject.destroy();

        chartByProject = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Taxa de Absenteísmo (%)',
                    data: data,
                    backgroundColor: 'rgba(13, 110, 253, 0.7)',
                    borderColor: 'rgba(13, 110, 253, 1)',
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                return context.dataset.label + ': ' + context.parsed.y.toFixed(2) + '%';
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: 'Taxa (%)' }
                    }
                }
            }
        });
    }

    function renderShiftChart(shifts) {
        const ctx = document.getElementById('chartByShift');
        if (!ctx) return;

        const labels = shifts.map(s => 'Turno ' + s.shift);
        const rates = shifts.map(s => s.absenteeism_rate);
        const lostHours = shifts.map(s => s.lost_hours);

        if (chartByShift) chartByShift.destroy();

        chartByShift = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Taxa de Absenteísmo (%)',
                        data: rates,
                        backgroundColor: 'rgba(25, 135, 84, 0.7)',
                        borderColor: 'rgba(25, 135, 84, 1)',
                        borderWidth: 2,
                        yAxisID: 'y'
                    },
                    {
                        label: 'Horas Perdidas',
                        data: lostHours,
                        backgroundColor: 'rgba(108, 117, 125, 0.7)',
                        borderColor: 'rgba(108, 117, 125, 1)',
                        borderWidth: 2,
                        yAxisID: 'y1'
                    }
                ]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: true },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                if (context.dataset.yAxisID === 'y') {
                                    return context.dataset.label + ': ' + context.parsed.y.toFixed(2) + '%';
                                }
                                return context.dataset.label + ': ' + context.parsed.y + 'h';
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: 'Taxa (%)' }
                    },
                    y1: {
                        beginAtZero: true,
                        position: 'right',
                        grid: { drawOnChartArea: false },
                        title: { display: true, text: 'Horas' }
                    }
                }
            }
        });
    }

    function renderDailyTrend(trendData) {
        const ctx = document.getElementById('chartDailyTrend');
        if (!ctx) return;

        if (chartDailyTrend) chartDailyTrend.destroy();

        chartDailyTrend = new Chart(ctx, {
            type: 'line',
            data: {
                labels: trendData.dates || [],
                datasets: [
                    {
                        label: 'Ausências por Dia',
                        data: trendData.absent_counts || [],
                        borderColor: 'rgba(13, 110, 253, 1)',
                        backgroundColor: 'rgba(13, 110, 253, 0.1)',
                        tension: 0.4,
                        fill: true
                    },
                    {
                        label: 'Minutos Perdidos',
                        data: trendData.lost_minutes || [],
                        borderColor: 'rgba(220, 53, 69, 1)',
                        backgroundColor: 'rgba(220, 53, 69, 0.1)',
                        tension: 0.4,
                        fill: true
                    }
                ]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: true }
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
});