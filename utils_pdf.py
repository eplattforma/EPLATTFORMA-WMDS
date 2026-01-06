"""
PDF generation utilities for receipts and reports
"""
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A5
from io import BytesIO


def draw_driver_receipt_header(c, width, height, receipt_data):
    """
    Draw A5 COD receipt header with company info, payment details, and invoice references.
    
    Args:
        c: ReportLab canvas object
        width: Page width
        height: Page height
        receipt_data: Dictionary containing:
            - stop_name: Customer name
            - stop_addr: Customer address
            - driver_name: Driver name
            - delivered_at: Delivery datetime
            - payment_method: Payment method (Cash, Card, Cheque, etc.)
            - expected_amount: Expected total (Decimal)
            - received_amount: Received amount (Decimal)
            - variance: Difference (Decimal)
            - note: Payment note
            - exceptions_count: Number of exceptions (int)
            - invoice_numbers: List of invoice numbers
            - receipt_id: Receipt ID for tracking
    
    Returns:
        y: Remaining y position for additional content
    """
    # Set pure black for all text and lines
    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0, 0, 0)
    
    y = height - 40

    # 1. Company header
    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(width / 2, y, "STEP EPLATTFORMA LTD")
    y -= 14

    c.setFont("Helvetica", 9)
    c.drawCentredString(width / 2, y, "Digeni Akrita 13BC, 1055 Lefkosia")
    y -= 12
    c.drawCentredString(width / 2, y, "Tel: 7000 0394")
    y -= 12
    c.drawCentredString(width / 2, y, "VAT Reg No: CY10353264O")
    y -= 14

    c.line(30, y, width - 30, y)
    y -= 18

    # 2. Receipt title
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(width / 2, y, "CASH SALES RECEIPT")
    y -= 12
    c.line(30, y, width - 30, y)
    y -= 16

    # 3. Delivery / stop info
    c.setFont("Helvetica", 8)

    delivered_at = receipt_data.get('delivered_at')
    if delivered_at:
        date_text = delivered_at.strftime('%Y-%m-%d')
        time_text = delivered_at.strftime('%H:%M')
    else:
        date_text = ""
        time_text = ""

    c.drawString(30, y, f"Date: {date_text}")
    c.drawRightString(width - 30, y, f"Time: {time_text}")
    y -= 12

    # Receipt ID
    receipt_id = receipt_data.get('receipt_id', 'N/A')
    c.drawString(30, y, f"Receipt No: {receipt_id}")
    y -= 12

    stop_name = receipt_data.get('stop_name', '')
    if len(stop_name) > 50:
        stop_name = stop_name[:50] + "..."
    c.drawString(30, y, f"Customer: {stop_name}")
    y -= 12

    stop_addr = receipt_data.get('stop_addr', '')
    if stop_addr:
        if len(stop_addr) > 60:
            # Wrap long address
            c.drawString(30, y, f"Location: {stop_addr[:60]}")
            y -= 10
            if len(stop_addr) > 60:
                c.drawString(50, y, stop_addr[60:120])
                y -= 2
        else:
            c.drawString(30, y, f"Location: {stop_addr}")
        y -= 12

    driver_name = receipt_data.get('driver_name', '-')
    c.drawString(30, y, f"Driver: {driver_name}")
    y -= 12

    c.line(30, y, width - 30, y)
    y -= 14

    # 4. Payment Information
    c.setFont("Helvetica-Bold", 9)
    c.drawString(30, y, "Payment Information")
    y -= 12

    c.setFont("Helvetica", 8)
    
    # Check if this is a preview (payment not collected)
    is_preview = receipt_data.get('is_preview', False)
    payment_method = receipt_data.get('payment_method', 'Cash')
    
    if is_preview or payment_method == 'NOT COLLECTED':
        # Preview mode - show clearly that payment not collected
        c.setFont("Helvetica-Bold", 9)
        c.drawString(30, y, "Status: PAYMENT NOT YET COLLECTED")
        y -= 14
        
        c.setFont("Helvetica", 8)
        expected_amount = float(receipt_data.get('expected_amount', 0))
        c.drawString(30, y, f"Expected Amount: €{expected_amount:.2f}")
        y -= 12
        c.drawString(30, y, "This is a PREVIEW receipt - not valid for payment confirmation")
        y -= 12
    else:
        # Actual payment collected
        c.drawString(30, y, f"Payment Method: {payment_method}")
        y -= 12

        expected_amount = float(receipt_data.get('expected_amount', 0))
        c.drawString(30, y, f"Expected: €{expected_amount:.2f}")
        y -= 12

        received_amount = receipt_data.get('received_amount')
        if received_amount is not None:
            received_amount = float(received_amount)
            c.drawString(30, y, f"Received: €{received_amount:.2f}")
            y -= 12

        variance = receipt_data.get('variance')
        if variance is not None:
            variance = float(variance)
            c.drawString(30, y, f"Variance: €{variance:.2f}")
            y -= 12

    note = receipt_data.get('note', '')
    if note:
        c.drawString(30, y, f"Note: {note[:60]}")
        y -= 12

    exceptions_count = receipt_data.get('exceptions_count')
    if exceptions_count is not None and exceptions_count > 0:
        c.drawString(30, y, f"Exceptions: {exceptions_count}")
        y -= 12

    c.line(30, y, width - 30, y)
    y -= 14

    # 5. Invoice Reference
    invoice_numbers = receipt_data.get('invoice_numbers', [])
    if isinstance(invoice_numbers, str):
        invoice_list = [x.strip() for x in invoice_numbers.split(",") if x.strip()]
    else:
        invoice_list = list(invoice_numbers)

    if invoice_list:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(30, y, "Invoice Reference:")
        y -= 12

        c.setFont("Helvetica", 8)
        for inv_num in invoice_list:
            c.drawString(40, y, str(inv_num))
            y -= 12
            if y < 80:
                # If we get too low, new page
                c.showPage()
                y = height - 40
                c.setFont("Helvetica", 8)

        c.line(30, y, width - 30, y)
        y -= 14

    # 6. Total received (or expected if preview)
    c.setFont("Helvetica-Bold", 10)
    is_preview = receipt_data.get('is_preview', False)
    
    if is_preview or receipt_data.get('payment_method') == 'NOT COLLECTED':
        expected_amount = float(receipt_data.get('expected_amount', 0))
        c.drawString(30, y, f"EXPECTED AMOUNT: €{expected_amount:.2f}")
    else:
        received_amount = receipt_data.get('received_amount')
        if received_amount is not None:
            received_amount = float(received_amount)
            c.drawString(30, y, f"TOTAL AMOUNT RECEIVED: €{received_amount:.2f}")
        else:
            c.drawString(30, y, "AMOUNT: NOT COLLECTED")
    y -= 16

    c.line(30, y, width - 30, y)
    y -= 16

    # 7. Footer / signatures
    c.setFont("Helvetica", 8)
    c.drawCentredString(width / 2, y, "Thank you for your business.")
    y -= 12
    
    # Conditional footer based on payment status
    is_preview = receipt_data.get('is_preview', False)
    if is_preview or receipt_data.get('payment_method') == 'NOT COLLECTED':
        c.drawCentredString(width / 2, y, "This is a PREVIEW - does not confirm payment collection.")
    else:
        c.drawCentredString(width / 2, y, "This receipt confirms delivery and payment collection.")
    y -= 20

    c.setFont("Helvetica", 8)
    c.drawString(30, y, "Customer Signature: ______________________")
    y -= 14
    c.drawString(30, y, "Driver Signature:   ______________________")
    y -= 20

    c.line(30, y, width - 30, y)
    y -= 20

    return y


def generate_driver_receipt_pdf(receipt_data):
    """
    Generate A5 driver COD receipt PDF.
    
    Args:
        receipt_data: Dictionary containing receipt information (see draw_driver_receipt_header)
    
    Returns:
        BytesIO: PDF file in memory
    """
    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=A5)
    width, height = A5

    # Draw the receipt header (includes all content for now)
    draw_driver_receipt_header(c, width, height, receipt_data)

    # Future enhancement: Add item-level details here if needed
    # y = draw_driver_receipt_header(c, width, height, receipt_data)
    # draw_item_lines(c, width, y, receipt_data['items'])

    c.showPage()
    c.save()
    pdf_buffer.seek(0)

    return pdf_buffer
