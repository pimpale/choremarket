# Choremarket

A small FastAPI + SQLite app with a React-Bootstrap frontend for a roommate chore market.

The data model splits chores into **recurring chores** (templates) and **chore instances**
(the actual ledger rows). Weeks run **Sunday → Saturday**; a background scheduler spawns one
instance per active recurring chore each week (with catch-up on startup), auto-assigning it via
the AGV mechanism from each roommate's recurring-chore preferences. One-off chores are entered
directly onto the ledger. Every instance has mutually-exclusive **done** / **failed** state —
money only pays out when an instance is marked done.

Pages:

- **Ledger** (start page) — the global ledger of all instances, shaded by week/status
  (done = gray, current week = white, upcoming week = light green), with done/failed checkboxes
  and one-off entry.
- **Roommate Preferences** — per-roommate WTP/bid for each recurring chore.
- **Recurring Chores** — manage the weekly templates.
- **Admin** — roommate management + mock-data reset.
- **Overall Balances** — net balances (done instances only) and a settle-up plan.

## Run

Backend:

```bash
uv sync
uv run uvicorn backend.app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Then open the local URL printed by Vite.

The SQLite database is created automatically at `backend/choremarket.sqlite3`.

For local testing, the Admin page has a mock-data reset that recreates roommates,
chores, generated bids/WTPs, and historical ledger rows.
