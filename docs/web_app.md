# Parcel Workspace Web App

The local web app lives in `web/` and uses Vite + React. It is a Mapbox-first parcel intelligence workspace: the map sits beside a sticky analyst panel for eligibility, massing, feasibility, audits, grounded parcel chat, notes, and review status.

## Run Locally

Start the API:

```bash
python scripts/run_api.py
```

Start the web app:

```bash
cd web
cp .env.example .env.local
# Edit .env.local and set VITE_MAPBOX_TOKEN if you want the map.
npm run dev
```

The web app reads:

- `VITE_API_URL`, defaulting to `http://127.0.0.1:8000`.
- `VITE_MAPBOX_TOKEN`, required for the Mapbox map. The Vite config also bridges the repo-root `MAPBOX_TOKEN` into `VITE_MAPBOX_TOKEN` for local development, exposing only the Mapbox browser token. Do not expose `DATABASE_URL`, `OPENROUTER_API_KEY`, service role keys, or other server-side secrets to Vite.

If `VITE_MAPBOX_TOKEN` is missing, the app shows a clear fallback card and the rest of the parcel workspace remains usable.

The Python virtual environment stays at the repository root, typically `.venv/`, for the API and workers. The web app does not use a Python venv; it uses npm packages plus the Vite env file in `web/.env.local`.

## Current Workspace

- Mapbox GL dark map can identify parcels by click via `GET /parcels/identify?lng=&lat=`, then renders the selected parcel boundary from `GET /parcels/{parcel_id}/geometry`, with a subtle fill, bright outline, and bounds fit. If geometry is unavailable, it falls back to the centroid from `GET /parcels/{parcel_id}/context`.
- Folio + county parcel command search. The first result loads automatically and fits the map to the selected parcel boundary when available.
- Map clicks show `Identifying parcel...` while the point lookup runs and a subtle `No parcel found here` message when no stored polygon covers the clicked point. Search selection uses the same context + geometry load flow.
- Marker color follows parcel status when only the centroid is available: eligible, needs review, ineligible, or unknown.
- Parcel intelligence header for address, folio, county, jurisdiction, eligibility, confidence, and audit state.
- Known / Estimated / Needs Verification source context strip for analyst confidence.
- Massing Sanity / Zoning Audit card with deterministic flags loaded automatically and an explicit AI reviewer button.
- Template selector and hard-cost/acquisition/rent inputs.
- Feasibility result card backed by the API.
- Cost audit card backed by server-side OpenRouter calls.
- Parcel chat panel with suggested diligence prompts, message bubbles, loading and empty states, and a source-context explanation.
- Review status and notes controls.

The massing audit card reinforces that AI is a reviewer, not the calculator. Deterministic flags come from stored parcel context and massing output; OpenRouter is only called when the analyst clicks `Run AI Reviewer`.

## Deferred

Viewport-wide parcel polygons and vector tiles are deferred. The current map identifies parcels by clicked point and loads only the selected parcel geometry so it remains responsive and avoids bulk polygon transfer.
