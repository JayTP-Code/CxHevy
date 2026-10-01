# Day & Iron dashboard

Live page: https://claude.ai/artifact/Nn4VJTCBrP2UoD9xVuLEXy (private to you)

- **To-dos** are stored in the page's own database (`todos` collection), so they sync across devices.
- **Calendar** shows the next 7 days from your Google Calendar connector, refreshed every 5 minutes.
  Any event can be turned into a to-do with "+ To-do".
- **Training** reads the `gym/summary` document, built from Hevy by `scripts/hevy_sync.py`.

## Syncing Hevy

1. Put your Hevy API key (Hevy Pro → Settings → Developer) in `.env` as `HEVY_API_KEY=...`,
   or set it as an environment secret for cloud sessions.
2. Ask Claude to "sync the gym dashboard". It runs `python scripts/hevy_sync.py`,
   which writes `data/gym_summary.json`, then loads that file into the page's `gym/summary` document.

`data/` and `.env` are git-ignored, so your workout data and key stay out of the repo.
