# Parcel Workspace Web App

The local web app lives in `web/` and uses Vite + React. It is a panel-first internal tool shell with no Mapbox dependency in this pass.

## Run Locally

Start the API:

```bash
python scripts/run_api.py
```

Start the web app:

```bash
cd web
npm run dev
```

The web app reads `VITE_API_URL` and defaults to `http://127.0.0.1:8000`.

## Current Panels

- Folio + county parcel search.
- Parcel intelligence cards for eligibility, massing, jurisdiction, flags/data gaps, and latest market rent provenance.
- Template selector and hard-cost/acquisition/rent inputs.
- Feasibility result card backed by the API.
- Cost audit card backed by server-side OpenRouter calls.
- Parcel chat panel with suggested diligence prompts.
- Review status and notes controls.

## Deferred

Mapbox and geometry-driven map interactions are intentionally deferred. The current slice keeps the UI interoperable with the API and database without adding map token handling or map state.
