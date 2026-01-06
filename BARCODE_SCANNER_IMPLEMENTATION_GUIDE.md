# Barcode Scanner Implementation Guide

## Overview
This guide shows you how to implement the camera-based barcode scanning functionality from this warehouse system in any other Replit app. The scanner uses QuaggaJS to read various barcode formats directly from a device's camera.

**Works on:** Mobile phones, tablets, laptops with webcams  
**Framework:** Flask + Vanilla JavaScript  
**Dependencies:** QuaggaJS (CDN), Bootstrap 5 (optional for styling), Font Awesome (optional for icons)

## Supported Barcode Formats
- Code 128 (most warehouse barcodes)
- EAN-13 / EAN-8 (retail products)
- UPC-A / UPC-E (North American products)
- Code 39
- Codabar

---

## Quick Start (5 Minutes)

If you just want to test the scanner without PS365 integration:

1. Add QuaggaJS CDN to your HTML
2. Copy the HTML structure from Step 2
3. Copy CSS from Step 3
4. Copy JavaScript from Step 4
5. Use the mock backend from Step 7 (Testing Without PS365)
6. Open on your phone and click "Scan"

---

## Step 1: Add Required Libraries

Add these to your HTML `<head>` section:

```html
<!-- QuaggaJS for barcode scanning -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/quagga/0.12.1/quagga.min.js"></script>

<!-- Bootstrap 5 (optional, for styling) -->
<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>

<!-- Font Awesome (optional, for icons) -->
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
```

---

## Step 2: Add HTML Structure

Add this HTML to your page where you want the scanner to appear:

```html
<!-- Scanner Container -->
<div class="card mb-3">
    <div class="card-body">
        <!-- Video viewport for camera feed -->
        <div id="scannerViewport" class="scanner-off mb-3"></div>
        
        <!-- Scanner controls -->
        <div class="d-flex gap-2" style="min-height: 120px;">
            <!-- Left: Manual barcode input -->
            <div style="flex: 0 0 250px; display: flex; flex-direction: column; justify-content: space-between;">
                <div>
                    <input type="text" id="manualCode" class="form-control form-control-lg" 
                           placeholder="Enter barcode..." autofocus maxlength="20" 
                           style="width: 100%; font-family: monospace;">
                </div>
                <div class="d-flex gap-2">
                    <button class="btn btn-secondary btn-lg w-100" onclick="lookupBarcode()">
                        <i class="fas fa-search"></i> Lookup
                    </button>
                </div>
            </div>
            
            <!-- Right: Scan button -->
            <div style="flex: 1;">
                <button class="btn btn-outline-primary w-100 h-100 d-flex align-items-center justify-content-center flex-column" 
                        onclick="toggleScanner()" id="scannerBtn">
                    <i class="fas fa-barcode fa-3x mb-2"></i>
                    <span style="font-size: 1.2rem;">Scan</span>
                </button>
            </div>
        </div>
    </div>
</div>

<!-- Results display area -->
<div id="scanResults"></div>
```

---

## Step 3: Add CSS Styles

Add these styles to your page (in `<style>` tag or CSS file):

```css
/* Scanner viewport styling */
#scannerViewport {
    position: relative;
    width: 100%;
    max-width: 600px;
    margin: 0 auto;
}

#scannerViewport video {
    width: 100%;
    border-radius: 8px;
}

#scannerViewport canvas.drawingBuffer {
    position: absolute;
    top: 0;
    left: 0;
}

/* Hide scanner when not active */
.scanner-off {
    display: none;
}

/* Remove number input arrows (optional) */
input[type=number]::-webkit-outer-spin-button,
input[type=number]::-webkit-inner-spin-button {
    -webkit-appearance: none;
    margin: 0;
}
input[type=number] {
    -moz-appearance: textfield;
}
```

---

## Step 4: Add JavaScript Scanner Logic

Add this JavaScript code to your page:

