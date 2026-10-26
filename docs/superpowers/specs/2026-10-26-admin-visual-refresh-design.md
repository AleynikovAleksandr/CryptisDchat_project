# CryptisDchat admin visual refresh

## Intent

Bring every admin page into the visual language of the main CryptisDchat application while preserving the existing top navigation, page structure, routes, forms, data, and administrator workflows.

## Goals

- Match the application's light neutral palette, typography, blue accent, rounded controls, thin borders, and restrained shadows.
- Give the top navigation a clear active state and make dashboard cards, tables, forms, status badges, alerts, and destructive actions feel like one coherent system.
- Keep dense operational data easy to scan on desktop and make all admin pages usable on phone-sized screens.
- Keep the existing glass login screen, adjusted only where needed to align tokens and branding with the refreshed panel.

## Non-goals and constraints

- Do not replace the top navigation with the application's sidebar.
- Do not change endpoints, permissions, form behavior, wording, database logic, or admin capabilities.
- Do not add a frontend framework or external asset dependency. The Flask and Jinja templates remain server rendered.
- Preserve clear red styling and explicit confirmation for destructive actions.

## Approaches considered

1. **Main application style with the current top navigation — selected.** Reuse the application's visual tokens and interaction patterns while retaining the admin information architecture. This gives the closest brand match and remains practical for tables.
2. **Glass styling from the login page.** More decorative, but blur and transparency reduce clarity in data-heavy screens.

## Design

- Move shared admin styling out of the base template into a dedicated stylesheet so every page uses the same tokens and responsive rules.
- Keep a sticky white top bar. Use the CryptisDchat mark and name at the left, rounded navigation tabs in the center, and a quiet outlined log-out action at the right. The active section uses the application's pale blue background and blue text.
- Use the same page background and content colors as the application: soft gray canvas, white surfaces, dark primary text, muted secondary text, and blue interactive accents.
- Restyle summary metrics as rounded white cards with subtle borders, compact labels, stronger values, and consistent spacing.
- Restyle tables as white rounded surfaces with a muted header, comfortable row spacing, hover feedback, and horizontal scrolling where columns cannot safely collapse.
- Restyle search, text inputs, selects, and textareas with rounded application-like borders and a blue focus ring. Keep pill-shaped primary and secondary buttons.
- Normalize status badges for active, warning, failure, pending, and neutral states. Keep danger actions red and visually separated from routine controls.
- Replace scattered inline presentation styles where needed with reusable semantic classes, without touching template logic.
- On screens below 768 px, allow the top navigation to scroll horizontally, stack headers and forms, reduce page padding, and turn tables into safe horizontal regions without page-level overflow.

## Verification

- Run the existing admin and full Python test suites.
- Open login, dashboard, users, user detail, reports, report detail, blockchain queue, delete confirmation, and not-found pages at desktop and phone widths.
- Verify active navigation, form submission controls, destructive-action emphasis, table scrolling, focus states, and absence of horizontal page overflow.
