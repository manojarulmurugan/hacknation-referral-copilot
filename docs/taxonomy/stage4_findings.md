# Stage 4 capability taxonomy — deterministic mapping findings

## Status: COMPLETE

Stage 4 now uses a complete, local deterministic mapper. It scanned all
451,110 evidence bullets as 285,967 exact distinct strings and produced 60,666
bullet/tracer match rows. The map covers 55,673 bullet IDs and 6,093 facilities.
No model or external API is used.

Taxonomy version 2 expands the original 10 high-acuity capabilities to 20.
The additional set was chosen after a full-corpus prevalence scan: pediatric
intensive care, stroke care, neurosurgery, orthopaedic surgery, respiratory
care, gastroenterology/endoscopy, urology, ophthalmology, diagnostic imaging,
and mental health. Human-facing `display_names` keep internal identifiers out
of the app.

The earlier count of 285,966 distinct texts came from `pandas.Series.nunique()`.
Exact Python equality and `drop_duplicates()` identify 285,967; the former
under-counts this corpus by one due to a hash collision. Coverage assertions use
exact deduplication.

## Active method

`taxonomy/capability_taxonomy.yaml` defines the locked 20 capabilities, four
SARA domains, and executable include/exclude/context rules for every tracer.
`pipeline/stage4_taxonomy_mapping.py`:

1. validates capability, domain, tracer, and regex definitions;
2. applies case-insensitive, word-bounded rules to each distinct text once;
3. blocks explicit negation, external/referral phrasing, and known ambiguity
   traps;
4. broadcasts each text-level match to every corresponding bullet ID;
5. stores the exact regex match as `supporting_quote` and the rule as
   `matched_pattern`;
6. joins Stage 3 provenance status one-to-one; and
7. retains rejected evidence with `exclude_from_scoring=true`.

Unmatched bullets correctly produce zero map rows; complete coverage means every
distinct input string was evaluated, not that every bullet was forced into a
capability.

`normalization_vocab.json` is deliberately separate. It is Stage 8 prompt and
normalization context, not an input to the Stage 4 matcher.

## Validation and one tuning pass

The generated seeded, tracer-stratified sample contains 358 rows (up to three
per tracer). It is stored in `stage4_validation_sample.csv` with empty
`review_label` and `review_notes` columns for human review.

The single permitted tuning pass fixed five concrete sample errors:

- retrospective “underwent angiography at …” text no longer asserts the current
  facility's capability;
- implantable cardioverter-defibrillators no longer count as a facility
  defibrillator;
- “non-invasive ventilation” no longer matches invasive ventilation;
- PET brain imaging without CT no longer counts as PET-CT equipment; and
- training centres for blood-bank officers no longer count as on-staff
  transfusion specialists.

Regression tests also cover specialty surgeons, dental trauma, oncology LDR,
intensive-care ambulances, bare `OT`, department-vs-staff evidence, negation,
verbatim quotes, exact Stage 3 flag passthrough, duplicate output rows, and
specific positive fixtures for all ten added capabilities.

The expansion's bounded tuning pass removed generic spine surgery from
neurosurgery and required stroke context for mechanical thrombectomy, preventing
orthopaedic spine procedures and DVT thrombectomy from being mislabeled.

This bounded sample is precision-oriented error inspection. It does not support
a population-recall claim. The warning diagnostics identify five capability
maps dominated by their service/unit tracer; those counts are plausible but
remain explicitly visible for later review.

## Full-run results

- 451,110 input bullets
- 285,967 exact distinct texts scanned (100% coverage)
- 33,254 distinct texts with at least one match
- 60,666 output match rows
- 55,673 matched bullet IDs
- 6,093 facilities with at least one match
- 196 matched rows retained but excluded by Stage 3 provenance

Per-capability, per-domain, and per-tracer counts and dominance warnings are in
`stage4_metrics.json`.

## Archived LLM experiment

The full-corpus LLM approach is rejected as the production method and preserved
under `pipeline/experimental/` with its archived tests. At batch size 30, the
285,966-count planning estimate implied approximately 9,533 calls. The last
scoped run attempted 662 batches over 19,842 texts: 217 successful calls and
445 batch errors. Its partial map covered only 528 of 10,077 facilities.

Although the experiment validated useful controls—structured output,
quote-grounding, caching, and retry/backoff—it also cached failed batches as
empty results. Those failures cannot be interpreted as “no clinical evidence.”
The gitignored cache is preserved only for audit history and must not be
resumed. The old partial Stage 4 map and all Stage 5 metrics derived from it are
superseded.

## Evidence basis

The tracer catalogue remains grounded in India's IPHS 2022 district-hospital
service requirements. The aggregation method planned for Stage 5 uses WHO
SARA-style tracer/domain readiness; this project does not claim to reproduce
WHO SARA's primary-care service catalogue verbatim.

Primary references:

- WHO SARA Reference Manual, Chapter 3:
  https://cdn.who.int/media/docs/default-source/service-availability-and-readinessassessment(sara)/sara_reference_manual_chapter3.pdf
- IPHS 2022, Volume I (SDH/DH): https://nhsrcindia.org/IPHS2022
- Guidelines on HDU/ICU:
  https://nhsrcindia.org/sites/default/files/Guidelines-on-HDU_ICU.pdf
- Guidelines on OT:
  https://nhsrcindia.org/sites/default/files/Guidelines-on-OT.pdf
- Emergency Care Services at District Hospitals:
  https://nhsrcindia.org/sites/default/files/OT_emergency%20services%20at%20DH_inside_dt%2003%20march%202023_revised.pdf

## Run

```bash
python pipeline/stage4_taxonomy_mapping.py
python -m unittest eval/test_stage4_taxonomy_mapping.py
```

Generated data artifacts under `data/processed/` remain gitignored. The metrics,
validation sample, taxonomy, pipeline, and tests are committed artifacts.
