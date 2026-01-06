document.addEventListener('DOMContentLoaded', function() {
    // Handle assigning orders to pickers
    const assignButtons = document.querySelectorAll('.assign-btn');
    const modalInvoiceNoInput = document.getElementById('modalInvoiceNo');
    
    assignButtons.forEach(button => {
        button.addEventListener('click', function() {
            const invoiceNo = this.getAttribute('data-invoice-no');
            modalInvoiceNoInput.value = invoiceNo;
        });
    });
    
    // Add sorting functionality to tables if needed
    const ordersTable = document.getElementById('ordersTable');
    if (ordersTable) {
        // You could implement table sorting here if needed
    }
});
