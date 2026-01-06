import requests
import os
from datetime import datetime, timedelta

API_BASE = os.getenv("POWERSOFT_BASE", "https://doc4api.powersoft365.com")
API_TOKEN = os.getenv("POWERSOFT_TOKEN")
API_URL = f"{API_BASE}/list_loyalty_invoices_header"
INVOICE_NUMBER = "IN10051409"
CUSTOMER_CODE = "77700188"

# Use broad date range to ensure we find the invoice
from_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
to_date = datetime.now().strftime("%Y-%m-%d")

print(f"Using API URL: {API_URL}")
print(f"Searching for Invoice: {INVOICE_NUMBER}, Customer: {CUSTOMER_CODE}")
print(f"Date range: {from_date} to {to_date}")

payload = {
    "api_credentials": {
        "token": API_TOKEN
    },
    "filter_define": {
        "only_counted": "N",
        "page_number": 1,
        "page_size": 10,
        "invoice_type": "all",
        "invoice_number_selection": INVOICE_NUMBER,
        "invoice_customer_code_selection": CUSTOMER_CODE,
        "invoice_customer_phone_selection": "",
        "invoice_customer_email_selection": "",
        "invoice_customer_name_selection": "",
        "from_date": from_date,
        "to_date": to_date
    }
}

response = requests.post(API_URL, json=payload)

print(f"Response status: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    
    # Check for API error (response_code '1' means success)
    api_response = data.get("api_response", {})
    if api_response.get("response_code") not in ["1", "200"]:
        print(f"⚠️  API Error: {api_response.get('response_msg')}")
        print(f"Full API Response: {data}")
        exit(1)
    
    invoices = data.get("list_invoices") or []
    print(f"Number of invoices returned: {len(invoices)}")
    
    if invoices:
        invoice = invoices[0]
        total_grand = invoice.get("total_grand")
        print(f"\n✅ Invoice {INVOICE_NUMBER} - Gross Total: {total_grand}")
        print(f"Full invoice data: {invoice}")
    else:
        print(f"\n❌ Invoice {INVOICE_NUMBER} not found.")
        print("Possible reasons:")
        print("  - Invoice number doesn't exist")
        print("  - Different invoice number format expected")
        print("  - Invoice is outside the accessible scope")
else:
    print(f"❌ Error {response.status_code}: {response.text}")
