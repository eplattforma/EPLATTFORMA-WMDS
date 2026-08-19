---
name: Deli label orientation
description: Physical page geometry required for box labels printed on the Deli DL-750W.
---

Generate Deli DL-750W box labels on a 105×70 mm landscape PDF page, drawing the existing landscape design directly without canvas rotation or translation.

**Why:** The physical label feed and the label design are both landscape; rotating onto a portrait page produces an incorrectly oriented label.

**How to apply:** Preserve the landscape page dimensions and untransformed coordinates whenever changing the label PDF or print-agent settings.