---
name: Deli label orientation
description: Physical page geometry required for box labels printed on the Deli DL-750W.
---

Generate Deli DL-750W box labels on a 105×70 mm landscape PDF page, flip the whole design 180° once (`c.translate(W, H); c.rotate(180)`) because the printer feeds the stock inverted, and keep all content inside a 5 mm safe margin — the thermal head cannot print the outer ~4–5 mm and clips anything at the edge.

**Why:** A physical test showed an unflipped landscape label prints upside-down, and content drawn closer than ~5 mm to any edge gets shaved off regardless of orientation.

**How to apply:** When changing the label PDF, preserve the landscape page size, the single 180° flip, and the 5 mm margin. On-screen the PDF looks upside-down — that is correct; it prints upright.
