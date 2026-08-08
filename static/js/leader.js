/**
 * leader.js — Auto-fill check-in time on Early Exit modal.
 * Reads data-shift-start and data-attendance-checkin from the employee row.
 */
document.addEventListener('DOMContentLoaded', function() {
    // Intercept "Saída Antecipada" (data-needs-time="both") buttons
    document.querySelectorAll('.quick-action-btn[data-needs-time="both"]').forEach(function(btn) {
        btn.addEventListener('click', function() {
            var row = this.closest('tr');
            var existingCheckin = row.dataset.attendanceCheckin || '';
            var shiftStart = row.dataset.shiftStart || '';

            // Auto-fill the check-in input after the modal is shown
            setTimeout(function() {
                var checkinInput = document.getElementById('exitCheckIn');
                if (checkinInput && !checkinInput.value) {
                    checkinInput.value = existingCheckin || shiftStart || '';
                }
            }, 150);
        });
    });
});