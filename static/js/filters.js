/**
 * filters.js — Shared filter utilities (cascading dropdowns, clear, debounced search)
 *
 * Usage:
 *   1. Include this script BEFORE page-specific scripts in your template.
 *   2. Call Filters.init(config) with your page's configuration.
 *
 * Config shape:
 *   {
 *     apiEndpoint: '/api/cascade-options',   // Backend endpoint for cascade data
 *     shiftSelectId: 'filterShift',
 *     projectSelectId: 'filterProject',
 *     lineSelectId: 'filterLine',
 *     formId: 'filterForm',                   // Form to submit/clear
 *     searchInputId: 'searchInput',           // Debounced search field (optional)
 *     debounceMs: 300,                        // Search debounce delay
 *     onFilterChange: null,                   // Callback after filters applied (optional)
 *     onClear: null,                          // Callback after clear filters (replaces form submit)
 *     multiSelect: true,                      // Whether selects are multi-select
 *     preserveSelections: true                // Restore selections after cascade update
 *   }
 */

const Filters = (function() {
    'use strict';

    let _config = {};

    const DEFAULTS = {
        apiEndpoint: '/api/cascade-options',
        shiftSelectId: 'filterShift',
        projectSelectId: 'filterProject',
        lineSelectId: 'filterLine',
        formId: 'filterForm',
        searchInputId: null,
        debounceMs: 300,
        onFilterChange: null,
        onClear: null,
        multiSelect: false,
        preserveSelections: true
    };

    // ─── PUBLIC API ────────────────────────────────────────────

    function init(config) {
        _config = Object.assign({}, DEFAULTS, config);
        _setupCascadeEvents();
        _setupClearButton();
        _setupDebouncedSearch();
        _setupPerPageChange();
        // Initial load of filter options
        loadFilterOptions();
    }

    function loadFilterOptions() {
        const shiftEl = document.getElementById(_config.shiftSelectId);
        const projectEl = document.getElementById(_config.projectSelectId);
        const lineEl = document.getElementById(_config.lineSelectId);

        const selectedShifts = _getSelectedValues(shiftEl);
        const selectedProjects = _getSelectedValues(projectEl);

        const params = new URLSearchParams();
        selectedShifts.forEach(s => params.append('shifts', s));
        selectedProjects.forEach(p => params.append('projects', p));

        return fetch(_config.apiEndpoint + '?' + params.toString())
            .then(res => res.json())
            .then(data => {
                _populateDropdown(shiftEl, data.shifts || []);
                _populateDropdown(projectEl, data.projects || []);
                _populateDropdown(lineEl, data.lines || []);

                if (_config.preserveSelections) {
                    _restoreSelections(shiftEl, selectedShifts);
                    _restoreSelections(projectEl, selectedProjects);
                    // Lines get reset when parent filters change (cascading behavior)
                    const selectedLines = _getSelectedValues(lineEl);
                    _restoreSelections(lineEl, selectedLines);
                }
            })
            .catch(err => console.error('Filters: failed to load options', err));
    }

    function clearAllFilters() {
        const form = document.getElementById(_config.formId);
        if (!form) return;

        // Reset all select elements — unselect every option (multi or single)
        form.querySelectorAll('select').forEach(select => {
            for (let i = 0; i < select.options.length; i++) {
                select.options[i].selected = false;
            }
            // Set to first option if single-select (not multiple)
            if (!select.multiple && select.options.length > 0) {
                select.options[0].selected = true;
            }
        });

        // Reset date inputs to today and 30 days ago
        const today = new Date();
        const thirtyDaysAgo = new Date(today);
        thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);

        form.querySelectorAll('input[type="date"]').forEach(input => {
            if (input.name === 'date_to' || input.name === 'dateTo') {
                input.value = today.toISOString().split('T')[0];
            } else if (input.name === 'date_from' || input.name === 'dateFrom') {
                input.value = thirtyDaysAgo.toISOString().split('T')[0];
            } else {
                input.value = today.toISOString().split('T')[0];
            }
        });

        // Reset search input
        const searchInput = document.getElementById(_config.searchInputId);
        if (searchInput) {
            searchInput.value = '';
        }

        // Reset per-page select to first option
        const perPageEl = form.querySelector('select[name="per_page"]');
        if (perPageEl && perPageEl.options.length > 0) {
            perPageEl.options[0].selected = true;
        }

        // Trigger callbacks
        if (_config.onFilterChange) _config.onFilterChange();

        // Reload full filter options from server (without active selections),
        // then trigger onClear callback or submit form
        loadFilterOptions().then(function() {
            if (_config.onClear) {
                _config.onClear();
            } else {
                _submitForm();
            }
        }).catch(function() {
            // Fallback: still trigger callback even if fetch fails
            if (_config.onClear) {
                _config.onClear();
            } else {
                _submitForm();
            }
        });
    }

    // ─── PRIVATE HELPERS ───────────────────────────────────────

    function _setupCascadeEvents() {
        const shiftEl = document.getElementById(_config.shiftSelectId);
        const projectEl = document.getElementById(_config.projectSelectId);

        if (shiftEl) {
            shiftEl.addEventListener('change', function() {
                loadFilterOptions();
            });
        }
        if (projectEl) {
            projectEl.addEventListener('change', function() {
                loadFilterOptions();
            });
        }
    }

    function _setupClearButton() {
        const form = document.getElementById(_config.formId);
        if (!form) return;

        // Look for an existing clear button or create one dynamically
        const existingBtn = form.querySelector('[data-action="clear-filters"]');
        if (existingBtn) {
            existingBtn.addEventListener('click', function(e) {
                e.preventDefault();
                clearAllFilters();
            });
        }
    }

    function _setupDebouncedSearch() {
        const searchInputId = _config.searchInputId;
        if (!searchInputId) return;

        const input = document.getElementById(searchInputId);
        if (!input) return;

        let timer = null;
        const delay = _config.debounceMs;

        input.addEventListener('input', function() {
            clearTimeout(timer);
            timer = setTimeout(function() {
                if (_config.onFilterChange) _config.onFilterChange();
                _submitForm();
            }, delay);
        });

        // Also submit on Enter key immediately
        input.addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                clearTimeout(timer);
                if (_config.onFilterChange) _config.onFilterChange();
                _submitForm();
            }
        });
    }

    function _setupPerPageChange() {
        const form = document.getElementById(_config.formId);
        if (!form) return;

        const perPageEl = form.querySelector('select[name="per_page"]');
        if (perPageEl) {
            perPageEl.addEventListener('change', function() {
                // Reset to page 1 when changing page size
                const pageInput = form.querySelector('input[name="page"]');
                if (pageInput) pageInput.value = '1';
                _submitForm();
            });
        }
    }

    function _submitForm() {
        const form = document.getElementById(_config.formId);
        if (!form) return;

        // If the form has a submit button, use requestSubmit for proper event handling
        if (typeof form.requestSubmit === 'function') {
            form.requestSubmit();
        } else {
            form.submit();
        }
    }

    function _getSelectedValues(el) {
        if (!el) return [];
        if (el.multiple) {
            return [...el.selectedOptions].map(o => o.value).filter(v => v);
        }
        return el.value ? [el.value] : [];
    }

    function _restoreSelections(el, values) {
        if (!el || !values.length) return;
        for (const option of el.options) {
            if (values.includes(option.value)) {
                option.selected = true;
            }
        }
    }

    function _populateDropdown(el, options) {
        if (!el) return;

        const currentSelections = _getSelectedValues(el);

        // Clear existing options
        el.innerHTML = '';

        // Add default empty option for single-select
        if (!el.multiple) {
            const defaultOpt = document.createElement('option');
            defaultOpt.value = '';
            defaultOpt.textContent = el.dataset.placeholder || 'Todos';
            el.appendChild(defaultOpt);
        }

        // Add new options
        options.forEach(optValue => {
            const opt = document.createElement('option');
            opt.value = optValue;
            opt.textContent = optValue;
            el.appendChild(opt);
        });

        // Restore previous selections (except for cascading resets)
        _restoreSelections(el, currentSelections);
    }

    // ─── EXPORT ─────────────────────────────────────────────────

    return {
        init: init,
        loadFilterOptions: loadFilterOptions,
        clearAllFilters: clearAllFilters
    };
})();