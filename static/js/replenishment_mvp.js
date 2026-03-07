document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.final-cases-input').forEach(function(input) {
        input.addEventListener('input', function() {
            var caseQty = parseFloat(this.dataset.caseQty) || 0;
            var lineId = this.dataset.lineId;
            var finalCases = parseInt(this.value) || 0;
            var finalUnits = finalCases * caseQty;
            var unitCell = document.getElementById('final-units-' + lineId);
            if (unitCell) {
                unitCell.textContent = Math.round(finalUnits);
            }
        });
    });
});