```javascript
let scannerOn = false;
let lastScanned = null;

// Toggle scanner on/off
function toggleScanner() {
    const viewport = document.getElementById('scannerViewport');
    const btn = document.getElementById('scannerBtn');
    
    if (scannerOn) {
        // Stop scanner
        Quagga.stop();
        viewport.classList.add('scanner-off');
        btn.innerHTML = '<i class="fas fa-barcode fa-3x mb-2"></i><span style="font-size: 1.2rem;">Scan</span>';
        scannerOn = false;
    } else {
        // Start scanner
        viewport.classList.remove('scanner-off');
        startScanner();
        btn.innerHTML = '<i class="fas fa-stop fa-3x mb-2"></i><span style="font-size: 1.2rem;">Stop</span>';
        scannerOn = true;
    }
}

// Close scanner (called after successful scan)
function closeScanner() {
    if (scannerOn) {
        Quagga.stop();
        const viewport = document.getElementById('scannerViewport');
        const btn = document.getElementById('scannerBtn');
        viewport.classList.add('scanner-off');
        btn.innerHTML = '<i class="fas fa-barcode fa-3x mb-2"></i><span style="font-size: 1.2rem;">Scan</span>';
        scannerOn = false;
    }
}

// Initialize and start the barcode scanner
function startScanner() {
    Quagga.init({
        inputStream: {
            name: "Live",
            type: "LiveStream",
            constraints: {
                facingMode: { ideal: "environment" },  // Use back camera on mobile
                width: { ideal: 1280 },
                height: { ideal: 720 }
            },
            target: document.querySelector('#scannerViewport')  // Where to display camera feed
        },
        decoder: {
            readers: [
                "code_128_reader",    // Common warehouse barcodes
                "ean_reader",         // EAN-13
                "ean_8_reader",       // EAN-8
                "upc_reader",         // UPC-A
                "upc_e_reader",       // UPC-E
                "code_39_reader",     // Code 39
                "codabar_reader"      // Codabar
            ]
        },
        locate: true,  // Try to locate barcode position
        debug: {
            drawBoundingBox: true,    // Draw box around detected barcode
            showFrequency: false,     // Don't show frequency analysis
            showPattern: false        // Don't show pattern analysis
        }
    }, function(err) {
        if (err) {
            console.error("Camera init failed:", err);
            alert('Unable to access camera. Please check permissions or try refreshing.');
            closeScanner();
            return;
        }
        Quagga.start();
        scannerOn = true;
    });

    // Handle barcode detection
    Quagga.onDetected(function(result) {
        const code = result.codeResult.code;
        
        // Prevent duplicate scans
        if (code !== lastScanned) {
            lastScanned = code;
            
            // Put barcode in input field
            document.getElementById('manualCode').value = code;
            
            // Auto-close scanner
            closeScanner();
            
            // Process the barcode (call your custom function)
            lookupBarcode();
            
            // Provide haptic feedback on mobile
            if (navigator.vibrate) navigator.vibrate(200);
        }
    });
}

// Lookup barcode (customize this for your app)
async function lookupBarcode() {
    const barcode = document.getElementById('manualCode').value.trim();
    
    if (!barcode) {
        alert('Please enter or scan a barcode');
        return;
    }
    
    // Show loading indicator
    document.getElementById('scanResults').innerHTML = 
        '<div class="alert alert-info"><i class="fas fa-spinner fa-spin"></i> Looking up barcode...</div>';
    
    try {
        // CUSTOMIZE THIS: Replace with your own API endpoint
        const response = await fetch('/api/lookup-barcode', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({barcode: barcode})
        });
        
        const data = await response.json();
        
        if (data.ok) {
            // CUSTOMIZE THIS: Display your results
            document.getElementById('scanResults').innerHTML = `
                <div class="alert alert-success">
                    <h5>✓ Found!</h5>
                    <p><strong>Item Code:</strong> ${data.item_code}</p>
                    <p><strong>Name:</strong> ${data.item_name}</p>
                </div>
            `;
        } else {
            document.getElementById('scanResults').innerHTML = 
                `<div class="alert alert-danger">❌ ${data.error || 'Item not found'}</div>`;
        }
        
        // Clear input for next scan
        document.getElementById('manualCode').value = '';
        document.getElementById('manualCode').focus();
        
    } catch (err) {
        console.error('Lookup error:', err);
        document.getElementById('scanResults').innerHTML = 
            `<div class="alert alert-danger">❌ Error: ${err.message}</div>`;
    }
}

// Allow Enter key to trigger lookup
document.addEventListener('DOMContentLoaded', function() {
    document.getElementById('manualCode').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            lookupBarcode();
        }
    });
});
```

---

## Step 5: Create Backend API Endpoint (Flask)

### 5A. Install Required Python Package

Add `requests` to your project if not already installed:

```bash
pip install requests
```

Or add to `requirements.txt`:
```
flask
requests
```

