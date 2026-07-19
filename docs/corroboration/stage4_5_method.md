# Stage 4.5 — bounded external corroboration

## Purpose

This optional pass asks whether a credible external source explicitly confirms a
specific facility-capability pair after Stage 4 has identified the capability.
It is not a crawler for all facilities and does not interpret missing web
evidence as medical incapability.

## Implemented workflow

1. Read Stage 4 facility-capability matches from CSV or Parquet.
2. Rank pairs by distinct readiness domains, tracer items, and matched bullets.
3. Select at most 10 candidates per capability and 100 total by default.
4. Reuse existing `officialWebsite`, `websites`, and `source_urls`.
5. Search Tavily only for bounded selected pairs during a live run.
6. Rank official facility, government, NABH/PM-JAY, and institutional sources
   above directories and social media.
7. Ask OpenAI for a structured decision containing an exact quote and URL.
8. Reject any model quote that is not present verbatim in the retrieved text.
9. Cache successful live results locally so the same pair is not billed twice.

`not_found`, API failure, and unsupported model output all become `unverified`.
They never become evidence that a facility lacks a capability.

## Dry-run verification

The current Stage 4 sample contains 390 distinct facility-capability pairs. The
bounded selector prepared 100 pairs: at most 10 for each of the ten tracer
capabilities. It made zero external calls and wrote:

- `data/processed/external_corroboration_dry_run.parquet`
- `docs/corroboration/stage4_5_summary_dry_run.json`

Live execution is intentionally gated on `TAVILY_API_KEY` and `OPENAI_API_KEY`.
The current local `.env` did not provide those hackathon credentials during
implementation, so no shared credits were consumed.

## Commands

```bash
# Verify selection, joins, queries, and output schema without API calls.
python pipeline/stage4_5_external_corroboration.py --dry-run

# Capped live pass after adding hackathon credentials.
python pipeline/stage4_5_external_corroboration.py
```

The live defaults can be reduced further:

```bash
python pipeline/stage4_5_external_corroboration.py \
  --per-capability 5 \
  --total-cap 30
```

## Integration rule

External corroboration remains a separate signal in Stage 5:

- official quote found: may strengthen `corroborated`
- weak directory only: supports `claimed`, not independent corroboration
- unverified/not found: no negative inference
- identity mismatch: exclude that external source

The evidence card must show the exact external quote, source URL, and source
tier separately from the original dataset bullet.
