# pixiepath-live

Live data for the **Pixie Path** iOS app. Nothing here is secret; the app reads it anonymously.

- `drinks-menu.json` — overrides the app lays over its built-in drink menus once a day: exact **verified** prices with the date the menu showed, notes, added/removed drinks, venue hours and house picks. The schema is described in the file's `about` field.
- `drinks-catalog.json` — the app's built-in menu (parks → venues → drinks with estimate ranges), exported so the sweep knows what to look for. Regenerated whenever the app's menu changes.
- `sources.json` — which public menu pages to read for each venue.
- `sweep.py` — the sweep itself: plain Python, standard library only, no AI in the loop. It fetches each venue's pages, parses drink names and prices from the HTML, matches them to the catalog conservatively (exact or ≥86% name similarity), and rewrites `drinks-menu.json`. Drinks it cannot find stay estimates in the app; a verified drink missing from its page for 21 days is marked removed; a price not re-seen in 120 days falls back to an estimate.

`.github/workflows/sweep.yml` runs the sweep every morning at 6am Eastern and commits only when something changed. Run it by hand from the Actions tab, or locally with `python3 sweep.py --dry-run`.