### 5B. Create the Flask Route

**Option 1: Add to existing `app.py` or `main.py`:**

```python
import os
import requests
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

# PS365 API Configuration (from environment variables)
POWERSOFT_BASE = os.getenv("POWERSOFT_BASE", "").rstrip("/")
POWERSOFT_TOKEN = os.getenv("POWERSOFT_TOKEN", "")

@app.route('/')
def index():
    """Main page with barcode scanner"""
    return render_template('scanner.html')

@app.route('/api/lookup-barcode', methods=['POST'])
def api_lookup_barcode():
    """
    Lookup item by barcode via PS365 API
    
    Request JSON:
        {
            "barcode": "1234567890123"
        }
    
    Response JSON (success):
        {
            "ok": true,
            "item_code": "ITEM-001",
            "item_name": "Example Product",
            "barcode": "1234567890123"
        }
    
    Response JSON (error):
        {
            "ok": false,
            "error": "No item found for this barcode"
        }
    """
    data = request.get_json()
    barcode = data.get('barcode', '').strip()
    
    # Validate input
    if not barcode:
        return jsonify({'ok': False, 'error': 'No barcode provided'}), 400
    
    # Check PS365 configuration
    if not POWERSOFT_BASE or not POWERSOFT_TOKEN:
        return jsonify({'ok': False, 'error': 'PS365 not configured'}), 500
    
    try:
        # Build PS365 API request
        search_payload = {
            "api_credentials": {
                "token": POWERSOFT_TOKEN
            },
            "search_option": {
                "only_counted": "N",
                "page_number": 1,
                "page_size": 10,
                "expression_searched": barcode,
                "search_operator_type": "Equals",
                "search_in_fields": "ItemBarcode,ItemCode",  # Search both barcode and item code
                "active_type": "all"
            }
        }
        
        # Call PS365 API
        url = f"{POWERSOFT_BASE}/search_item"
        r = requests.post(url, json=search_payload, timeout=(10, 30))
        r.raise_for_status()
        result = r.json()
        
        # Check API response status
        api_resp = result.get("api_response", {})
        if str(api_resp.get("response_code")) != "1":
            return jsonify({
                'ok': False, 
                'error': f"PS365 Error: {api_resp.get('response_msg', 'Unknown error')}"
            }), 400
        
        # Extract items from response
        items = result.get("list_items", [])
        if not items:
            return jsonify({'ok': False, 'error': 'No item found for this barcode'}), 404
        
        # Return first matching item
        first_item = items[0]
        return jsonify({
            'ok': True,
            'item_code': first_item.get('item_code_365'),
            'item_name': first_item.get('item_name'),
            'barcode': barcode
        }), 200
        
    except requests.exceptions.Timeout:
        return jsonify({'ok': False, 'error': 'PS365 request timed out'}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({'ok': False, 'error': f'PS365 connection error: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Server error: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
```

**Option 2: Using Blueprint (for larger apps):**

Create `routes_scanner.py`:

```python
import os
import requests
from flask import Blueprint, request, jsonify

scanner_bp = Blueprint('scanner', __name__)

POWERSOFT_BASE = os.getenv("POWERSOFT_BASE", "").rstrip("/")
POWERSOFT_TOKEN = os.getenv("POWERSOFT_TOKEN", "")

@scanner_bp.route('/api/lookup-barcode', methods=['POST'])
def api_lookup_barcode():
    # ... same code as above ...
    pass
```

Then register in `app.py`:

```python
from routes_scanner import scanner_bp

app = Flask(__name__)
app.register_blueprint(scanner_bp)
```

### 5C. API Contract Documentation

**Request Format:**
```json
POST /api/lookup-barcode
Content-Type: application/json

{
    "barcode": "1234567890123"
}
```

**Success Response (200):**
```json
{
    "ok": true,
    "item_code": "ITEM-001",
    "item_name": "Example Product Name",
    "barcode": "1234567890123"
}
```

**Error Response (400/404/500):**
```json
{
    "ok": false,
    "error": "No item found for this barcode"
}
```

**Required Fields:**
- `ok` (boolean): Indicates success/failure
- `error` (string): Error message if `ok` is false
- `item_code` (string): Item identifier if found
- `item_name` (string): Item description if found
- `barcode` (string): The original barcode searched

---

## Step 6: Add Environment Variables (Replit Secrets)

Add these secrets to your Replit project:

