// JavaScript functions for batch picking
document.addEventListener('DOMContentLoaded', function() {
    // Select all zones
    var selectAllZonesBtn = document.getElementById('selectAllZones');
    if (selectAllZonesBtn) {
        selectAllZonesBtn.addEventListener('click', function() {
            var checkboxes = document.querySelectorAll('.zone-checkbox');
            checkboxes.forEach(function(checkbox) {
                checkbox.checked = true;
            });
        });
    }

    // Deselect all zones
    var deselectAllZonesBtn = document.getElementById('deselectAllZones');
    if (deselectAllZonesBtn) {
        deselectAllZonesBtn.addEventListener('click', function() {
            var checkboxes = document.querySelectorAll('.zone-checkbox');
            checkboxes.forEach(function(checkbox) {
                checkbox.checked = false;
            });
        });
    }

    // Select all invoices
    var selectAllInvoicesBtn = document.getElementById('selectAllInvoices');
    if (selectAllInvoicesBtn) {
        selectAllInvoicesBtn.addEventListener('click', function() {
            var checkboxes = document.querySelectorAll('.invoice-checkbox');
            checkboxes.forEach(function(checkbox) {
                checkbox.checked = selectAllInvoicesBtn.checked;
            });
        });
    }

    // Filter invoices by selected zones
    var filterButton = document.getElementById('filterButton');
    if (filterButton) {
        filterButton.addEventListener('click', function() {
            var selectedZones = [];
            var zoneCheckboxes = document.querySelectorAll('input[name="zones"]:checked');
            
            zoneCheckboxes.forEach(function(checkbox) {
                selectedZones.push(checkbox.value);
            });
            
            if (selectedZones.length === 0) {
                alert("Please select at least one zone.");
                return;
            }
            
            // Show loading indicator
            var loadingIndicator = document.getElementById('loading-indicator');
            var invoiceTable = document.getElementById('invoice-table');
            
            if (loadingIndicator) loadingIndicator.classList.remove('d-none');
            if (invoiceTable) invoiceTable.classList.add('d-none');
            
            // Build URL with selected zones
            var url = document.getElementById('filter-url').value + '?';
            selectedZones.forEach(function(zone) {
                url += "zones=" + encodeURIComponent(zone) + "&";
            });
            
            // Add include_partially_picked parameter
            var includePartiallyPicked = document.getElementById('include_partially_picked');
            var includePartial = includePartiallyPicked ? includePartiallyPicked.checked : false;
            url += "include_partially_picked=" + includePartial;
            
            // Fetch filtered invoices
            fetch(url)
                .then(function(response) { return response.json(); })
                .then(function(data) {
                    console.log("Filtered invoices:", data);
                    
                    // Hide loading indicator
                    if (loadingIndicator) loadingIndicator.classList.add('d-none');
                    if (invoiceTable) invoiceTable.classList.remove('d-none');
                    
                    // Update the form action with the selected zones
                    var batchForm = document.getElementById('batchForm');
                    var createBatchUrl = document.getElementById('create-batch-url').value;
                    var sessionName = document.getElementById('session_name');
                    
                    var formAction = createBatchUrl + "?";
                    selectedZones.forEach(function(zone) {
                        formAction += "zones=" + encodeURIComponent(zone) + "&";
                    });
                    
                    formAction += "session_name=" + encodeURIComponent(sessionName ? sessionName.value : '') + "&";
                    formAction += "include_partially_picked=" + includePartial;
                    
                    if (batchForm) batchForm.setAttribute("action", formAction);
                    
                    // Update the table
                    if (data.invoices && data.invoices.length > 0) {
                        var createBatchButton = document.getElementById('createBatchButton');
                        if (createBatchButton) createBatchButton.disabled = false;
                        
                        var tableHtml = `
                            <table class="table table-hover">
                                <thead>
                                    <tr>
                                        <th>
                                            <div class="form-check">
                                                <input class="form-check-input" type="checkbox" id="selectAllInvoices">
                                                <label class="form-check-label" for="selectAllInvoices">Select All</label>
                                            </div>
                                        </th>
                                        <th>Invoice No</th>
                                        <th>Customer</th>
                                        <th>Routing</th>
                                        <th>Status</th>
                                        <th>Items in Selected Zones</th>
                                    </tr>
                                </thead>
                                <tbody>`;
                        
                        data.invoices.forEach(function(invoice) {
                            tableHtml += `
                                <tr>
                                    <td>
                                        <div class="form-check">
                                            <input class="form-check-input invoice-checkbox" type="checkbox" 
                                                   name="selected_invoices" value="${invoice.invoice_no}" 
                                                   id="invoice_${invoice.invoice_no}">
                                            <label class="form-check-label" for="invoice_${invoice.invoice_no}"></label>
                                        </div>
                                    </td>
                                    <td><strong>${invoice.invoice_no}</strong></td>
                                    <td>${invoice.customer_name || '-'}</td>
                                    <td>${invoice.routing || '-'}</td>
                                    <td>
                                        ${invoice.status === 'In Progress' ? 
                                            '<span class="badge bg-warning text-dark">In Progress</span>' : 
                                            '<span class="badge bg-secondary">Not Started</span>'
                                        }
                                    </td>
                                    <td>
                                        <span class="badge rounded-pill bg-primary">${invoice.item_count}</span>
                                    </td>
                                </tr>`;
                        });
                        
                        tableHtml += `
                                </tbody>
                            </table>`;
                        
                        if (invoiceTable) invoiceTable.innerHTML = tableHtml;
                        
                        // Reattach select all event handler
                        var newSelectAllInvoicesBtn = document.getElementById('selectAllInvoices');
                        if (newSelectAllInvoicesBtn) {
                            newSelectAllInvoicesBtn.addEventListener('click', function() {
                                var checkboxes = document.querySelectorAll('.invoice-checkbox');
                                checkboxes.forEach(function(checkbox) {
                                    checkbox.checked = newSelectAllInvoicesBtn.checked;
                                });
                            });
                        }
                    } else {
                        if (createBatchButton) createBatchButton.disabled = true;
                        if (invoiceTable) {
                            invoiceTable.innerHTML = `
                                <div class="alert alert-info">
                                    <i class="fas fa-info-circle me-2"></i>
                                    No invoices found with unpicked items in the selected zones.
                                </div>
                            `;
                        }
                    }
                })
                .catch(function(error) {
                    console.error("Error fetching filtered invoices:", error);
                    if (loadingIndicator) loadingIndicator.classList.add('d-none');
                    if (invoiceTable) {
                        invoiceTable.classList.remove('d-none');
                        invoiceTable.innerHTML = `
                            <div class="alert alert-danger">
                                <i class="fas fa-exclamation-circle me-2"></i>
                                Error filtering invoices. Please try again.
                            </div>
                        `;
                    }
                });
        });
    }

    // Validate form submission
    var batchForm = document.getElementById('batchForm');
    if (batchForm) {
        batchForm.addEventListener('submit', function(event) {
            var selectedZones = document.querySelectorAll('input[name="zones"]:checked').length;
            if (selectedZones === 0) {
                alert("Please select at least one zone.");
                event.preventDefault();
                return false;
            }
            
            var selectedInvoices = document.querySelectorAll('input[name="selected_invoices"]:checked').length;
            if (selectedInvoices === 0) {
                alert("Please select at least one invoice.");
                event.preventDefault();
                return false;
            }
            
            return true;
        });
    }
});