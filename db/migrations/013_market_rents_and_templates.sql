-- Market rent provenance and reusable scenario templates.
-- Additive only: existing parcel_scenarios, rent_limits, and utility_allowances remain unchanged.

CREATE TABLE IF NOT EXISTS lla.market_rent_sources (
    market_rent_source_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    parcel_id UUID REFERENCES lla.parcels(parcel_id) ON DELETE CASCADE,
    county_fips CHAR(5),
    source_type TEXT NOT NULL
        CHECK (source_type IN ('costar', 'broker', 'internal', 'manual', 'other')),
    report_name TEXT,
    report_date DATE,
    submarket TEXT,
    bedroom_count INTEGER CHECK (bedroom_count >= 0 AND bedroom_count <= 8),
    market_rent_monthly NUMERIC(12, 2) NOT NULL CHECK (market_rent_monthly >= 0),
    rent_psf NUMERIC(12, 2) CHECK (rent_psf IS NULL OR rent_psf >= 0),
    vacancy_rate NUMERIC(6, 5) CHECK (vacancy_rate IS NULL OR (vacancy_rate >= 0 AND vacancy_rate <= 1)),
    concessions_notes TEXT,
    confidence TEXT CHECK (confidence IS NULL OR confidence IN ('low', 'medium', 'high')),
    notes TEXT,
    source_file_ref TEXT,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (parcel_id IS NOT NULL OR county_fips IS NOT NULL OR submarket IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_lla_market_rent_sources_parcel_latest
    ON lla.market_rent_sources (parcel_id, bedroom_count, report_date DESC NULLS LAST, updated_at DESC)
    WHERE parcel_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_lla_market_rent_sources_area_latest
    ON lla.market_rent_sources (county_fips, submarket, bedroom_count, report_date DESC NULLS LAST, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_lla_market_rent_sources_raw
    ON lla.market_rent_sources USING GIN (raw);

ALTER TABLE lla.market_rent_sources ENABLE ROW LEVEL SECURITY;

CREATE TABLE IF NOT EXISTS lla.scenario_templates (
    template_name TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    description TEXT NOT NULL,
    assumptions_jsonb JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE lla.scenario_templates ENABLE ROW LEVEL SECURITY;

INSERT INTO lla.scenario_templates (template_name, label, description, assumptions_jsonb, updated_at)
VALUES
    (
        'conservative',
        'Conservative',
        'Higher costs, higher vacancy and yield, lower market exposure.',
        '{
            "hard_cost_basis": "hard_cost_per_gross_sf",
            "soft_cost_pct": 0.24,
            "contingency_pct": 0.08,
            "financing_carry_pct": 0.10,
            "vacancy_rate": 0.07,
            "opex_rate": 0.38,
            "required_yield_on_cost": 0.0725,
            "affordable_share": 0.45,
            "market_share": 0.55,
            "utilities_included": false,
            "use_utility_allowance": true,
            "include_tax_exemption": true,
            "use_latest_market_rent_source": true,
            "rent_year": 2026,
            "tax_year": 2026
        }'::jsonb,
        now()
    ),
    (
        'base_case',
        'Base Case',
        'Default underwriting case aligned with the feasibility calculator defaults.',
        '{
            "hard_cost_basis": "hard_cost_per_gross_sf",
            "soft_cost_pct": 0.20,
            "contingency_pct": 0.05,
            "financing_carry_pct": 0.08,
            "vacancy_rate": 0.05,
            "opex_rate": 0.35,
            "required_yield_on_cost": 0.065,
            "affordable_share": 0.40,
            "market_share": 0.60,
            "utilities_included": false,
            "use_utility_allowance": true,
            "include_tax_exemption": true,
            "use_latest_market_rent_source": true,
            "rent_year": 2026,
            "tax_year": 2026
        }'::jsonb,
        now()
    ),
    (
        'aggressive',
        'Aggressive',
        'Lower cost load and yield with higher market-rate share.',
        '{
            "hard_cost_basis": "hard_cost_per_gross_sf",
            "soft_cost_pct": 0.18,
            "contingency_pct": 0.04,
            "financing_carry_pct": 0.07,
            "vacancy_rate": 0.04,
            "opex_rate": 0.32,
            "required_yield_on_cost": 0.06,
            "affordable_share": 0.40,
            "market_share": 0.60,
            "utilities_included": false,
            "use_utility_allowance": true,
            "include_tax_exemption": true,
            "use_latest_market_rent_source": true,
            "rent_year": 2026,
            "tax_year": 2026
        }'::jsonb,
        now()
    ),
    (
        'internal_cost_advantage',
        'Internal Cost Advantage',
        'Base rent and vacancy case with reduced hard/soft cost load for in-house delivery.',
        '{
            "hard_cost_basis": "hard_cost_per_gross_sf",
            "hard_cost_discount_pct": 0.06,
            "soft_cost_pct": 0.18,
            "contingency_pct": 0.04,
            "financing_carry_pct": 0.075,
            "vacancy_rate": 0.05,
            "opex_rate": 0.34,
            "required_yield_on_cost": 0.0625,
            "affordable_share": 0.40,
            "market_share": 0.60,
            "utilities_included": false,
            "use_utility_allowance": true,
            "include_tax_exemption": true,
            "use_latest_market_rent_source": true,
            "rent_year": 2026,
            "tax_year": 2026
        }'::jsonb,
        now()
    ),
    (
        'tax_exemption_case',
        'Tax Exemption Case',
        'Base case that includes the stored Live Local tax exemption estimate.',
        '{
            "hard_cost_basis": "hard_cost_per_gross_sf",
            "soft_cost_pct": 0.20,
            "contingency_pct": 0.05,
            "financing_carry_pct": 0.08,
            "vacancy_rate": 0.05,
            "opex_rate": 0.35,
            "required_yield_on_cost": 0.065,
            "affordable_share": 0.40,
            "market_share": 0.60,
            "utilities_included": false,
            "use_utility_allowance": true,
            "include_tax_exemption": true,
            "use_latest_market_rent_source": true,
            "rent_year": 2026,
            "tax_year": 2026
        }'::jsonb,
        now()
    ),
    (
        'no_tax_benefit_case',
        'No Tax Benefit Case',
        'Base operating case with property tax savings excluded.',
        '{
            "hard_cost_basis": "hard_cost_per_gross_sf",
            "soft_cost_pct": 0.20,
            "contingency_pct": 0.05,
            "financing_carry_pct": 0.08,
            "vacancy_rate": 0.05,
            "opex_rate": 0.35,
            "required_yield_on_cost": 0.065,
            "affordable_share": 0.40,
            "market_share": 0.60,
            "utilities_included": false,
            "use_utility_allowance": true,
            "include_tax_exemption": false,
            "use_latest_market_rent_source": true,
            "rent_year": 2026,
            "tax_year": 2026
        }'::jsonb,
        now()
    )
ON CONFLICT (template_name) DO UPDATE
SET
    label = EXCLUDED.label,
    description = EXCLUDED.description,
    assumptions_jsonb = EXCLUDED.assumptions_jsonb,
    is_active = true,
    updated_at = now();