1. Click **Tools** → **Secrets** in the left sidebar
2. Click **+ New Secret**
3. Add the following:

| Secret Name | Example Value | Description |
|------------|---------------|-------------|
| `POWERSOFT_BASE` | `http://api.powersoft365.com` | PS365 API base URL (without trailing slash) |
| `POWERSOFT_TOKEN` | `your-token-here` | PS365 API authentication token |

**Note:** Environment variables are automatically available in your Flask app via `os.getenv()`.

---

## Step 7: Testing Without PS365 (Mock Implementation)

If you don't have PS365 credentials yet or want to test the scanner independently, use this mock endpoint:

```python
@app.route('/api/lookup-barcode', methods=['POST'])
def api_lookup_barcode():
    """Mock barcode lookup - no PS365 required"""
    data = request.get_json()
    barcode = data.get('barcode', '').strip()
    
    if not barcode:
        return jsonify({'ok': False, 'error': 'No barcode provided'}), 400
    
    # Simulate database lookup with mock data
    mock_database = {
        '1234567890123': {'item_code': 'ITEM-001', 'item_name': 'Test Product A'},
        '9876543210987': {'item_code': 'ITEM-002', 'item_name': 'Test Product B'},
        '5555555555555': {'item_code': 'ITEM-003', 'item_name': 'Test Product C'},
    }
    
    # Check if barcode exists in mock database
    if barcode in mock_database:
        item = mock_database[barcode]
        return jsonify({
            'ok': True,
            'item_code': item['item_code'],
            'item_name': item['item_name'],
            'barcode': barcode
        }), 200
    else:
        # For any other barcode, create a dynamic response
        return jsonify({
            'ok': True,
            'item_code': f'ITEM-{barcode[:6]}',
            'item_name': f'Mock Product {barcode}',
            'barcode': barcode
        }), 200
```

This allows you to test the full scanning workflow without external dependencies.

---

## Step 8: Create HTML Template

Save this as `templates/scanner.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Barcode Scanner</title>
    
    <!-- QuaggaJS -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/quagga/0.12.1/quagga.min.js"></script>
    
    <!-- Bootstrap 5 -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    
    <!-- Font Awesome -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    
    <style>
        /* Add CSS from Step 3 here */
    </style>
</head>
<body>
    <div class="container mt-4">
        <h2 class="text-center mb-4">📦 Barcode Scanner</h2>
        
        <!-- Add HTML from Step 2 here -->
        
    </div>
    
    <script>
        // Add JavaScript from Step 4 here
    </script>
</body>
</html>
```

---

## Complete Working Example

Here's a minimal working Flask app with barcode scanning:

**File: `main.py`**
```python
import os
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('scanner.html')

@app.route('/api/lookup-barcode', methods=['POST'])
def api_lookup_barcode():
    data = request.get_json()
    barcode = data.get('barcode', '').strip()
    
    if not barcode:
        return jsonify({'ok': False, 'error': 'No barcode provided'}), 400
    
    # Mock response
    return jsonify({
        'ok': True,
        'item_code': f'ITEM-{barcode[:6]}',
        'item_name': f'Product {barcode}',
        'barcode': barcode
    }), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
```

**File: `templates/scanner.html`**
- Copy the HTML template from Step 8
- Insert CSS from Step 3
- Insert HTML from Step 2
- Insert JavaScript from Step 4

**Run:**
```bash
python main.py
```

Open on your phone at `https://your-repl-name.your-username.repl.co` and click "Scan"!

---

## Configuration Options

### Camera Settings
```javascript
constraints: {
    facingMode: { ideal: "environment" },  // Options: "environment" (back), "user" (front)
    width: { ideal: 1280 },                // Higher = better accuracy, slower performance
    height: { ideal: 720 }
}
```

### Barcode Readers
Enable only the formats you need for better performance:

```javascript
readers: [
    "code_128_reader",    // Warehouse/shipping labels
    "ean_reader",         // Retail products (Europe)
    "upc_reader"          // Retail products (USA/Canada)
]
```

### Debug Mode
```javascript
debug: {
    drawBoundingBox: true,     // Show green box around barcode
    showFrequency: true,       // Show signal frequency (for debugging)
    showPattern: true          // Show pattern analysis (for debugging)
}
```

---

## Mobile Optimization Tips

