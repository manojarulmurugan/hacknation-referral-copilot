# hacknation-referral-copilot

Hackathon scaffold for a referral scoring / copilot pipeline on Databricks.

## Layout

| Path | Owner | Purpose |
|------|--------|---------|
| `/app` | Teammate | Databricks App (Streamlit/Dash) UI |
| `/pipeline` | You | Ingestion, scoring, validator |
| `/notebooks` | Shared | Exploration & one-off Databricks notebooks |
| `/data` | Shared | **Small samples only** — never the full 10k raw dump |
| `/schemas` | Shared | Data contract: Delta DDL, Pydantic models |
| `/eval` | You | Hand-labeled calibration set |

## Coordination rule

Pushing to `main` is fine for the hackathon.

**Exception:** before changing anything under `/schemas` (the data contract), post a message in the team chat channel. That file is the only real coordination risk.

## Quick start

1. Put tiny sample files under `data/samples/` (gitignored bulk under `data/` otherwise).
2. Define / update the contract in `schemas/` — **chat first**.
3. Pipeline work lands in `pipeline/`; UI in `app/`.
4. Calibration labels go in `eval/`.
