---
name: Flask test app blueprint & template rendering
description: How to get pages extending base.html to render in the pytest app fixture
---

**Rule:** The pytest `app` fixture (tests/conftest.py) registers only a few blueprints. Any test rendering a page that extends `base.html` will hit two failures: `has_permission` undefined (fix: call `services.permissions.register_template_helpers(app)` once, guard with a flag attr on app), and `BuildError` for the ~30 nav endpoints of unregistered blueprints (fix: wrap jinja `url_for` in a lenient version returning `#` on BuildError for just those tests — see tests/test_receipt_controls.py `lenient_urls` fixture).

**Why:** Registering all main.py blueprints in tests is impractical, and Flask forbids blueprint registration after the app serves its first request — so any blueprint a test file needs must be registered in the FIRST fixture, before any request, or later test classes get "setup method can no longer be called" errors.

**How to apply:** In new test files needing extra blueprints or full-page renders, register every needed blueprint (and template helpers) up-front in one fixture, guarded by `'name' not in app.blueprints`.