1. **Camera Permissions**: The browser will ask for camera access on first use
2. **HTTPS Required**: Camera access requires HTTPS (Replit apps automatically use HTTPS)
3. **Better Lighting**: Scanner works best in good lighting conditions
4. **Distance**: Hold barcode 4-8 inches from camera for best results
5. **Haptic Feedback**: Uses `navigator.vibrate()` on supported devices

---

## Troubleshooting

### Camera Won't Start
- Check browser permissions (Settings → Site Settings → Camera)
- Ensure HTTPS connection (not HTTP)
- Try refreshing the page

### Poor Scan Accuracy
- Increase camera resolution (width/height)
- Improve lighting conditions
- Enable only needed barcode formats
- Ensure barcode is flat and not damaged

### Performance Issues
- Reduce camera resolution
- Enable fewer barcode readers
- Disable debug visualization

---

## Example Use Cases

1. **Inventory Management**: Scan items to check stock levels
2. **Order Receiving**: Match scanned barcodes to purchase orders
3. **Product Lookup**: Quick product information retrieval
4. **Asset Tracking**: Track equipment or tools
5. **Point of Sale**: Scan products for checkout

---

## Deployment Checklist

Before deploying your barcode scanner app:

- [ ] Test camera access on HTTPS (Replit provides this automatically)
- [ ] Verify all CDN scripts load correctly
- [ ] Test on actual mobile device (not just desktop)
- [ ] Add PS365 credentials to Replit Secrets (if using PS365)
- [ ] Test with real barcodes in your environment
- [ ] Check timeout values for your network conditions
- [ ] Consider adding error logging for production use
- [ ] Test haptic feedback on supported devices

---

## Additional Resources

- **QuaggaJS Documentation**: https://serratus.github.io/quaggaJS/
- **Supported Barcode Types**: https://github.com/serratus/quaggaJS#supported-formats
- **Camera API**: https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia

---

## Implementation Checklist

Follow these steps to add barcode scanning to your Replit app:

- [ ] **Step 1:** Add QuaggaJS, Bootstrap, and Font Awesome CDN links
- [ ] **Step 2:** Copy HTML structure (scanner viewport + controls)
- [ ] **Step 3:** Copy CSS styles (scanner viewport, buttons)
- [ ] **Step 4:** Copy JavaScript functions (scanner init, detection, lookup)
- [ ] **Step 5:** Create Flask backend endpoint `/api/lookup-barcode`
- [ ] **Step 6:** Add PS365 credentials to Replit Secrets (or skip for testing)
- [ ] **Step 7:** Test with mock backend first
- [ ] **Step 8:** Create `templates/scanner.html` template
- [ ] **Deploy:** Test on actual mobile device with real barcodes

---

## Summary

This guide provides everything you need to add professional barcode scanning to any Flask-based Replit app:

✅ **Works on mobile devices** - Uses device camera for scanning  
✅ **No additional hardware** - Just a smartphone/tablet with camera  
✅ **Multiple barcode formats** - EAN, UPC, Code 128, Code 39, Codabar  
✅ **PS365 Integration** - Ready for Powersoft365 API (or use mock data)  
✅ **Standalone & Complete** - Copy/paste and start scanning immediately  
✅ **Production-ready** - Includes error handling, timeouts, feedback

The scanner will work on any modern smartphone or tablet with a camera, making it perfect for warehouse operations, inventory management, retail checkout, or any application that needs barcode scanning capabilities.

---

## Table of Contents

1. [Quick Start (5 Minutes)](#quick-start-5-minutes)
2. [Step 1: Add Required Libraries](#step-1-add-required-libraries)
3. [Step 2: Add HTML Structure](#step-2-add-html-structure)
4. [Step 3: Add CSS Styles](#step-3-add-css-styles)
5. [Step 4: Add JavaScript Scanner Logic](#step-4-add-javascript-scanner-logic)
6. [Step 5: Create Backend API Endpoint (Flask)](#step-5-create-backend-api-endpoint-flask)
7. [Step 6: Add Environment Variables](#step-6-add-environment-variables-replit-secrets)
8. [Step 7: Testing Without PS365](#step-7-testing-without-ps365-mock-implementation)
9. [Step 8: Create HTML Template](#step-8-create-html-template)
10. [Complete Working Example](#complete-working-example)
11. [Configuration Options](#configuration-options)
12. [Mobile Optimization Tips](#mobile-optimization-tips)
13. [Troubleshooting](#troubleshooting)
14. [Deployment Checklist](#deployment-checklist)
