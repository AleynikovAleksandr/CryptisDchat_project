# Admin Visual Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle every CryptisDchat admin page to match the main application while preserving the existing top navigation and all administrator behavior.

**Architecture:** Keep Flask/Jinja rendering and route logic unchanged. Add one dedicated admin stylesheet with application-derived design tokens and responsive rules, then replace page-specific inline presentation with reusable semantic classes. Verify behavior with the current admin tests and verify desktop/mobile layout in a real browser.

**Tech Stack:** Flask, Jinja2, HTML, CSS, Playwright, pytest

**Spec:** `docs/superpowers/specs/2026-10-26-admin-visual-refresh-design.md`

## Global Constraints

- Preserve the top navigation, routes, permissions, forms, data, wording, and admin capabilities.
- Do not add a frontend framework or external asset dependency.
- Keep server-rendered Flask/Jinja templates.
- Preserve explicit red danger styling and confirmation for destructive actions.
- Use the application's light gray canvas, white surfaces, dark text, muted text, and blue accent.

## Review Focus

- A 320 px viewport must keep the page itself free of horizontal overflow; wide tables scroll inside their own containers.
- Long UUIDs, TON addresses, errors, and user-agent strings must wrap or remain contained without widening cards.
- Detail routes must highlight the correct top-level navigation item.
- Destructive actions must remain visually distinct from normal and primary actions.
- Keyboard focus must remain visible on links, buttons, fields, selects, and textareas.

---

### Task 1: Shared admin visual system and top shell

**Files:**
- Create: `backend/static/css/admin.css`
- Modify: `backend/admin_app/templates/admin/base.html`
- Test: `tests/test_admin.py`

**Interfaces:**
- Consumes: Flask's existing `static` endpoint and current Jinja request/session variables.
- Produces: reusable CSS tokens and component classes used by every admin template; the existing class names remain supported during migration.

- [ ] **Step 1: Establish a behavioral baseline**

Run: `.venv/bin/python -m pytest tests/test_admin.py -q`
Expected: all existing admin tests pass.

- [ ] **Step 2: Create the shared stylesheet**

Define color, radius, shadow, spacing, and focus tokens in `:root`; style the page canvas, sticky top bar, branded navigation, cards, metrics, tables, forms, badges, flashes, avatars, buttons, danger surfaces, and responsive states. Include `overflow-wrap` and `min-width: 0` rules for long operational values, `:focus-visible` rules for all controls, a horizontally scrollable mobile navigation, and table-local overflow.

- [ ] **Step 3: Simplify the base template shell**

Link `css/admin.css`, remove the embedded style block, add semantic shell classes, keep all existing endpoint matching and CSRF logout behavior, and add `aria-current="page"` to the active navigation link.

- [ ] **Step 4: Verify behavior**

Run: `.venv/bin/python -m pytest tests/test_admin.py -q`
Expected: all admin tests pass; rendered authenticated pages reference `/admin/static/css/admin.css`.

- [ ] **Step 5: Commit**

```bash
git add backend/static/css/admin.css backend/admin_app/templates/admin/base.html
git commit -m "Restyle admin shell"
```

### Task 2: Page components and dense data layouts

**Files:**
- Modify: `backend/admin_app/templates/admin/dashboard.html`
- Modify: `backend/admin_app/templates/admin/users.html`
- Modify: `backend/admin_app/templates/admin/user_detail.html`
- Modify: `backend/admin_app/templates/admin/user_delete.html`
- Modify: `backend/admin_app/templates/admin/reports.html`
- Modify: `backend/admin_app/templates/admin/report_detail.html`
- Modify: `backend/admin_app/templates/admin/blockchain_queue.html`
- Modify: `backend/admin_app/templates/admin/not_found.html`
- Modify: `backend/admin_app/templates/admin/login.html`
- Modify: `backend/static/css/admin.css`
- Test: `tests/test_admin.py`

**Interfaces:**
- Consumes: the tokens and components from Task 1; all existing Jinja variables and form objects.
- Produces: consistent page headers, metric cards, section cards, filter pills, data labels, action panels, and responsive form layouts across every admin route.

- [ ] **Step 1: Convert dashboard and queue metrics**

Apply reusable page-header, metric-card, metric-label, metric-value, section-heading, and status classes. Remove presentation-only inline styles while keeping every metric and link unchanged.

- [ ] **Step 2: Convert user list and user detail**

Apply the shared search, person, metadata, action-group, status, and table classes. Ensure long addresses and device strings stay contained and the user detail actions stack at narrow widths.

- [ ] **Step 3: Convert reports, delete confirmation, and empty state**

Use shared filter tabs, detail grids, form stacks, danger panels, back links, and empty-state styling. Preserve the `DELETE` confirmation and all form actions exactly.

- [ ] **Step 4: Align the login screen**

Keep the approved glass composition and background, but replace duplicated color/radius/focus values with the shared visual tokens where practical. Preserve the password visibility control, validation messages, CSRF field, and reduced-motion behavior.

- [ ] **Step 5: Verify all server-rendered behavior**

Run: `.venv/bin/python -m pytest tests/test_admin.py -q`
Expected: all admin tests pass, including login lockout, user actions, deletion, reports, queue retry, metrics, CSRF, and authorization.

- [ ] **Step 6: Commit**

```bash
git add backend/admin_app/templates/admin backend/static/css/admin.css
git commit -m "Apply CryptisDchat styling to admin pages"
```

### Task 3: Browser layout regression coverage

**Files:**
- Create: `tests/e2e/e2e_admin.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: the dev server on ports 3890 and 3891 and the documented development administrator credentials.
- Produces: a Playwright smoke scenario with optional screenshots and a browser channel override.

- [ ] **Step 1: Add the browser smoke scenario**

Implement `tests/e2e/e2e_admin.py` to sign in, visit dashboard, users, reports, blockchain queue, and one available detail page; assert the active navigation state, stylesheet load, visible focus, and absence of page-level horizontal overflow. At 320 px, assert top navigation scrolling and table-local scrolling. Capture each page when `--shots DIR` is supplied and accept `--channel chrome` like the mobile app scenario.

- [ ] **Step 2: Run the smoke scenario before final adjustments**

Run: `.venv/bin/python tests/e2e/e2e_admin.py --channel chrome`
Expected: any remaining overflow or navigation assertion identifies the page needing a CSS adjustment.

- [ ] **Step 3: Fix only observed layout regressions**

Update `backend/static/css/admin.css` or semantic template classes for failures involving 320 px overflow, long values, active navigation, danger emphasis, or focus visibility.

- [ ] **Step 4: Document the browser check**

Add the admin E2E command and its desktop/mobile coverage to the README test section.

- [ ] **Step 5: Run final verification**

Run: `.venv/bin/python -m pytest -q`
Expected: all Python tests pass.

Run: `.venv/bin/python tests/e2e/e2e_admin.py --channel chrome`
Expected: `ADMIN E2E OK` with no browser console or page errors.

Run: `git diff --check`
Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add tests/e2e/e2e_admin.py README.md backend/static/css/admin.css backend/admin_app/templates/admin
git commit -m "Verify responsive admin layout"
```
