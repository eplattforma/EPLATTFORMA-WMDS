// JavaScript for batch filtering feature
document.addEventListener('DOMContentLoaded', function() {
    // Initialize zone selection handler
    initZoneFiltering();
    
    // Initialize select/deselect all functionality
    initSelectAllButtons();
});

// Set up zone selection event handlers
function initZoneFiltering() {
    const zoneCheckboxes = document.querySelectorAll('.zone-checkbox');
    const apiUrl = '/api/filter_invoices_by_zone';
    
    // Add change event listener to each zone checkbox
    zoneCheckboxes.forEach(checkbox => {
        checkbox.addEventListener('change', function() {
            updateFilterStatus();
            
            // Get all selected zones
            const selectedZones = Array.from(document.querySelectorAll('.zone-checkbox:checked'))
                .map(cb => cb.value);
                
            if (selectedZones.length === 0) {
                // If no zones selected, show a message instead of making API call
                showNoZonesSelectedMessage();
                return;
            }
            
            // Show loading indicator
            showLoadingIndicator();
            
            // Get partially picked status
            const includePartiallyPicked = document.getElementById('include_partially_picked').checked;
            
            // Build request data
            const requestData = {
                zones: selectedZones,
                include_partially_picked: includePartiallyPicked
            };
            
            // Call API to get filtered invoices
            fetch(apiUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken()
                },
                body: JSON.stringify(requestData)
            })
            .then(response => response.json())
            .then(data => {
                updateInvoiceList(data.invoices);
                hideLoadingIndicator();
            })
            .catch(error => {
                console.error('Error filtering invoices:', error);
                showErrorMessage();
                hideLoadingIndicator();
            });
        });
    });
    
    // Include partially picked checkbox handler
    const partiallyPickedCheckbox = document.getElementById('include_partially_picked');
    if (partiallyPickedCheckbox) {
        partiallyPickedCheckbox.addEventListener('change', function() {
            // Trigger filter update by dispatching event on first checked zone checkbox
            const firstCheckedZone = document.querySelector('.zone-checkbox:checked');
            if (firstCheckedZone) {
                // Create and dispatch a change event
                const event = new Event('change');
                firstCheckedZone.dispatchEvent(event);
            }
        });
    }
}

// Initialize select/deselect all buttons
function initSelectAllButtons() {
    // Select all zones
    const selectAllZonesBtn = document.getElementById('selectAllZones');
    if (selectAllZonesBtn) {
        selectAllZonesBtn.addEventListener('click', function() {
            const checkboxes = document.querySelectorAll('.zone-checkbox');
            checkboxes.forEach(checkbox => {
                checkbox.checked = true;
            });
            
            // Trigger change event on the first checkbox to update filters
            if (checkboxes.length > 0) {
                const event = new Event('change');
                checkboxes[0].dispatchEvent(event);
            }
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
            
            // Show no zones selected message
            showNoZonesSelectedMessage();
        });
    }
}

// Get CSRF token from meta tag
function getCsrfToken() {
    return document.querySelector('meta[name="csrf-token"]').getAttribute('content');
}

// Show loading indicator
function showLoadingIndicator() {
    const invoiceListContainer = document.getElementById('invoice-list-container');
    if (invoiceListContainer) {
        invoiceListContainer.innerHTML = `
            <div class="text-center p-5">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
                <p class="mt-2">Filtering invoices based on selected zones...</p>
            </div>
        `;
    }
}

// Hide loading indicator
function hideLoadingIndicator() {
    // This is handled by updateInvoiceList
}

