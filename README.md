# pixiepath-live

Live data for the **Pixie Path** iOS app. Nothing here is secret; the app reads it anonymously.

- `drinks-menu.json` — overrides the app lays over its built-in drink menus once a day: exact **verified** prices with the date they were seen, notes, added/removed drinks, venue hours and house picks. Schema is described in the file's `about` field.
- `drinks-catalog.json` — the app's built-in menu (parks → venues → drinks with estimate ranges), exported so the daily routine knows what to check. Regenerated whenever the app's menu changes.

A scheduled Claude Code cloud routine re-checks public Walt Disney World menu sources every morning and commits changes to `drinks-menu.json`.
