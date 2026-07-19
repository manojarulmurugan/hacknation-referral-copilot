# Referral Copilot

Referral Copilot is an evidence-attached facility search app for care coordinators working with an incomplete, noisy dataset of about 10,000 Indian healthcare facilities. A user describes a care need and city in plain language; the app returns a ranked shortlist, the exact row-level text supporting each result, explicit evidence gaps, and data-quality warnings.

Live app: https://data-legend-app-7474656737321234.aws.databricksapps.com

## User workflow

1. Enter a name. It is used only to separate saved referrals.
2. Search for a care need and place, such as `dialysis near Jaipur`.
3. Review the Best match ranking, or switch to the pure Nearest or Most evidence views.
4. Expand “How this was scored” and inspect every cited facility-text bullet.
5. Treat “claimed only,” missing-domain, facility-type, and coordinate warnings as reasons to verify before referral.
6. Save a result to My shortlist. Saved items remain after page reloads, app restarts, and redeployments.

The app is decision support, not clinical advice or a guarantee that a service is currently available.

## Architecture

- `pipeline/stage3_provenance_audit.py` explodes source text into traceable evidence bullets and audits contamination.
- `pipeline/stage4_taxonomy_mapping.py` maps literal evidence to a locked 20-capability taxonomy and domain-specific tracer items.
- `pipeline/stage5_readiness_scoring.py` produces facility-capability verdicts, SARA-style domain coverage, evidence counts, and contradiction signals.
- `app/query.py` resolves deterministic care-need and city aliases first. `app/llm_query.py` uses the attached Databricks Model Serving endpoint only for query interpretation when deterministic parsing is insufficient.
- `app/ranking.py` combines evidence strength and geographic proximity while visibly discounting coordinate mismatches and implausible facility types.
- `app/cards.py` and `app/app.py` render ranked, row-cited evidence cards and honest empty/fallback states.
- `app/persistence.py` stores typed-name shortlists behind a swappable repository interface.

All facility claims shown as evidence come from the provided dataset. The LLM does not enrich, correct, or invent facility facts. Out-of-taxonomy searches use transparent literal matching against the same row-level evidence.

## Ranking

Best match is the default:

`composite = (0.60 × evidence_component + 0.40 × proximity_component) × confidence penalties`

The evidence component is derived from the verdict base and normalized readiness score. Proximity follows a gravity-decay curve, `1 / (1 + distance_km / 25)`. A known address/coordinate mismatch discounts proximity, and records typed as dentist/pharmacy receive a strong plausibility penalty for unrelated advanced-care capabilities. Evidence-backed results remain separated from zero-evidence proximity fillers.

Nearest and Most evidence preserve their pure-axis ordering and are available for comparison.

## Evidence framework and citations

Readiness is informational, not a certification. It groups capability-specific tracer items across staffing, equipment, diagnostics, and procedures using:

- World Health Organization, *Service Availability and Readiness Assessment (SARA) Reference Manual*: https://www.who.int/publications/i/item/WHO-HIS-HSI-2014.5-Rev.1
- Government of India, *Indian Public Health Standards (IPHS) 2022*: https://iphs.mohfw.gov.in/
- IPHS 2022 Community Health Centre guidelines: https://nhsrcindia.org/sites/default/files/CHC%20IPHS%202022%20Guidelines%20pdf.pdf

## Persistence

The deployed app uses a dedicated Lakebase Autoscaling project (`referral-copilot`) and the app-owned `referral_copilot.shortlist_item` Postgres table. Connections use the Databricks App service principal, unified OAuth, and short-lived generated database credentials; no database password or personal token is bundled.

Local development defaults to `data/referral_shortlist.sqlite`. Override with:

```bash
export REFERRAL_PERSISTENCE=sqlite
export REFERRAL_SHORTLIST_DB=/path/to/referral_shortlist.sqlite
```

SQLite is a development fallback only. Container-local SQLite would not be durable across a Databricks App restart or redeploy.

## Run locally

Use Python 3.9+ and place the required runtime artifacts under `data/`:

- `facilities_local.parquet`
- `processed/facility_capability_readiness.parquet`
- `processed/bullet_capability_map.parquet`
- `processed/evidence_bullets.parquet`

Then:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. python -m app.app
```

Open http://127.0.0.1:8050.

Run all tests:

```bash
PYTHONPATH=. python -m unittest discover -s eval -p "test_*.py"
```

## Deploy

The deployment builder packages only the app closure, taxonomy, configuration, and required runtime parquets. Large parquets are partitioned below the Databricks Workspace per-file export limit without changing their rows or schemas.

```bash
PYTHONPATH=. python deploy/build_bundle.py
databricks sync --full --include "**" build/databricks_app <workspace-source-path>
databricks apps deploy data-legend-app --source-code-path <workspace-source-path>
```

The Databricks App must retain two resources:

- Lakebase `postgres` with `CAN_CONNECT_AND_CREATE`
- `databricks-meta-llama-3-3-70b-instruct` as `serving-endpoint` with `CAN_QUERY`

Databricks Free Edition Apps can stop after the platform’s 24-hour runtime window. Before a demo, confirm `data-legend-app` is RUNNING and redeploy the latest bundle if the URL is unavailable. Lakebase shortlist data remains durable when app compute stops.
