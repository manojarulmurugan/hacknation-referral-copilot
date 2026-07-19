# Stage 3 provenance audit — MVP findings

## Outcome

The deterministic audit processed all 10,077 distinct facility IDs and 451,110
evidence bullets from `capability`, `procedure`, and `equipment`. It did not
alter the raw dataset.

- 4,556 bullets were classified `suspected_conflict` and excluded from
  downstream scoring.
- 989 bullets were classified `review` and retained.
- 1,516 facilities contained at least one suspected conflict: **15.04%** of
  distinct facilities.
- 662 facilities contained at least one review flag: **6.57%**.

The 15.04% value is the rate produced by this conservative detector. It is not
an estimate of true contamination prevalence.

## What changed from pre-flight Tier A

The pre-flight exact-vocabulary scan flagged 85.65% of rows, or 65.94% after a
basic noise filter. Those rates were inflated by numeric cities, generic
organization names, place-name collisions, aliases, and neighborhoods.

The MVP replaces that single broad rule with several narrow checks:

1. Resolve city names and aliases through the offline GeoNames India dump.
2. Restrict the text vocabulary to places represented by facility addresses,
   avoiding GeoNames features whose names collide with ordinary words.
3. Prefer the longest place phrase, so `Navi Mumbai` suppresses nested
   `Mumbai`.
4. Require explicit location language and at least 75 km separation before
   automatically rejecting a geographic assertion.
5. Normalize facility names, remove legal and healthcare descriptors, and
   require a distinctive organization identity.
6. Reject foreign organizations only when sentence context attributes a claim
   to them. Credential, affiliation, directory, and branch references abstain.
7. Find long exact evidence blocks repeated across unrelated facilities and
   mark them for review unless another identity signal reinforces the conflict.

Every decision preserves the original bullet and records machine-readable
reason codes, matched places/organizations, and the exclusion decision.

## Development-sample check

Against the 25-row Claude semantic review produced during pre-flight:

- true positives: 5
- false positives: 0
- false negatives: 1
- true negatives: 19
- measured precision: 100%
- recall on the six rows labeled contaminated: 83.33%

This is a biased development sample drawn entirely from previously flagged
rows. It cannot estimate population precision, recall, or prevalence.

The sole nominal false negative is `Rela Hospital`. Its saved Claude rationale
attributes two CARE Hospital equipment bullets to Rela, but those bullets occur
in the saved NIMS input and current NIMS row, not the current Rela row. The
detector correctly flags the NIMS bullets. The label inconsistency is retained
rather than silently edited.

Regression tests also verify that the audit catches the confirmed Wadhwa,
Saravana, Upasana, NIMS, and Cosmos examples while not auto-rejecting the known
Navi Mumbai, personal-name, neighborhood, historical-alias, and generic
organization false-positive examples.

## Artifacts

Local, gitignored data products:

- `data/processed/evidence_bullets.parquet`
- `data/processed/bullet_provenance_flags.parquet`
- `data/processed/facility_provenance_summary.parquet`
- `data/reference/geonames/IN.zip`

Commit-safe artifacts:

- `pipeline/stage3_provenance_audit.py`
- `eval/test_stage3_provenance.py`
- `docs/provenance/stage3_metrics.json`
- `docs/provenance/stage3_findings.md`

Run:

```bash
python pipeline/stage3_provenance_audit.py
python -m unittest eval/test_stage3_provenance.py
```

GeoNames data is used under CC BY 4.0:
https://www.geonames.org/

## Interpretation and limits

`consistent_or_no_conflict` means that the audit found no high-confidence
identity contradiction. It does not prove a medical claim is true.

`suspected_conflict` means a deterministic rule found explicit, geographically
or organizationally inconsistent attribution. These bullets are excluded from
readiness scoring but remain available for the UI under “evidence we rejected.”

`review` means that a signal exists but automatic exclusion would be unsafe.
These bullets remain available.

The MVP deliberately does not perform fuzzy entity resolution, LLM review,
external medical fact-checking, source truth discovery, or raw-data correction.
The next core step is Stage 4 capability taxonomy and tracer mapping.
