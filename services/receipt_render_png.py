from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import textwrap
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)

DOT_WIDTH_DEFAULT = 576
PADDING_X = 20
PADDING_Y = 20

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_BOLD_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def money(v):
    try:
        return f"EUR {Decimal(str(v)):,.2f}"
    except Exception:
        return "EUR 0.00"


def _load_company_settings():
    try:
        from app import db
        from models import Setting
        return {
            'company_name': Setting.get(db.session, 'company_name', 'STEP EPLATTFORMA LTD'),
            'company_address': Setting.get(db.session, 'company_address', 'Digeni Akrita 13BC, 1055 Lefkosia'),
            'company_tel': Setting.get(db.session, 'company_tel', '7000 0394'),
            'company_vat': Setting.get(db.session, 'company_vat', 'CY103532640'),
            'bank_name': Setting.get(db.session, 'bank_name', 'Bank of Cyprus'),
            'bank_iban': Setting.get(db.session, 'bank_iban', 'CY04 0020 0195 0000 0357 0208 4600'),
            'bank_bic': Setting.get(db.session, 'bank_bic', 'BCYPCY2N'),
            'bank_beneficiary': Setting.get(db.session, 'bank_beneficiary', 'Step Eplattforma'),
        }
    except Exception as e:
        logger.warning(f"Could not load company settings: {e}")
        return {
            'company_name': 'STEP EPLATTFORMA LTD',
            'company_address': 'Digeni Akrita 13BC, 1055 Lefkosia',
            'company_tel': '7000 0394',
            'company_vat': 'CY103532640',
            'bank_name': 'Bank of Cyprus',
            'bank_iban': 'CY04 0020 0195 0000 0357 0208 4600',
            'bank_bic': 'BCYPCY2N',
            'bank_beneficiary': 'Step Eplattforma',
        }


def _pad_right(label, value, width):
    label = str(label)
    value = str(value)
    gap = width - len(label) - len(value)
    if gap < 1:
        gap = 1
    return label + (" " * gap) + value


