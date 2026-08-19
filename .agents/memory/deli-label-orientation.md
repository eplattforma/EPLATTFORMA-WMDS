---
name: Deli label orientation
description: Physical page geometry required for box labels printed on the Deli DL-750W.
---

The Deli DL-750W media is 70 mm wide (across the print head) × 105 mm long (feed direction) — portrait. Generate box labels on a 70×105 mm portrait PDF page, drawing the design in 105×70 landscape coordinates rotated onto it with `c.translate(0, 105*mm); c.rotate(-90)`. Keep every element inside a 5 mm safe margin — the thermal head cannot print the outer ~4–5 mm.

**Why:** Physical tests showed a 105×70 landscape page makes SumatraPDF rotate to fit ("prints vertically"), and content at the edge is shaved off. The page must match the physical media; the rotation lives in the code, not the driver.

**How to apply:** Preserve the 70×105 portrait pagesize, the -90° transform, and the 5 mm margin. Driver: paper 70×105 mm, Portrait, Rotate 180 OFF, 100% scale; agent keeps `-print-settings "noscale"`. If a physical print is upside-down, swap only the transform to `c.translate(70*mm, 0); c.rotate(90)`.