// Display the filtered invoice list
function updateInvoiceList(invoices) {
    const invoiceListContainer = document.getElementById('invoice-list-container');
    if (!invoiceListContainer) return;
    
    if (!invoices || invoices.length === 0) {
        invoiceListContainer.innerHTML = `
            <div class="alert alert-info">
                <i class="fas fa-info-circle me-2"></i>
                No invoices found with items in the selected zones.
            </div>
        `;
        return;
    }
    
    // Build HTML for invoice list
    let html = `
        <div class="table-responsive">
            <table class="table table-striped table-hover">
                <thead class="table-dark">
                    <tr>
                        <th style="width: 50px;">
                            <div class="form-check">
                                <input class="form-check-input" type="checkbox" id="selectAllCheckbox">
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
    
    // Add each invoice row
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
                <td>
                    <span class="badge bg-primary rounded-pill">${invoice.item_count} ${invoice.total_qty ? `(${invoice.total_qty} total)` : ''}</span>
                </td>
                <td>${invoice.total_lines || '-'}</td>
                <td>${invoice.upload_date || '-'}</td>
            </tr>
        `;
    });
    
    // Close table and add select all functionality
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
    invoiceListContainer.innerHTML = html;
    
    // Add event listeners to the new select all/none buttons
    const selectAllInvoicesBtn = document.getElementById('selectAllInvoicesBtn');
    if (selectAllInvoicesBtn) {
        selectAllInvoicesBtn.addEventListener('click', function() {
            const invoiceCheckboxes = document.querySelectorAll('.invoice-checkbox');
            invoiceCheckboxes.forEach(cb => cb.checked = true);
            
            // Also check the header checkbox
            const selectAllCheckbox = document.getElementById('selectAllCheckbox');
            if (selectAllCheckbox) selectAllCheckbox.checked = true;
        });
    }
    
    const deselectAllInvoicesBtn = document.getElementById('deselectAllInvoicesBtn');
    if (deselectAllInvoicesBtn) {
        deselectAllInvoicesBtn.addEventListener('click', function() {
            const invoiceCheckboxes = document.querySelectorAll('.invoice-checkbox');
            invoiceCheckboxes.forEach(cb => cb.checked = false);
            
            // Also uncheck the header checkbox
            const selectAllCheckbox = document.getElementById('selectAllCheckbox');
            if (selectAllCheckbox) selectAllCheckbox.checked = false;
        });
    }
    
    // Add event listener to the select all checkbox in the header
    const selectAllCheckbox = document.getElementById('selectAllCheckbox');
    if (selectAllCheckbox) {
        selectAllCheckbox.addEventListener('change', function() {
            const invoiceCheckboxes = document.querySelectorAll('.invoice-checkbox');
            invoiceCheckboxes.forEach(cb => cb.checked = this.checked);
        });
    }
}

// Show error message
function showErrorMessage() {
    const invoiceListContainer = document.getElementById('invoice-list-container');
    if (invoiceListContainer) {
        invoiceListContainer.innerHTML = `
            <div class="alert alert-danger">
                <i class="fas fa-exclamation-circle me-2"></i>
                There was an error filtering invoices. Please try again.
            </div>
        `;
    }
}

// Show message when no zones are selected
function showNoZonesSelectedMessage() {
    const invoiceListContainer = document.getElementById('invoice-list-container');
    if (invoiceListContainer) {
        invoiceListContainer.innerHTML = `
            <div class="alert alert-warning">
                <i class="fas fa-exclamation-triangle me-2"></i>
                Please select at least one zone to filter invoices.
            </div>
        `;
    }
}

// Update filter status message
function updateFilterStatus() {
    const filterStatusElement = document.getElementById('filter-status');
    if (!filterStatusElement) return;
    
    const selectedZonesCount = document.querySelectorAll('.zone-checkbox:checked').length;
    if (selectedZonesCount > 0) {
        filterStatusElement.textContent = `${selectedZonesCount} zone(s) selected`;
        filterStatusElement.classList.remove('text-danger');
        filterStatusElement.classList.add('text-success');
    } else {
        filterStatusElement.textContent = 'No zones selected';
        filterStatusElement.classList.remove('text-success');
        filterStatusElement.classList.add('text-danger');
    }
}