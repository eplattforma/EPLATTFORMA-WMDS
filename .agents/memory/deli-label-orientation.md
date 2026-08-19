---
name: Deli label orientation
description: Physical page geometry required for box labels printed on the Deli DL-750W.
---

Generate Deli DL-750W box labels as a plain 105×70 mm landscape PDF page with NO canvas translate/rotate — any rotation the media needs is handled entirely by the printer driver / print agent settings on the office PC. Keep every element inside a 5 mm safe margin because the thermal head cannot print the outer ~4–5 mm.

**Why:** After several rounds of physical tests (unflipped landscape, 180° flip, and 70×105 portrait with -90° rotation), the user settled on a clean landscape PDF with orientation owned by the driver side. Content at the edge gets shaved off regardless of orientation, so the margin stays.

**How to apply:** When changing the label PDF, keep pagesize=(105*mm, 70*mm), zero transforms, and the 5 mm margin. Orientation problems on paper are fixed in the Deli driver or print_agent.ps1, not in the PDF code.
