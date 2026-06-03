---
name: SMTP Brevo relay setup
description: How PO email sending is configured when using Brevo as the SMTP relay
---

## Rule
When SMTP_HOST=smtp-relay.brevo.com, two separate env vars are required:
- `SMTP_EMAIL` — Brevo authentication key (e.g. `ad6cab001@smtp-brevo.com`)
- `SMTP_FROM` — visible sender address (e.g. `purchase.orders@eplattforma.com.cy`)

The code reads `SMTP_FROM = os.getenv("SMTP_FROM", "") or SMTP_EMAIL` so it falls back gracefully if SMTP_FROM is not set.

**Why:** Brevo uses a per-account SMTP key for auth that is different from the actual business email address. Using the Brevo key as the From header causes Gmail to silently drop the email because the sending domain (smtp-brevo.com) has no relationship to the stated From domain.

**How to apply:** Any time SMTP_HOST/SMTP_EMAIL are changed to a relay service (Brevo, SendGrid, etc.), always check whether a separate SMTP_FROM secret is needed. The fix is already in `_send_po_email` in `blueprints/replenishment_mvp.py`.

## Also fixed in same session
- Added `Date`, `Message-ID`, `Reply-To` headers (RFC 2822 required — missing them causes silent drops on some relays)
- Switched from `SMTP_SSL` port 465 to `SMTP + STARTTLS` port 587 (required for Brevo relay)
- `sendmail()` return value now checked — rejected recipients logged as errors
