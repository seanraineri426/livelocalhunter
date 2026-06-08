# Live Local Hunter API

The FastAPI app lives at `src/lla/api/app.py` and is intentionally thin. It wraps the existing Python modules for parcel context, deterministic feasibility, rent and utility lookups, tax exemption, cost audit, and parcel chat.

## Run Locally

```bash
python scripts/run_api.py
```

Equivalent uvicorn command:

```bash
PYTHONPATH=src uvicorn lla.api.app:app --reload --host 127.0.0.1 --port 8000
```

The app reads `DATABASE_URL`, `OPENROUTER_API_KEY`, and optional `OPENROUTER_MODEL` from `.env.local` / `.env`. OpenRouter keys stay server-side; the frontend only receives API responses.

## Endpoints

- `GET /health`
- `GET /parcels/search?folio=&county=`
- `GET /parcels/{parcel_id}/context`
- `GET /scenario-templates`
- `POST /parcels/{parcel_id}/feasibility`
- `POST /parcels/{parcel_id}/cost-audit`
- `POST /parcels/{parcel_id}/chat`
- `GET /parcels/{parcel_id}/scenarios`
- `POST /parcels/{parcel_id}/scenarios`
- `PATCH /parcels/{parcel_id}/status`
- `POST /parcels/{parcel_id}/notes`

## Assumption Flow

`POST /parcels/{parcel_id}/feasibility` accepts:

```json
{
  "template_name": "base_case",
  "assumptions": {
    "hard_cost_per_gross_sf": 240,
    "gross_sf": 120000,
    "acquisition_price": 5000000,
    "market_monthly_rent": 3000,
    "assessed_value": 10000000
  }
}
```

The API applies the template first, then explicit assumptions. It calls `build_parcel_context`, rent and utility lookups, `estimate_exemption`, and `calculate_feasibility`. If a parcel-specific row exists in `lla.market_rent_sources` and `market_monthly_rent` is omitted, the latest source can prefill market rent.

`POST /parcels/{parcel_id}/scenarios` runs the same calculation and saves a row to `lla.parcel_scenarios`.
