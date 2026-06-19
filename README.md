# Choremarket

A small FastAPI + SQLite app with a React-Bootstrap frontend for a roommate chore market. It has pages for:

- Admin roommate management
- Overall balances from recorded weekly ledgers
- Chore list management
- Weekly roommate preferences
- Weekly AGV-style ledger previews and snapshots

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