def render_receipt_png(data: dict, dot_width: int = None) -> bytes:
    dot_width = dot_width or DOT_WIDTH_DEFAULT

    cs = _load_company_settings()

    font_body = ImageFont.truetype(FONT_PATH, 28)
    font_bold = ImageFont.truetype(FONT_BOLD_PATH, 28)
    font_title = ImageFont.truetype(FONT_BOLD_PATH, 34)

    dummy = Image.new("L", (dot_width, 200), 255)
    d = ImageDraw.Draw(dummy)
    char_w = d.textlength("M", font=font_body)
    cols = max(24, int((dot_width - 2 * PADDING_X) / char_w))
    sep = "-" * cols

    def wrap(s):
        return textwrap.wrap((s or "").strip(), width=cols) or [""]

    lines = []

    def add(s=""):
        lines.append(("body", s))

    def add_b(s=""):
        lines.append(("bold", s))

    def add_c(s=""):
        lines.append(("center", s))

    def add_t(s=""):
        lines.append(("title", s))

    def add_company_header():
        add_t(cs['company_name'])
        add_c(cs['company_address'])
        add_c(f"Tel: {cs['company_tel']}  VAT: {cs['company_vat']}")

    is_preview = bool(data.get("is_preview"))
    is_amended = bool(data.get("is_amended"))
    is_collected = bool(data.get("is_collected"))
    is_credit = bool(data.get("is_credit"))
    doc_type = str(data.get("doc_type", "") or "").strip().lower()
    doc_mode = str(data.get("doc_mode", "") or "").strip().lower()

    if doc_mode == "exceptions":
        add_company_header()
        add(sep)
        add_t("DELIVERY EXCEPTIONS")
        add_t("ACKNOWLEDGEMENT")
        add(sep)

        receipt_no = str(data.get("receipt_no", "")).strip()
        date_str = str(data.get("date_str", "")).strip()
        if " " in date_str:
            d_part, t_part = date_str.split(" ", 1)
        else:
            d_part, t_part = date_str, ""
        add_b(f"Ref: {receipt_no}")
        if t_part:
            add_b(f"Date: {d_part}   Time: {t_part}")
        else:
            add_b(f"Date: {d_part}")

        route_no = str(data.get("route_no", "")).strip()
        stop_no_val = data.get("stop_no", "")
        try:
            stop_no_val = str(int(float(stop_no_val))).zfill(3)
        except Exception:
            stop_no_val = str(stop_no_val).zfill(3) if stop_no_val else "---"
        driver = str(data.get("driver_name", "")).strip()

        add_b(f"Route: {route_no}  Stop: {stop_no_val}")
        add_b(f"Driver: {driver}")
        add(sep)

        add_b("Customer:")
        for w_line in wrap(data.get("customer_name", "")):
            add(w_line)
        cust_code = str(data.get("customer_code", "")).strip()
        if cust_code:
            add(f"Code: {cust_code}")
        add(sep)

        invoices = data.get("invoices") or []
        add_b(f"REFERENCED INVOICES ({len(invoices)})")
        for inv in invoices:
            inv_no = str(inv.get("invoice_no", "")).strip()
            add(f"  {inv_no}")
        add(sep)
        add("")
        add("")

        exceptions_data = data.get("exceptions") or []
        if exceptions_data:
            add_t("ITEMS NOT DELIVERED")
            add(sep)
            col_code = 12
            col_nd = 4
            add_b(f"{'ITEM CODE':<{col_code}}{'ND':>{col_nd}}")
            add(sep)
            for exc in exceptions_data:
                item_code = str(exc.get("item_code", ""))
                item_name = str(exc.get("item_name", ""))
                nd = str(exc.get("qty_not_delivered", ""))
                add_b(f"{item_code:<{col_code}}{nd:>{col_nd}}")
                for name_line in wrap(item_name):
                    add(name_line)
                add("")
            add("")
            add("")
            add(sep)

        sig_line = "_" * cols
        add("")
        add("Customer Signature (Acknowledgement):")
        add(sig_line)
        add("")
        add("")
        add("Driver Signature:")
        add(sig_line)
        add("")
        add("")
        add(sep)

        customer_email = str(data.get("customer_email", "")).strip()
        if not customer_email:
            customer_email = "(not available)"
        add("This is an acknowledgement of items")
        add("from the referenced invoice(s) that")
        add("were not delivered.")
        add("This document is NOT a credit note")
        add("and has no monetary value.")
        add("A credit note will be issued after")
        add("the driver returns to the warehouse")
        add("and will be emailed to:")
        add_b(customer_email)
        for _ in range(6):
            add("")

        line_h_body = 36
        line_h_title = 44
        height = PADDING_Y * 2 + sum(
            line_h_title if t == "title" else line_h_body for t, _ in lines
        )
        img = Image.new("L", (dot_width, height), 255)
        draw = ImageDraw.Draw(img)

        y = PADDING_Y
        avail_w = dot_width - 2 * PADDING_X

        for typ, txt in lines:
            if typ == "title":
                tw = draw.textlength(txt, font=font_title)
                x = PADDING_X + max(0, (avail_w - tw) / 2)
                draw.text((x, y), txt, font=font_title, fill=0)
                y += line_h_title
            elif typ == "center":
                tw = draw.textlength(txt, font=font_body)
                x = PADDING_X + max(0, (avail_w - tw) / 2)
                draw.text((x, y), txt, font=font_body, fill=0)
                y += line_h_body
            elif typ == "bold":
                draw.text((PADDING_X, y), txt, font=font_bold, fill=0)
                y += line_h_body
            else:
                draw.text((PADDING_X, y), txt, font=font_body, fill=0)
                y += line_h_body

        img = img.convert("1")
        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()

    if doc_type == "official":
        # ── COLLECTION RECEIPT (Customer Copy) ───────────────────────────
        def _wrap_inv_refs(invoice_nos, width):
            s = ", ".join(str(n) for n in invoice_nos)
            ref_lines, line = [], ""
            for part in s.split(", "):
                candidate = part if not line else ", " + part
                if len(line) + len(candidate) > width:
                    ref_lines.append(line)
                    line = part
                else:
                    line += candidate
            if line:
                ref_lines.append(line)
            return ref_lines or [""]

        if is_preview:
            add_t("*** PREVIEW ***")

        is_reprint = bool(data.get("is_reprint"))

        add_company_header()
        add(sep)
        reprint_label = "REPRINT" if is_reprint else ""
        if reprint_label:
            add_b(_pad_right("PAYMENT RECEIPT", reprint_label, cols))
        else:
            add_t("PAYMENT RECEIPT")
        add(sep)

        receipt_no = str(data.get("receipt_no", "")).strip()
        date_str = str(data.get("date_str", "")).strip()
        if " " in date_str:
            d_part, t_part = date_str.split(" ", 1)
        else:
            d_part, t_part = date_str, ""
        add_b(f"Receipt No: {receipt_no}")
        if t_part:
            add_b(f"Date: {d_part}   Time: {t_part}")
        else:
            add_b(f"Date: {d_part}")
        add(sep)

        add_b("Customer:")
        for w in wrap(data.get("customer_name", "")):
            add(w)

        invoice_nos_plain = data.get("invoice_nos_plain") or [
            inv.get("invoice_no", "") for inv in (data.get("invoices") or [])
        ]
        invoice_nos_plain = [str(n) for n in invoice_nos_plain if n]
        if invoice_nos_plain:
            if len(invoice_nos_plain) == 1:
                add_b(f"Payment for Invoice: {invoice_nos_plain[0]}")
            else:
                add_b("Invoices:")
                for ref_line in _wrap_inv_refs(invoice_nos_plain, cols - 2):
                    add("  " + ref_line)
        add(sep)

        add_b("Collected:")
        payments = data.get("payments") or []
        total_collected = Decimal("0")
        if payments:
            for p in payments:
                method = str(p.get("method", "")).strip()
                amt = Decimal(str(p.get("amount", "0") or "0"))
                total_collected += amt
                add(_pad_right(f"  {method}:", f"EUR {amt:,.2f}", cols))
        else:
            fallback_collected = Decimal(str(data.get("collected", "0") or "0"))
            pm = str(data.get("payment_method", "")).replace("_", " ").title().strip()
            if pm and fallback_collected > 0:
                add(_pad_right(f"  {pm}:", f"EUR {fallback_collected:,.2f}", cols))
                total_collected = fallback_collected

        cheque_number = str(data.get("cheque_number", "") or "").strip()
        cheque_date = str(data.get("cheque_date", "") or "").strip()
        if cheque_number:
            add(f"  Cheque No: {cheque_number}")
        if cheque_date:
            add(f"  Cheque Date: {cheque_date}")

        add_b(_pad_right("Total Paid:", f"EUR {total_collected:,.2f}", cols))
        add(sep)

        collector = str(data.get("collector_name", "") or data.get("driver_name", "")).strip()
        if collector:
            add("")
            add("")
            add_b(f"Collector: {collector}")

        sig_line = "_" * cols
        add("Collector Signature:")
        add(sig_line)
        add("")
        add("")
        add(sep)
        add("Payment acknowledgement for the")
        add("invoice(s) referenced above.")
        add("Not a tax invoice.")
        for _ in range(6):
            add("")

        line_h_body = 36
        line_h_title = 44
        height = PADDING_Y * 2 + sum(
            line_h_title if t == "title" else line_h_body for t, _ in lines
        )
        img = Image.new("L", (dot_width, height), 255)
        draw = ImageDraw.Draw(img)
        y = PADDING_Y
        avail_w = dot_width - 2 * PADDING_X
        for typ, txt in lines:
            if typ == "title":
                tw = draw.textlength(txt, font=font_title)
                x = PADDING_X + max(0, (avail_w - tw) / 2)
                draw.text((x, y), txt, font=font_title, fill=0)
                y += line_h_title
            elif typ == "center":
                tw = draw.textlength(txt, font=font_body)
                x = PADDING_X + max(0, (avail_w - tw) / 2)
                draw.text((x, y), txt, font=font_body, fill=0)
                y += line_h_body
            elif typ == "bold":
                draw.text((PADDING_X, y), txt, font=font_bold, fill=0)
                y += line_h_body
            else:
                draw.text((PADDING_X, y), txt, font=font_body, fill=0)
                y += line_h_body
        img = img.convert("1")
        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()

    if doc_type == "online_notice":
        add_company_header()
        add(sep)
        add_t("PAYMENT ADVICE")
        add_t("BANK TRANSFER")
        add(sep)
        add_c("NOT A RECEIPT")
        add_c("ACTION REQUIRED:")
        add_c("Please pay by bank transfer.")
        add(sep)

        date_str = str(data.get("date_str", "")).strip()
        due_date = str(data.get("due_date", "")).strip()
        if date_str:
            add_b(f"Issued: {date_str}")
        if due_date:
            add_b(f"Due date: {due_date}")
        add(sep)

        add_b("Customer:")
        for w in wrap(data.get("customer_name", "")):
            add(w)
        add(sep)

        invoices = data.get("invoices") or []
        if invoices:
            add_b("Invoice(s):")
            for inv in invoices:
                inv_no = str(inv.get("invoice_no", "")).strip()
                inv_total = inv.get("total")
                if inv_total is not None:
                    add(_pad_right(f"  {inv_no}", money(inv_total), cols))
                else:
                    add(f"  {inv_no}")
            add(sep)

        exceptions_data = data.get("exceptions") or []
        ex_total = Decimal(str(data.get("exceptions_total", "0") or "0"))
        if exceptions_data:
            add_b("EXCEPTIONS")
            for exc in exceptions_data:
                exc_type = str(exc.get("type", "")).upper()
                item = str(exc.get("item_name", ""))
                qty_e = exc.get("qty_expected", "")
                qty_a = exc.get("qty_actual", "")
                ded = exc.get("deduction_value")
                add(f"  {exc_type}: {item}")
                line_detail = f"  Exp: {qty_e} | Act: {qty_a}"
                if ded is not None:
                    try:
                        line_detail += f"  {money(ded)}"
                    except Exception:
                        pass
                add(line_detail)
            add(sep)
            add_b(_pad_right("  Exceptions deducted:", f"-{money(ex_total)}", cols))
            add(sep)

        net_payable = Decimal(str(data.get("net_payable", "0") or "0"))
        font_net = ImageFont.truetype(FONT_BOLD_PATH, 38)
        add("")
        lines.append(("net_payable", f"TOTAL PAYABLE: {money(net_payable)}"))
        add("")
        add(sep)

        add_b("BANK TRANSFER DETAILS")
        add(f"  Beneficiary: {cs['bank_beneficiary']}")
        add(f"  Bank: {cs['bank_name']}")
        iban = cs['bank_iban']
        if len(iban) > 24:
            add(f"  IBAN: {iban[:24]}")
            add(f"        {iban[24:].strip()}")
        else:
            add(f"  IBAN: {iban}")
        add(f"  BIC/SWIFT: {cs['bank_bic']}")
        add(sep)

        invoice_nos_plain = [str(inv.get("invoice_no", "")).strip() for inv in invoices if inv.get("invoice_no")]
        if len(invoice_nos_plain) == 1:
            ref_str = invoice_nos_plain[0]
        elif len(invoice_nos_plain) > 1:
            cust_name_short = (data.get("customer_name") or "CUSTOMER")[:20].strip()
            ref_str = f"MULTI + {cust_name_short}"
        else:
            ref_str = "See invoices above"
        add_b("TRANSFER REFERENCE")
        add_b("(MUST BE USED):")
        add_b(f"  {ref_str}")
        add(sep)

        for _ in range(6):
            add("")

        line_h_body = 36
        line_h_title = 44
        line_h_net = 52
        height = PADDING_Y * 2 + sum(
            line_h_title if t == "title" else (line_h_net if t == "net_payable" else line_h_body)
            for t, _ in lines
        )
        img = Image.new("L", (dot_width, height), 255)
        draw = ImageDraw.Draw(img)
        y = PADDING_Y
        avail_w = dot_width - 2 * PADDING_X
        for typ, txt in lines:
            if typ == "title":
                tw = draw.textlength(txt, font=font_title)
                x = PADDING_X + max(0, (avail_w - tw) / 2)
                draw.text((x, y), txt, font=font_title, fill=0)
                y += line_h_title
            elif typ == "center":
                tw = draw.textlength(txt, font=font_body)
                x = PADDING_X + max(0, (avail_w - tw) / 2)
                draw.text((x, y), txt, font=font_body, fill=0)
                y += line_h_body
            elif typ == "net_payable":
                tw = draw.textlength(txt, font=font_net)
                x = PADDING_X + max(0, (avail_w - tw) / 2)
                draw.text((x, y), txt, font=font_net, fill=0)
                y += line_h_net
            elif typ == "bold":
                draw.text((PADDING_X, y), txt, font=font_bold, fill=0)
                y += line_h_body
            else:
                draw.text((PADDING_X, y), txt, font=font_body, fill=0)
                y += line_h_body
        img = img.convert("1")
        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()

    if doc_type == "pdc_ack":
        def _wrap_inv_refs_pdc(invoice_nos, width):
            s = ", ".join(str(n) for n in invoice_nos)
            ref_lines, line = [], ""
            for part in s.split(", "):
                candidate = part if not line else ", " + part
                if len(line) + len(candidate) > width:
                    ref_lines.append(line)
                    line = part
                else:
                    line += candidate
            if line:
                ref_lines.append(line)
            return ref_lines or [""]

        if is_preview:
            add_t("*** PREVIEW ***")

        is_reprint = bool(data.get("is_reprint"))

        add_company_header()
        add(sep)
        reprint_label = "REPRINT" if is_reprint else ""
        if reprint_label:
            add_b(_pad_right("CHEQUE RECEIVED (PDC)", reprint_label, cols))
            add_b("ACKNOWLEDGEMENT")
        else:
            add_t("CHEQUE RECEIVED (PDC)")
            add_t("ACKNOWLEDGEMENT")
        add_c("Post-dated cheque received -")
        add_c("pending clearance.")
        add_c("This document is NOT a")
        add_c("payment receipt.")
        add(sep)

        receipt_no = str(data.get("receipt_no", "")).strip()
        date_str = str(data.get("date_str", "")).strip()
        if " " in date_str:
            d_part, t_part = date_str.split(" ", 1)
        else:
            d_part, t_part = date_str, ""
        add_b(f"Doc No: {receipt_no}")
        if t_part:
            add_b(f"Date: {d_part}   Time: {t_part}")
        else:
            add_b(f"Date: {d_part}")
        add(sep)

        add_b("Customer:")
        for w in wrap(data.get("customer_name", "")):
            add(w)

        invoice_nos_plain = data.get("invoice_nos_plain") or [
            inv.get("invoice_no", "") for inv in (data.get("invoices") or [])
        ]
        invoice_nos_plain = [str(n) for n in invoice_nos_plain if n]
        if invoice_nos_plain:
            if len(invoice_nos_plain) == 1:
                add_b(f"Payment for Invoice: {invoice_nos_plain[0]}")
            else:
                add_b("Invoices:")
                for ref_line in _wrap_inv_refs_pdc(invoice_nos_plain, cols - 2):
                    add("  " + ref_line)
        add(sep)

        add_b("Collected:")
        payments = data.get("payments") or []
        total_collected = Decimal("0")
        if payments:
            for p in payments:
                method = str(p.get("method", "")).strip()
                amt = Decimal(str(p.get("amount", "0") or "0"))
                total_collected += amt
                add(_pad_right(f"  {method}:", f"EUR {amt:,.2f}", cols))
        else:
            fallback_collected = Decimal(str(data.get("collected", "0") or "0"))
            pm = str(data.get("payment_method", "")).replace("_", " ").title().strip()
            if pm and fallback_collected > 0:
                add(_pad_right(f"  {pm}:", f"EUR {fallback_collected:,.2f}", cols))
                total_collected = fallback_collected

        cheque_number = str(data.get("cheque_number", "") or "").strip()
        cheque_date = str(data.get("cheque_date", "") or "").strip()
        if cheque_number:
            add(f"  Cheque No: {cheque_number}")
        if cheque_date:
            add(f"  Cheque Date: {cheque_date}")

        add_b(_pad_right("Total Paid:", f"EUR {total_collected:,.2f}", cols))
        add(sep)

        collector = str(data.get("collector_name", "") or data.get("driver_name", "")).strip()
        if collector:
            add("")
            add("")
            add_b(f"Collector: {collector}")

        sig_line = "_" * cols
        add("Collector Signature:")
        add(sig_line)
        add("")
        add("")
        add(sep)
        add("Final settlement is confirmed")
        add("only after cheque clearance.")
        add("Any credit notes/adjustments are")
        add("issued by Accounts.")
        add(sep)
        for _ in range(6):
            add("")

        line_h_body = 36
        line_h_title = 44
        height = PADDING_Y * 2 + sum(
            line_h_title if t == "title" else line_h_body for t, _ in lines
        )
        img = Image.new("L", (dot_width, height), 255)
        draw = ImageDraw.Draw(img)
        y = PADDING_Y
        avail_w = dot_width - 2 * PADDING_X
        for typ, txt in lines:
            if typ == "title":
                tw = draw.textlength(txt, font=font_title)
                x = PADDING_X + max(0, (avail_w - tw) / 2)
                draw.text((x, y), txt, font=font_title, fill=0)
                y += line_h_title
            elif typ == "center":
                tw = draw.textlength(txt, font=font_body)
                x = PADDING_X + max(0, (avail_w - tw) / 2)
                draw.text((x, y), txt, font=font_body, fill=0)
                y += line_h_body
            elif typ == "bold":
                draw.text((PADDING_X, y), txt, font=font_bold, fill=0)
                y += line_h_body
            else:
                draw.text((PADDING_X, y), txt, font=font_body, fill=0)
                y += line_h_body
        img = img.convert("1")
        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()

    if is_preview:
        add_t("*** PREVIEW ***")
        add_t("NOT A FINAL RECEIPT")
    if is_amended:
        add_t("*** AMENDED DELIVERY ***")

    add_company_header()
    add(sep)

    if is_credit:
        add_t("DELIVERY CONFIRMATION")
        add_t("CREDIT ACCOUNT")
    elif is_collected:
        add_t("DELIVERY CONFIRMATION")
        add_t("PAYMENT COLLECTED")
    else:
        add_t("DELIVERY CONFIRMATION")
        add_t("PAYMENT DUE")
        add_t("STATUS: NOT COLLECTED")
    add(sep)

    receipt_no = str(data.get("receipt_no", "")).strip()
    date_str = str(data.get("date_str", "")).strip()
    add_b(_pad_right(f"Rcpt: {receipt_no}", f"Date: {date_str}", cols))

    route_no = str(data.get("route_no", "")).strip()
    stop_no = data.get("stop_no", "")
    try:
        stop_no = str(int(float(stop_no))).zfill(3)
    except Exception:
        stop_no = str(stop_no).zfill(3) if stop_no else "---"
    driver = str(data.get("driver_name", "")).strip()

    add_b(f"Route: {route_no}  Stop: {stop_no}")
    add_b(f"Driver: {driver}")

    cust_code = str(data.get("customer_code", "")).strip()
    if cust_code:
        add(f"Cust Code: {cust_code}")
    add(sep)

    add_b("CUSTOMER")
    for w in wrap(data.get("customer_name", "")):
        add(w)
    for w in wrap(data.get("customer_addr", "")):
        add(w)
    add(sep)

    invoices = data.get("invoices") or []
    add_b(f"INVOICES ({len(invoices)})")
    for inv in invoices:
        inv_no = str(inv.get("invoice_no", "")).strip()
        inv_total = inv.get("total")
        if inv_total is not None:
            add(_pad_right(f"  {inv_no}", money(inv_total), cols))
        else:
            add(f"  {inv_no}")
    add(sep)

    add_b("AMOUNTS")
    expected = Decimal(str(data.get("expected", "0") or "0"))
    collected = Decimal(str(data.get("collected", "0") or "0"))
    balance = Decimal(str(data.get("balance_due", "") or (expected - collected)))

    add_b(_pad_right("  Expected:", money(expected), cols))
    add_b(_pad_right("  Collected:", money(collected), cols))

    if is_collected:
        pm = str(data.get("payment_method", "")).upper().replace("_", " ").strip()
        add_b(_pad_right("  Method:", pm or "-", cols))

        if (data.get("payment_method") or "").lower() == "cheque":
            if data.get("cheque_number"):
                add_b(_pad_right("  Cheque No:", str(data["cheque_number"]), cols))
            if data.get("cheque_date"):
                add_b(_pad_right("  Cheque Date:", str(data["cheque_date"]), cols))

        if (data.get("payment_method") or "").lower() == "cash":
            if data.get("cash_received") is not None:
                add_b(_pad_right("  Cash Received:", money(data["cash_received"]), cols))
            if data.get("change_given") is not None:
                add_b(_pad_right("  Change Given:", money(data["change_given"]), cols))

    if is_credit:
        add_b("  ON ACCOUNT - NO BALANCE DUE")
    else:
        if (not is_collected) or (balance > 0):
            add_b(_pad_right("  BALANCE DUE:", money(balance), cols))

    add(sep)

    notes = (data.get("notes") or "").strip()
    if notes:
        add_b("NOTES")
        for w in wrap(notes):
            add("  " + w if len(w) <= cols - 2 else w[:cols])
        add(sep)

    exceptions = data.get("exceptions") or []
    if exceptions:
        add_b(f"EXCEPTIONS ({len(exceptions)})")
        for exc in exceptions:
            exc_type = str(exc.get("type", "")).upper()
            item = str(exc.get("item_name", ""))
            qty_e = exc.get("qty_expected", "")
            qty_a = exc.get("qty_actual", "")
            add(f"  {exc_type}: {item}")
            add(f"  Exp: {qty_e} | Act: {qty_a}")
            note = (exc.get("note") or "").strip()
            if note:
                for w in wrap(note):
                    add(f"  {w}")
        add(sep)

    sig_line = "_" * cols
    add("")
    add("Customer Signature (Delivery):")
    add(sig_line)
    add("")

    if is_collected:
        add("Customer Signature (Payment):")
        add(sig_line)
        add("")

    add("Driver Signature:")
    add(sig_line)

    line_h_body = 36
    line_h_title = 44
    height = PADDING_Y * 2 + sum(
        line_h_title if t == "title" else line_h_body for t, _ in lines
    )
    img = Image.new("L", (dot_width, height), 255)
    draw = ImageDraw.Draw(img)

    y = PADDING_Y
    avail_w = dot_width - 2 * PADDING_X

    for typ, txt in lines:
        if typ == "title":
            tw = draw.textlength(txt, font=font_title)
            x = PADDING_X + max(0, (avail_w - tw) / 2)
            draw.text((x, y), txt, font=font_title, fill=0)
            y += line_h_title
        elif typ == "center":
            tw = draw.textlength(txt, font=font_body)
            x = PADDING_X + max(0, (avail_w - tw) / 2)
            draw.text((x, y), txt, font=font_body, fill=0)
            y += line_h_body
        elif typ == "bold":
            draw.text((PADDING_X, y), txt, font=font_bold, fill=0)
            y += line_h_body
        else:
            draw.text((PADDING_X, y), txt, font=font_body, fill=0)
            y += line_h_body

    img = img.convert("1")
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
