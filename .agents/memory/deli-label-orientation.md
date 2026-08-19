---
name: Deli label orientation
description: Physical page geometry required for box labels printed on the Deli DL-750W.
---

Deli DL-750W box labels: the office pipeline prints pages turned 90° counter-clockwise (Sumatra does not rotate; the Deli driver maps pages onto 70 mm-wide portrait media internally). Compensate in code: 70×105 mm portrait PDF page with the 105×70 landscape design pre-rotated clockwise via `c.translate(70*mm, 0); c.rotate(90)`. Keep every element inside a 5 mm safe margin — the thermal head cannot print the outer ~4–5 mm.

**Why:** A published plain 105×70 landscape PDF printed needing a clockwise turn to read (confirmed by the user on paper), so the driver rotates CCW; the pre-rotated clockwise design cancels it. Earlier `translate(0,105*mm); rotate(-90)` produced the same wrong direction.

**How to apply:** Preserve the portrait pagesize, the +90° transform, and the 5 mm margin. Driver media MUST be set to 70×105 **portrait** (confirmed good on paper 20/08/2026: single label, upright text); the landscape media setting re-rotates and splits the print across two labels. Rotate 180 OFF, 100% scale; agent keeps `-print-settings "noscale"`.
