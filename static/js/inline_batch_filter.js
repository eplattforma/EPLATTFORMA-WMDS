// Simple inline batch filtering script
document.addEventListener('DOMContentLoaded', function() {
    // Setup zone checkboxes
    setupZoneCheckboxes();
    
    // Setup select/deselect all buttons
    setupSelectButtons();
});

function setupZoneCheckboxes() {
    const zoneCheckboxes = document.querySelectorAll('.zone-checkbox');
    zoneCheckboxes.forEach(checkbox => {
        checkbox.addEventListener('change', filterInvoices);
    });
    
    // Include partially picked checkbox
    const partiallyPickedCheckbox = document.getElementById('include_partially_picked');
    if (partiallyPickedCheckbox) {
        partiallyPickedCheckbox.addEventListener('change', filterInvoices);
    }
}

function setupSelectButtons() {
    // Select all zones
    const selectAllZonesBtn = document.getElementById('selectAllZones');
    if (selectAllZonesBtn) {
        selectAllZonesBtn.addEventListener('click', function() {
            const checkboxes = document.querySelectorAll('.zone-checkbox');
            checkboxes.forEach(checkbox => {
                checkbox.checked = true;
            });
            filterInvoices();
        });
    }
    
    // Deselect all zones
    const deselectAllZonesBtn = document.getElementById('deselectAllZones');
    if (deselectAllZonesBtn) {
        deselectAllZonesBtn.addEventListener('click', function() {
            const checkboxes = document.querySelectorAll('.zone-checkbox');
            checkboxes.forEach(checkbox => {
                checkbox.checked = false;
            });
            filterInvoices();
        });
    }
}

function filterInvoices() {
    // Update filter status
    updateFilterStatus();
    
    // Get selected zones
    const selectedZones = Array.from(document.querySelectorAll('.zone-checkbox:checked'))
        .map(cb => cb.value);
    
    // Determine if we include partially picked invoices
    const includePartiallyPicked = document.getElementById('include_partially_picked')?.checked || false;
    
    // Show appropriate message if no zones selected
    if (selectedZones.length === 0) {
        document.getElementById('invoice-list-container').innerHTML = `
            <div class="alert alert-warning">
                <i class="fas fa-exclamation-triangle me-2"></i>
                Please select at least one zone to see available invoices.
            </div>
        `;
        return;
    }
    
    // Show loading message
    document.getElementById('invoice-list-container').innerHTML = `
        <div class="text-center p-4">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
            <p class="mt-2">Filtering invoices...</p>
        </div>
    `;
    
    // Make AJAX request to get filtered invoices
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/filter_invoices_by_zone');
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    
    xhr.onload = function() {
        if (xhr.status === 200) {
            const response = JSON.parse(xhr.responseText);
            displayFilteredInvoices(response.invoices || []);
        } else {
            // Show error message
            document.getElementById('invoice-list-container').innerHTML = `
                <div class="alert alert-danger">
                    <i class="fas fa-exclamation-circle me-2"></i>
                    Error filtering invoices. Please try again.
                </div>
            `;
        }
    };
    
    xhr.onerror = function() {
        // Show error message
        document.getElementById('invoice-list-container').innerHTML = `
            <div class="alert alert-danger">
                <i class="fas fa-exclamation-circle me-2"></i>
                Network error. Please check your connection and try again.
            </div>
        `;
    };
    
    // Send the request with selected zones
    xhr.send(JSON.stringify({
        zones: selectedZones,
        include_partially_picked: includePartiallyPicked
    }));
}

function displayFilteredInvoices(invoices) {
    const container = document.getElementById('invoice-list-container');
    
    if (!invoices || invoices.length === 0) {
        container.innerHTML = `
            <div class="alert alert-info">
                <i class="fas fa-info-circle me-2"></i>
                No invoices found with items in the selected zones.
            </div>
        `;
        return;
    }
    
    // Build table HTML
    let html = `
        <div class="table-responsive">
            <table class="table table-striped table-hover" id="invoicesTable">
                <thead class="table-dark">
                    <tr>
                        <th style="width: 50px;">
                            <div class="form-check">
                                <input class="form-check-input" type="checkbox" id="selectAllInvoices">
                            </div>
                        </th>
                        <th>Invoice #</th>
                        <th>Customer</th>
                        <th>Status</th>
                        <th>Eligible Items</th>
                        <th>Total Lines</th>
                        <th>Upload Date</th>
                    </tr>
                </thead>
                <tbody>
    `;
    
    // Add invoice rows
    invoices.forEach(invoice => {
        html += `
            <tr>
                <td>
                    <div class="form-check">
                        <input class="form-check-input invoice-checkbox" type="checkbox" 
                               name="selected_invoices" value="${invoice.invoice_no}" 
                               id="invoice_${invoice.invoice_no}">
                    </div>
                </td>
                <td>${invoice.invoice_no}</td>
                <td>${invoice.customer_name || '-'}</td>
                <td>
                    <span class="badge bg-${invoice.status === 'In Progress' ? 'warning' : 'secondary'}">
                        ${invoice.status}
                    </span>
                </td>
                <td><span class="badge bg-primary rounded-pill">${invoice.item_count}</span></td>
                <td>${invoice.total_lines || '-'}</td>
                <td>${invoice.upload_date || '-'}</td>
            </tr>
        `;
    });
    
    // Close table and add select buttons
    html += `
                </tbody>
            </table>
        </div>
        <div class="mt-3">
            <button type="button" class="btn btn-sm btn-outline-primary" id="selectAllInvoicesBtn">Select All</button>
            <button type="button" class="btn btn-sm btn-outline-secondary" id="deselectAllInvoicesBtn">Deselect All</button>
        </div>
    `;
    
    // Update container with new HTML
    container.innerHTML = html;
    
    // Set up event listeners for the new buttons
    document.getElementById('selectAllInvoicesBtn')?.addEventListener('click', function() {
        document.querySelectorAll('.invoice-checkbox').forEach(cb => cb.checked = true);
        document.getElementById('selectAllInvoices').checked = true;
    });
    
    document.getElementById('deselectAllInvoicesBtn')?.addEventListener('click', function() {
        document.querySelectorAll('.invoice-checkbox').forEach(cb => cb.checked = false);
        document.getElementById('selectAllInvoices').checked = false;
    });
    
    document.getElementById('selectAllInvoices')?.addEventListener('change', function() {
        document.querySelectorAll('.invoice-checkbox').forEach(cb => cb.checked = this.checked);
    });
}

function updateFilterStatus() {
    const filterStatus = document.getElementById('filter-status');
    if (!filterStatus) return;
    
    const selectedCount = document.querySelectorAll('.zone-checkbox:checked').length;
    if (selectedCount > 0) {
        filterStatus.textContent = `${selectedCount} zone(s) selected`;
        filterStatus.classList.remove('text-danger');
        filterStatus.classList.add('text-success');
    } else {
        filterStatus.textContent = 'No zones selected';
        filterStatus.classList.remove('text-success');
        filterStatus.classList.add('text-danger');
    }
}