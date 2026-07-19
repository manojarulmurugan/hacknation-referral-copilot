# Stage 5 readiness scoring — findings

## Status: COMPLETE (revised twice after external review)

Built directly on Stage 4's complete, deterministic `bullet_capability_map.parquet`
(60,666 match rows, 6,093 facilities with any match). Pure aggregation — no new
data collected, no model calls. **Two rounds of second-opinion review found real
correctness issues in this file, both documented below rather than silently
corrected, since they materially change the output:** an internal Codex review
(round 1) and a second, independent review (round 2, Cursor/GPT-5.6) that caught
issues the first round missed.

## Outcome (full facility × capability cross product)

**201,540 rows** = 10,077 facilities × 20 capabilities — every facility has a
row for every capability, including capabilities with zero Stage 4 matches
(Fix #1, round 1).

| Verdict | Count (full population) | % of all pairs | % of pairs with ≥1 match |
|---|---|---|---|
| `corroborated` | 4,321 | 2.14% | 20.26% |
| `claimed_only` | 16,960 | 8.42% | 79.53% |
| `insufficient_evidence` | 180,259 | 89.44% | 0.20% (43 rows) |

The 89.44% full-population `insufficient_evidence` figure looks alarming out
of context but is expected and correct: most (facility, capability) pairs
have **zero** evidence for that specific capability (a dental clinic has no
dialysis-related text — that's a true negative-for-evidence, not a data
quality problem). The right number to reason about product quality with is
the **"≥1 match" column** — of pairs where Stage 4 found *something*, 20.26%
are independently corroborated across two different source fields.

2,276 of the 2,656 pairs the two contradiction rules actually apply to
(**85.7%**, not the 49.8% an earlier version of this document reported — see
Fix #4 below) carry a contradiction flag. Manually spot-checked, reflects
genuinely sparse single-domain evidence per facility, not a scoring bug.

## Method

Per `(facility_id, capability_id)` pair — generated for all 10,077 × 20
combinations, not just matched ones (Fix #1):

1. **Domain score** = distinct tracer_ids matched (excluding `exclude_from_scoring=True` rows) ÷ tracer_ids the taxonomy defines for that domain.
2. **Readiness score** = mean of domain scores — WHO SARA's literal formula. Informational, not a gate.
3. **Verdict**:
   - `insufficient_evidence` if **zero tracers matched for this specific capability** (Fix #2b), **or** the facility's total evidence-bullet count (all capabilities) is < 5.
   - `corroborated` if ≥2 distinct tracers matched **from ≥2 distinct source fields** (Fix #4a, see below — tightened again from the round-1 "≥2 distinct bullets" rule).
   - `claimed_only` otherwise — **usable evidence exists but doesn't clear the corroboration bar.** This is *not* "exactly one tracer matched" (Fix #4c): a pair with several tracers matched from one bullet, or from bullets that all share one source field, lands here too.
4. **Contradiction flags** — the two brief-specified cases (`general_surgery` w/o `operation_theatre`, `icu` w/o `ventilator`).

### Fix #1 — full cross product, not just matched pairs (round 1, Codex)

**Original version only produced rows for pairs with ≥1 Stage 4 match** — a
facility with no dialysis-related text got *no dialysis row at all*, rather
than an explicit verdict. Caught in review: this contradicts the brief's own
language — *"this is the data-desert/medical-desert distinction implemented
**at row level**"* — which only holds if every pair has a row. Fixed by
scoring the full `facilities × capability taxonomy` cross product.

### Fix #2 — corroboration must be genuinely independent, round 1 (Codex)

- **2a.** A single bullet like *"ICU with ventilator support"* can match two
  tracers (`icu_service` + `ventilator`) by itself — one claim from one
  sentence, not two independent lines of evidence. Fixed (round 1):
  `corroborated` required `distinct_tracer_count >= 2 AND distinct_bullet_count
  >= 2`. (Superseded by Fix #4a below — bullet-count independence turned out
  not to be enough either.)
- **2b.** A pair whose only matched rows were *all* `exclude_from_scoring=True`
  was being counted as "matched" even with zero usable evidence. Fixed:
  verdict logic checks `distinct_tracer_count == 0` (post-exclusion) and
  routes it to `insufficient_evidence`, never `claimed_only`.

### Fix #4 — round 2 (Cursor/GPT-5.6 second-opinion review, all three verified against real data before fixing)

**4a. `distinct_bullet_count >= 2` still didn't mean independent
corroboration.** Two bullets can both be split from the *same* source field
(e.g. two sentences pulled out of the same `description` text) — that's
still one underlying claim about the facility, not two independent
statements. Measured directly against the round-1 output: **830 of 3,320
`corroborated` pairs (25.0%) drew every one of their supporting bullets from
a single `source_field`.** Fixed: `corroborated` now requires
`distinct_tracer_count >= 2 AND distinct_source_field_count >= 2` — this
subsumes the old bullet-count check (two different source fields are never
the same bullet) while being strictly stronger. **830 pairs downgraded to
`claimed_only`; 2,490 remain `corroborated`.**

**4b. The contradiction-flag percentage used the wrong denominator.** The
prior version of this document computed 2,276 flags ÷ 4,566 "applicable
rows" = 49.8% — but 4,566 wasn't even a real quantity in the current
(post-Fix-#1) code; the two rules' capabilities have 20,154 rows total in
the full cross product, the overwhelming majority of which have no claim
tracer matched at all and so were never eligible for the rule to fire on in
the first place. The correct denominator is pairs where a claim tracer
*actually matched* — verified directly: **2,656 applicable pairs, 2,276
flagged, 85.7%.** `pipeline/stage5_readiness_scoring.py`'s
`contradiction_applicable_pair_count()` now computes this correctly and
`stage5_metrics.json` reports both the flag count and the applicable-pair
denominator explicitly.

**4c. `claimed_only`'s documented definition was wrong.** This document
previously said `claimed_only` means "exactly one tracer matched." The code
never actually enforced that — 90 pairs land in `claimed_only` with 2+
distinct tracers (the exact pairs Fix #2a/#4a downgrade: multiple tracers
matched, but not from independent enough sources). Corrected definition:
*usable capability evidence exists, but it doesn't satisfy the corroboration
threshold* — regardless of how many tracers matched. A stale sentence in
`stage5_metrics.json`'s `interpretation` field claiming a zero-match pair
could become `claimed_only` "if the facility has enough unrelated evidence"
was also wrong (the code has always routed zero-tracer pairs straight to
`insufficient_evidence`, unconditionally) and has been corrected.

### Why the verdict rule differs from the brief's original method line (unchanged from the original analysis)

The brief proposed conditional pass = mean domain score ≥80% (IPHS's real
compliance threshold) with every domain non-zero. **Tested directly against
the real, complete dataset: only 30 of 14,352 matched pairs (0.2%) would
pass.** IPHS's 80% is calibrated for in-person inspector surveys where every
tracer item is actively checked; our evidence is passively scraped website
text, which rarely covers staff *and* equipment *and* procedures *and*
diagnostics for one capability in one facility's snippets — a
data-collection-method mismatch, not a data quality failure. Recalibrated
twice (see Fix #2a then Fix #4a above) to `≥2 distinct tracers from ≥2
distinct source fields` — closer in spirit to the brief's own example
language, *"corroborating evidence across three fields."*

## Artifacts

Gitignored: `data/processed/facility_capability_readiness.parquet`.
Committed: `pipeline/stage5_readiness_scoring.py`, `eval/test_stage5_readiness_scoring.py`, this file, `stage5_metrics.json`.

Run:
```bash
python pipeline/stage5_readiness_scoring.py
python -m unittest eval/test_stage5_readiness_scoring.py
```

## Interpretation and limits

- Readiness score is informational context, not the verdict driver.
- Completeness floor (5 bullets, facility-wide) is a single global threshold, not per-capability-calibrated — a documented simplification under a hard time-box. The zero-tracer-match check (Fix #2b) provides a *capability-specific* completeness signal at the simplest possible level (present vs. absent), which partially offsets this limitation without the effort of full per-capability calibration.
- Contradiction flags cover only the two brief-specified cases.
- No independent population-level validation sample for Stage 5 itself — pure aggregation math over Stage 4's already-validated evidence layer.
- Domain weighting is equal, per the brief's stated default.
- Both round-1 fixes came from an internal Codex second-opinion review; all three round-2 fixes came from a second, independent review (Cursor/GPT-5.6) of the round-1 output — and every one of round 2's specific numeric claims was independently re-derived from the real data before being accepted, not taken on faith. Worth noting as a process point: two full rounds of external review, on a file that had already passed its own unit tests and looked numerically plausible both times, still found real issues. Internal tests catching "does the code do what I meant" is not the same as an outside read catching "did I mean the right thing."
