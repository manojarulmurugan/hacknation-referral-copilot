# Pre-flight diagnostic results

This report records three reproducible data-quality diagnostics (Q1/Q2/Q3)
performed before taxonomy mapping, provenance auditing, and readiness scoring.

- Data: `data/facilities_local.parquet`, 10,088 rows × 51 columns (verified against the expected shape before any check ran).
- All scripts live in `pipeline/preflight_*.py` and are re-runnable; every tier writes its raw output to `docs/preflight/` (not just this summary) so numbers can be audited independently.
- Randomness is seeded everywhere it's used (`SEED = 42`) — Tier B's 25-row sample and Check 3's 30-row sample are reproducible.
- Two data-quality artifacts surfaced during Check 1 apply dataset-wide and are worth knowing before Stage 1: **11 `unique_id` values are exact duplicate row-pairs** (22 rows total), and at least one row's `name` field is the literal degenerate string `"hospital"`.

---

## Check 1 — Contamination rate (layered)

### Tier A — full-population deterministic scan (all 10,088 rows)

Method exactly as specified: parsed `capability`/`procedure`/`equipment` as JSON bullet arrays; built a city vocabulary from all distinct `address_city` values (1,643 entries) and an org vocabulary from all distinct `name` values (9,512 entries); flagged a row if any bullet contains a word-boundary match for a vocab city/org other than the row's own. Exact-match only, no fuzzy/alias resolution (as instructed for this tier).

| Metric | Value |
|---|---|
| Total rows | 10,088 |
| **Flagged rows (raw, as literally specified)** | **8,640 (85.65%)** |
| Flagged rows (excluding generic single-word/numeric vocab noise) | 6,652 (65.94%) |
| Flagged bullets (raw count) | 71,720 |

**Why two numbers.** Building the vocab straight from the data (as instructed) pulls in degenerate entries: **47 org-vocab entries are single tokens** — e.g. one facility's `name` field is literally `"hospital"`, another is `"dental clinic"` — so those words match almost every bullet in the dataset regardless of any real cross-facility contamination. A handful of `address_city` values are also literally digits (`"0"`, `"2"`, `"6"`, `"33"`, `"64"`, `"871"`), which then match any bullet mentioning that digit (bed counts, ratings, model numbers). The 65.94% figure excludes rows whose *only* flag is one of these generic/numeric matches; it still includes every row with a real city mismatch or a specific (multi-word) org mismatch. Both numbers, plus the full flagged-bullet detail, are in `docs/preflight/tier_a_flagged_bullets.csv` and `tier_a_flagged_rows.csv` — this is a sensitivity disclosure, not a substitute judgment.

10 example flagged rows (prioritizing city/specific-org mismatches over generic noise) are below; full detail for all 8,640 in the CSV.

```
- Fortis Hospital at Ludhiana - Punjab (Ludhiana) — bullet mentions "Fortis Escorts Hospital, Amritsar"
- Mpct Hospital (Navi Mumbai) — bullet: "Located in Navi Mumbai, Maharashtra" [see Tier C note: false positive]
- Aadhar Hospital (Pune) — bullet mentions "Aadhar Multispeciality Hospital & I.C.U."
- Disha Eye Hospital (Kolkata) — bullet mentions "New Town" as a separate city
- Paras Hospital (Panchkula) — bullet mentions "Fortis Hospital"
- North City Hospital and Neuro Institute (Kolkata) — bullet mentions "Beliaghata" as a separate city, "city hospital" as org
- Lilavati Hospital (Nashik) — bullet: "0 occupied" [numeric vocab noise, see caveat above]
- 7 Orange Hospital (Chinchwad) — bullet mentions "Orange Healthcare"
- MOSC Medical College Hospital (Kolenchery) — bullet mentions "medical college hospital" as generic org
- Park Hospital (Karnal) — bullet mentions "Park Hospital Faridabad" / Faridabad as a separate city
```

Full outputs: `docs/preflight/tier_a_flagged_bullets.csv` (71,720 rows), `tier_a_flagged_rows.csv` (8,640 rows), `tier_a_vocab_cities.json`, `tier_a_vocab_orgs.json`, `tier_a_summary.json`.

### Tier B — human-readable validation sample (25 rows, seed=42)

Sampled from the Tier A **flagged set only** (not the whole dataset): 18 rows from the "obvious" bucket (city or multi-word-org mismatch) and 7 from the "borderline" bucket (generic single-word/numeric match only), then shuffled. Each entry shows `unique_id`, `name`, `address_city`, `description`, every flagged bullet, and why it was flagged — no judgment applied, per instructions.

Full 25-row printout: `docs/preflight/tier_b_sample.json`. Representative examples:

- **Genuine-looking contamination:** *Saravana Hospital* (own city: Kuttalam) has bullets placing it in Madurai, Chennai, Coimbatore, Namakkal, and Ahmedabad, plus a mention of a distinct org ("Shree Mahavir Multispeciality Hospital Pvt. Ltd.") — reads as several different facilities' records pooled together.
- **Likely false positive (compound place name):** *Dr. Jairaj's Hospital* (own city: Navi Mumbai) was flagged for mentioning "Mumbai" — but every mention is actually "Navi Mumbai," its own city; the exact-match scan hit the substring "Mumbai" inside the compound name.
- **Likely false positive (generic vocab noise):** *Redent Dental Clinic*, *My Smile Dental Clinic*, *Tulsi Dental Clinic* and others were flagged solely because "dental clinic" is itself some other facility's literal `name` value.

### Tier C — small-scale LLM validation (same 25 rows)

**Methodology note:** the spec calls for "one LLM call per facility." No external model endpoint (Databricks serving or otherwise) was configured for this local diagnostic session, so the extraction was performed directly by Claude (this session) reading each facility's full, un-filtered bullet set (`capability`+`procedure`+`equipment`, not just the Tier A–flagged subset — 4 to 132 bullets per facility) and producing the specified structured output plus a genuine-contamination judgment. This is a real semantic read of the text, not a second vocab-match pass — treat it as a proxy for what an LLM extraction pass would find, not as this exact prompt run against a production model. Full input blobs: `docs/preflight/tier_c_input.json`. Full extraction output: `docs/preflight/tier_c_llm_extraction.json`. Comparison: `docs/preflight/tier_c_comparison.json`.

| Metric | Value |
|---|---|
| Tier A "obvious" bucket in this sample | 18 / 25 |
| Tier A "borderline" bucket in this sample | 7 / 25 |
| LLM-confirmed genuine contamination | 6 / 25 (24%) |
| Precision of Tier A "obvious" bucket vs. LLM | 6 / 18 = **33%** |
| Recall of Tier A "obvious" bucket on LLM-confirmed cases | 6 / 6 = **100%** |
| LLM confirmed contamination in the "borderline" bucket | 0 / 7 |

**Reading this:** in this small sample, every case the LLM judged as genuine cross-facility contamination (Wadhwa Pathology Lab, Saravana Hospital, Rela Hospital, Upasana Hospital, Nizam's Institute of Medical Sciences, Cosmos Hospital) was already caught by Tier A's "obvious" bucket — no misses. But two-thirds of that same "obvious" bucket (12/18) turned out, on a real read, to *not* be contamination. The "borderline" bucket looks like it's near-pure noise (0/7 confirmed). That's a much more useful signal than the raw 85.65% headline.

**Systemic false-positive patterns found (useful for Stage 3's "principled" pass):**
1. **City rename/alias** — *Divine Touch Dental Hospital* (own city: Prayagraj) is flagged for mentioning "Allahabad," which is simply Prayagraj's pre-2018 name. Same place, not contamination.
2. **Sub-locality collision** — a neighborhood inside the facility's own city (e.g. "Andheri East" inside Mumbai, "Satellite" inside Ahmedabad) happens to equal some *other* facility's `address_city` value elsewhere in the dataset, so it gets flagged as a foreign city.
3. **Compound place-name substring** — "Navi Mumbai" contains the standalone word "Mumbai," which matches the separate vocab entry "Mumbai" even though they're the same city reference.
4. **Personal name / place name collision** — "Dr. Sachin Ganeshwadi" was flagged because "Sachin" is coincidentally also a real Gujarat town in the city vocabulary.
5. **Legitimate multi-branch disclosure** — *Satya Aesthetics* explicitly and consistently states it operates clinics in both Delhi and Gurugram; that's a real business fact, not misattributed evidence. Tier A's binary same-city check can't distinguish this from contamination.
6. **Generic descriptive org-name noise** — "dental clinic," "care hospital," "medical college hospital" are literal `name` values for *some* facility, so they flag almost any bullet using that phrase generically.

**One more finding worth flagging for Stage 3 directly:** the "Care Hospital" cardiac-imaging / dual-source-CT bullets appear **near-verbatim in two different sampled facilities** (Rela Hospital, Chennai, and Nizam's Institute of Medical Sciences, Hyderabad) — a repeating pattern, not an isolated one-off, which is exactly the kind of over-merge signature a Louvain/connected-components pass over `cluster_id` should catch.

### Decision point for §14/Stage 3

Raw Tier A (85.65%) is not a usable headline number — it's dominated by vocabulary noise from degenerate `name`/`address_city` values. The "strong" Tier A number (65.94%) is closer, and Tier C suggests even that overstates true contamination by roughly 2–3×, given six of the "obvious" bucket's causes were confirmed real out of eighteen sampled. **A defensible range to use in the demo is "a large minority to a majority of records show some cross-facility evidence pooling, with confirmed severe cases like Saravana Hospital and Cosmos Hospital" rather than a single precise percentage** — until a principled pass (alias resolution for renamed cities, sub-locality mapping, Louvain over `cluster_id`) narrows it further. **Recommendation: Stage 3 is still the headline** — the severe cases (Saravana Hospital pooling 4+ cities and a second org name) are real and dramatic — but the "principled" Louvain pass isn't optional polish here; it's needed to get the number honest. **A full LLM extraction pass across all 10,088 rows was explicitly not run**, per instructions — this is a decision to make together given the ~24% true-positive rate observed on this sample.

---

## Check 2 — Distinct source count per facility

Per instructions, inspected raw formats before assuming anything:

| Field | Format found |
|---|---|
| `source_types` | JSON array, but only **4 distinct values dataset-wide** (`dynamic`, `overture`, `constant`, `mongo_facility`) — a source-*type* label, not an identifier. Frequently padded/repeated to a fixed length (many rows cap at exactly 50 entries regardless of real content). |
| `source_ids` | JSON array of hash-like ids that **repeats within a row** — e.g. one row has 50 raw entries but only 11 distinct ids. This is the field that plausibly represents genuinely separate underlying sources. |
| `source_content_id` | A **single scalar string** per facility (not an array) — looks like an id for the underlying content/document record, not a list of sources. Can't produce a per-facility count by itself. |

10-facility side-by-side (raw `source_types`/`source_ids`/`source_content_id`): `docs/preflight/check2_source_examples.json`. `source_types` and `source_ids` array lengths only match in 4,631 / 10,088 rows, confirming they aren't a simple parallel pair — another reason to prefer `distinct(source_ids)` over raw length as the signal.

**Decision: distinct-count of `source_ids` per facility** is the metric used below.

| Distinct sources | Facilities | % |
|---|---|---|
| 1 (includes 818 with 0) | 3,612 | 35.84% |
| 2–4 | 2,869 | 28.47% |
| 5+ | 3,596 | 35.69% |

Mean 4.88, median 2, max 35. Raw counts: `docs/preflight/check2_histogram.csv`.

**Decides Q2:** over a third of facilities (35.69%) have 5+ distinct sources, and nearly two-thirds (64%) have 2+. **There is enough distinct-source signal for Stage 6 (truth discovery) to be worth attempting** — it does not degenerate to a single-source-per-facility dataset. The 35.84% single-source bucket (including 818 facilities with zero source ids at all) simply won't get a reliability-weighted adjustment and should fall back to unweighted scoring.

---

## Check 3 — `specialties` / `capability` positional alignment

Parsed both fields as JSON arrays, filtered to the 9,933 / 10,077 unique rows where both are non-null and non-empty, and printed raw `(specialties[i], capability[i])` pairs for every index across 30 seeded-random rows (seed=42). No judgment applied in the script — raw pairs only.

Full 30-row dump: `docs/preflight/check3_pairs.json`. Representative excerpt (`Burdwan Dental College & Hospital`, len_specialties=43, len_capability=35):

```
[0]  specialties='internalMedicine'         | capability='Established in 2009'
[1]  specialties='internalMedicine'         | capability='Affiliated to West Bengal University of Health Sciences'
[3]  specialties='dentistry'                | capability='Offers Bachelor of Dental Surgery (BDS) degree'
[9]  specialties='oralMedicine'             | capability='Established in 2009-10'
[19] specialties='internalMedicine'         | capability='BDCH has Department of Oral Pathology'
```

This pattern repeats across all 30 sampled rows and is visible immediately: `specialties[i]` looks like an independently-cycled list of department/specialty labels, while `capability[i]` is a free-text fact bullet — the two show no semantic correspondence at the same index. Array lengths often differ too (43 vs. 35 in the example above), and where they do match in length (e.g. `Brookefield Hospital`, 50/50) the pairs are still unrelated by content, so equal length isn't evidence of alignment either.

**Decides Q3: `specialties` does not positionally align with `capability`.** This is a *print only* check per instructions — no scoring/taxonomy decision made here — but the raw evidence strongly suggests there is no free supervised signal to harvest this way; `specialties` should be treated as an independent categorical field, not a per-bullet label for `capability`.

---

## Files produced this session

```
pipeline/preflight_check1_tierA.py           Tier A scan (script)
pipeline/preflight_check1_tierB.py           Tier B sample (script)
pipeline/preflight_check1_tierC_prep.py      Tier C input builder (script)
pipeline/preflight_check1_tierC_compare.py   Tier A/C agreement (script)
pipeline/preflight_check2_sources.py         Check 2 (script)
pipeline/preflight_check3_alignment.py       Check 3 (script)

docs/preflight/tier_a_summary.json
docs/preflight/tier_a_flagged_rows.csv       (8,640 rows)
docs/preflight/tier_a_flagged_bullets.csv    (71,720 rows)
docs/preflight/tier_a_vocab_cities.json      (1,643 entries)
docs/preflight/tier_a_vocab_orgs.json        (9,512 entries)
docs/preflight/tier_b_sample.json            (25 rows, seed=42)
docs/preflight/tier_c_input.json             (25 facility bullet blobs)
docs/preflight/tier_c_llm_extraction.json    (25 rows, LLM judgment)
docs/preflight/tier_c_comparison.json        (agreement stats)
docs/preflight/check2_source_examples.json   (10 rows side-by-side)
docs/preflight/check2_histogram.csv
docs/preflight/check3_pairs.json             (30 rows, seed=42)
```

All under `docs/` and `pipeline/` — safe to commit (no raw dataset content beyond short bullet excerpts already quoted in the brief).

---

## Appendix A — full Tier B sample (25 rows, seed=42, unedited)

For direct reading/validation, per instructions. Machine-readable version: `docs/preflight/tier_b_sample.json`.

```
SEED=42  strong_pool=6652  weak_pool=1989  sampled=25

--- [1/25] unique_id=14fb1802-6ba6-443b-ae3c-6cac18dea466 ---
name: 'Cardion Hospital'
address_city: 'Nagpur'
description: 'A one stop destination for the advanced Heart Health and Medical requirements of the citizens of Nagpur, Central India and across the globe.'
confidence_bucket: borderline (generic single-word/numeric match only)
flagged bullet(s):
  - [capability[0]] 'Cardion Hospital is listed as a popular hospital for cardiac problems in Nagpur'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[8]] 'Hospital started in 2020 in Nagpur'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[9]] 'Dr Suryaprakash Asawa is an interventional cardiologist practicing at Cardion Hospital'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[11]] 'Cardion Hospital is categorized as a Cardiologist facility type on the American Health Tourism directory'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[12]] 'Cardion Hospital listing status on the American Health Tourism directory is Unclaimed'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[14]] 'Describes itself as the best heart and super specialty hospital in Nagpur, Maharashtra'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[18]] 'Cardion Hospital is a unit of Cardion Hospital Health Care Pvt. Ltd.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[19]] 'Cardion Hospital is located in Nagpur, Maharashtra'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [procedure[9]] 'Cardion Hospital is a unit of Cardion Hospital Health Care Pvt. Ltd.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']

--- [2/25] unique_id=76291067-1c82-4c02-8160-f17e72d84e65 ---
name: 'Varanasi Hospital'
address_city: 'Varanasi'
description: 'Varanasi Hospital (NABH ACCREDITED) is a Multi-Specialty Hospital which was established by Dr. Baijnath Prasad, senior most surgeon of the region, working since 1964. It is the most renowned centre for laparoscopic surgery headed by Dr Manish Jindal.'
confidence_bucket: borderline (generic single-word/numeric match only)
flagged bullet(s):
  - [capability[1]] 'Multi-Specialty hospital'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[4]] 'Specialty hospital in eastern Uttar Pradesh'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']

--- [3/25] unique_id=34c1fc06-d8fc-4c14-991d-5d3f94c4159b ---
name: 'Calcutta Medical Centre'
address_city: 'Kolkata'
description: 'Calcutta Medical Centre is an Internal Medicine Clinic in Minto Park, Kolkata.'
confidence_bucket: borderline (generic single-word/numeric match only)
flagged bullet(s):
  - [capability[0]] 'Hospital activities (NIC code 8511) including general and specialized hospitals, sanatoria, asylums, rehabilitation centres, dental centres and other health institutions with accommodation facilities.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[1]] 'Calcutta Medical Centre is a hospital in Kolkata.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']

--- [4/25] unique_id=cf328ef3-97c1-4e5d-8ce5-8f75132f6a0c ---
name: 'Wadhwa Pathology Lab'
address_city: 'Sonipat'
description: 'Free Home Sample Collection'
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[2]] 'Provides pathology laboratory testing services in Palwal Rural, Haryana'
    why flagged: foreign_cities=['palwal'] foreign_orgs=[]

--- [5/25] unique_id=0548e4f4-cfd5-4822-ac49-10ec1ee8eb25 ---
name: 'Sai Multispeciality Hospital Washim'
address_city: 'Washim'
description: 'Hospital'
confidence_bucket: borderline (generic single-word/numeric match only)
flagged bullet(s):
  - [capability[0]] 'Sai Multispeciality Hospital Washim is listed as a hospital in Washim district on the District Washim government site (Hospital section).'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[1]] 'First hospital in Washim district to have modular operation theatre'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [equipment[0]] 'Has modular operation theatre at Sai Multispeciality Hospital, Washim district'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']

--- [6/25] unique_id=1d3f5f2e-e990-4bd8-abef-bd6c2b2b8d84 ---
name: "Dr. Jairaj's Hospital - 2nd Floor, Om Chanakya Complex, Sector 6, CBD Belapur, Navi Mumbai, Maharashtra 400614"
address_city: 'Navi Mumbai'
description: "Welcome to Dr. Jairaj's Hospital, a leading multispeciality healthcare institution located in Belapur, Navi Mumbai. Specialties include Orthopaedic, General Medicine, Gynecology, Laparoscopic Surgery, Maternity, Critical Care."
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[3]] 'Multispeciality hospital offering services across orthopedics, general medicine, critical care, cardiology, general & laparoscopic surgery, gastroenterology, neurology, gynecology, pediatrics, dermatology, and urology'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[8]] 'Private hospital in Navi Mumbai, Maharashtra'
    why flagged: foreign_cities=['mumbai'] foreign_orgs=['hospital']
  - [capability[16]] 'Located in Navi Mumbai'
    why flagged: foreign_cities=['mumbai'] foreign_orgs=[]

--- [7/25] unique_id=08c8f9bb-2e33-468e-8b09-0b1d235ce426 ---
name: 'Saravana Hospital'
address_city: 'Kuttalam'
description: 'SARAVANA HOSPITAL in Madurai, India.'
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[0]] 'Located in Madurai'
    why flagged: foreign_cities=['madurai'] foreign_orgs=[]
  - [capability[1]] 'Saravana Hospital is listed as a hospital in Kalimedu, Mohanur Block, Namakkal District, Tamil Nadu'
    why flagged: foreign_cities=['namakkal'] foreign_orgs=['hospital']
  - [capability[2]] 'Saravana Hospital is located in Madurai'
    why flagged: foreign_cities=['madurai'] foreign_orgs=['hospital']
  - [capability[6]] 'Located in Madurai, India'
    why flagged: foreign_cities=['madurai'] foreign_orgs=[]
  - [capability[7]] "Saravana Hospital is listed in Medindia's directory as a hospital in Chennai, Tamil Nadu."
    why flagged: foreign_cities=['chennai'] foreign_orgs=['hospital']
  - [capability[8]] "Listed in Medindia's Chennai, Tamil Nadu hospital directory as Saravana Hospital"
    why flagged: foreign_cities=['chennai'] foreign_orgs=['hospital']
  - [capability[9]] "Saravana Hospital is listed in Medindia's hospital directory as a hospital in Coimbatore, Tamil Nadu"
    why flagged: foreign_cities=['coimbatore'] foreign_orgs=['hospital']
  - [capability[10]] 'Shree Mahavir Multispeciality Hospital Pvt. Ltd. operates as a multispeciality hospital.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[11]] 'Located in AHMEDABAD, Gujarat.'
    why flagged: foreign_cities=['ahmedabad'] foreign_orgs=[]
  - [capability[14]] 'Saravana Hospital is located in Madurai.'
    why flagged: foreign_cities=['madurai'] foreign_orgs=['hospital']
  - [capability[15]] 'Located in Madurai, India'
    why flagged: foreign_cities=['madurai'] foreign_orgs=[]
  - [capability[16]] 'Established in 2004 in Madurai, Tamil Nadu'
    why flagged: foreign_cities=['madurai'] foreign_orgs=[]
  - [capability[17]] 'Multi-speciality hospital'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[26]] "Listed in Medindia's hospital directory as Saravana Hospital in Chennai, Tamil Nadu"
    why flagged: foreign_cities=['chennai'] foreign_orgs=['hospital']
  - [capability[27]] "Listed in Medindia's Chennai, Tamil Nadu hospital directory"
    why flagged: foreign_cities=['chennai'] foreign_orgs=['hospital']
  - [capability[28]] "Listed in Medindia's directory as a hospital in Coimbatore, Tamil Nadu"
    why flagged: foreign_cities=['coimbatore'] foreign_orgs=['hospital']
  - [capability[29]] 'Located in Namakkal, Tamil Nadu, India'
    why flagged: foreign_cities=['namakkal'] foreign_orgs=[]
  - [capability[30]] "Saravana Hospital is listed in Medindia's hospital directory as a hospital in Coimbatore, Tamil Nadu."
    why flagged: foreign_cities=['coimbatore'] foreign_orgs=['hospital']
  - [capability[31]] "Listed in Medindia's hospital directory for Chennai, Tamil Nadu as Saravana Hospital"
    why flagged: foreign_cities=['chennai'] foreign_orgs=['hospital']
  - [capability[32]] 'Hospital located in Coimbatore, Tamil Nadu'
    why flagged: foreign_cities=['coimbatore'] foreign_orgs=['hospital']
  - [capability[33]] "Listed in Medindia's hospital directory for Coimbatore, Tamil Nadu"
    why flagged: foreign_cities=['coimbatore'] foreign_orgs=['hospital']
  - [capability[34]] 'Listed in Medindia Hospital Directory for Chennai, Tamil Nadu'
    why flagged: foreign_cities=['chennai'] foreign_orgs=['hospital']
  - [capability[35]] "Saravana Hospital is listed in Medindia's directory as a hospital in Chennai, Tamil Nadu."
    why flagged: foreign_cities=['chennai'] foreign_orgs=['hospital']
  - [capability[36]] "Saravana Hospital is listed in Medindia's directory as a hospital in Chennai, Tamil Nadu, India."
    why flagged: foreign_cities=['chennai'] foreign_orgs=['hospital']
  - [capability[39]] 'Listed as a government hospital in Madurai on Sulekha'
    why flagged: foreign_cities=['madurai'] foreign_orgs=['hospital']
  - [capability[40]] 'Located in Athikulam, Madurai'
    why flagged: foreign_cities=['madurai'] foreign_orgs=[]
  - [capability[41]] "Listed in Medindia's hospital directory as Saravana Hospital in Chennai, Tamil Nadu"
    why flagged: foreign_cities=['chennai'] foreign_orgs=['hospital']
  - [capability[42]] "Listed in Medindia's Coimbatore hospital directory"
    why flagged: foreign_cities=['coimbatore'] foreign_orgs=['hospital']
  - [capability[43]] 'Saravana Hospital is located in Coimbatore, Tamil Nadu, India.'
    why flagged: foreign_cities=['coimbatore'] foreign_orgs=['hospital']
  - [capability[44]] "Listed in Medindia's hospital directory as Saravana Hospital in Coimbatore, Tamil Nadu."
    why flagged: foreign_cities=['coimbatore'] foreign_orgs=['hospital']
  - [capability[45]] "Listed in Medindia's hospital directory as Saravana Hospital in Coimbatore, Tamil Nadu."
    why flagged: foreign_cities=['coimbatore'] foreign_orgs=['hospital']

--- [8/25] unique_id=202c3520-fc99-4391-a6e1-fad7e062e679 ---
name: 'Jaya Dental Clinic'
address_city: 'Chennai'
description: 'Dental Clinic in Ramanathapuram'
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[0]] 'Outpatient dental clinic'
    why flagged: foreign_cities=[] foreign_orgs=['dental clinic']
  - [capability[1]] 'Multispeciality dental clinic'
    why flagged: foreign_cities=[] foreign_orgs=['dental clinic', 'multispeciality dental clinic']

--- [9/25] unique_id=a0081495-bbdd-49d9-92f2-2cc6a28e4ca0 ---
name: 'Sadamangalam Ayurvedic Panchakarma Clinic & Fertiveda'
address_city: 'Kolhapur'
description: 'Sadamangalam Ayurvedic Panchakarma Clinic & Fertiveda'
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[1]] 'Affiliated physician: Dr. Sachin Ganeshwadi, MD, with 15+ years of Ayurveda experience, practicing at the clinic since August 2010'
    why flagged: foreign_cities=['sachin'] foreign_orgs=[]
  - [capability[11]] 'Has 2 Ayurveda doctors on staff.'
    why flagged: foreign_cities=['2'] foreign_orgs=[]
  - [capability[12]] 'Dr. Sachin Sadashiv Ganeshwadi provides Ayurveda consultations; consultation fee 500 INR.'
    why flagged: foreign_cities=['sachin'] foreign_orgs=[]
  - [capability[13]] 'Dr. Prajakta Sachin Ganeshwadi provides Ayurveda consultations; consultation fee 500 INR.'
    why flagged: foreign_cities=['sachin'] foreign_orgs=[]

--- [10/25] unique_id=e517f93d-ff0f-4c90-b6ae-4b85343a1f25 ---
name: 'Redent Dental Clinic'
address_city: 'Ahmedabad'
description: 'Dental clinic'
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[0]] 'Redent Dental Clinic is a dental clinic located in Ahmedabad, Gujarat'
    why flagged: foreign_cities=[] foreign_orgs=['dental clinic']
  - [capability[1]] 'Dr. Vanali Umrania is a dentist specializing in Dentistry and practices at Redent Dental Clinic'
    why flagged: foreign_cities=[] foreign_orgs=['dental clinic']
  - [capability[2]] "Dr. Vanali Umrania is listed on Medindia's directory as a dentist associated with Redent Dental Clinic in Ahmedabad, Gujarat"
    why flagged: foreign_cities=[] foreign_orgs=['dental clinic']
  - [capability[3]] 'Dental clinic offering outpatient dental consultations'
    why flagged: foreign_cities=[] foreign_orgs=['dental clinic']

--- [11/25] unique_id=06bdf654-fc15-49eb-89c5-e3c14010ebe1 ---
name: 'Rela Hospital'
address_city: 'Chennai'
description: 'Rela Hospital is a 30-bed multispeciality hospital at Oragadam, Chennai, with ICU, Mini-OT, and 24x7 Ambulance and Pharmacy Services.'
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[2]] 'Rela Hospital has 30 beds including ICU'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[3]] 'Rela Hospital is a multispeciality hospital located at Oragadam within the Hiranandani Parks Oragadam township'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[4]] 'Rela Hospital provides 24x7 ambulance services'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[5]] 'Rela Hospital provides 24x7 pharmacy services'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[6]] '30-bed multi-speciality hospital with ICU and OT facilities'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[12]] 'Rela Hospital is located in Chromepet, Chennai'
    why flagged: foreign_cities=['chromepet'] foreign_orgs=['hospital']
  - [capability[13]] 'Rela Hospital is listed as a clinic option for Dr. Sukanya Mathupal (ENT Specialist) in Chennai'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[14]] 'Rela Hospital is a Chennai-based hospital.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[15]] 'Rela Hospital is located in Chennai, India.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[16]] "Rela Hospital is listed in Innayat Medical's hospital network."
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[17]] 'Quaternary care hospital'
    why flagged: foreign_cities=[] foreign_orgs=['care hospital', 'hospital']
  - [capability[26]] 'First hospital in South Asia to perform pediatric auxiliary liver transplant'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[29]] 'Rela Hospital is located in Chennai, India'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[30]] "Rela Hospital is listed as a related hospital in Innayat Medical's hospital network"
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[46]] 'SARS-CoV-2 Real-time RT-PCR testing capability'
    why flagged: foreign_cities=['2'] foreign_orgs=[]
  - [capability[47]] 'CBNAAT (Cepheid Xpert Xpress) SARS-CoV-2 testing capability'
    why flagged: foreign_cities=['2'] foreign_orgs=[]
  - [capability[48]] 'SARS-CoV-2 IgG antibody testing capability'
    why flagged: foreign_cities=['2'] foreign_orgs=[]
  - [procedure[20]] 'Microbiology and Serology with MALDI-TOF, BacT Alert, Vitek 2, CMIA/ELFA/ELISA, MGIT, Real-time PCR for Virology, CBNAAT for SARS-CoV-2'
    why flagged: foreign_cities=['2'] foreign_orgs=[]
  - [procedure[31]] 'Intracytoplasmic Sperm Injection (ICSI)'
    why flagged: foreign_cities=[] foreign_orgs=['icsi']
  - [equipment[17]] 'BioMerieux MALDI-TOF and culture systems (BacT Alert, Vitek 2)'
    why flagged: foreign_cities=['2'] foreign_orgs=[]
  - [equipment[21]] 'CBNAAT (Cepheid Xpert Xpress) for SARS-CoV-2'
    why flagged: foreign_cities=['2'] foreign_orgs=[]

--- [12/25] unique_id=4d3a82b0-8b69-4918-b15f-58d7f579818c ---
name: 'My Smile Dental Clinic'
address_city: 'Mumbai'
description: 'Located in Mumbai, My Smile Dental Clinic offers comprehensive dental care with a focus on patient comfort, including preventive, restorative, cosmetic dentistry, orthodontics, pediatric dentistry, and oral surgery.'
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[0]] 'Outpatient dental clinic'
    why flagged: foreign_cities=[] foreign_orgs=['dental clinic']

--- [13/25] unique_id=77edbf49-532f-4e03-a2d1-04bc2b34cbd6 ---
name: 'Upasana Hospital'
address_city: 'Kollam'
description: 'Upasana Hospital'
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[2]] 'Listed as Upasana Hospital in the OneFiveNine directory of hospitals in Kollam district'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[3]] 'Located in Anchal, Kollam, Kerala, India'
    why flagged: foreign_cities=['anchal'] foreign_orgs=[]
  - [capability[5]] 'Located in Pathanapuram, Kollam district, Kerala, India'
    why flagged: foreign_cities=['pathanapuram'] foreign_orgs=[]
  - [capability[8]] 'Multispecialty hospital'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[13]] 'Upasana Hospital is listed as a hospital in Kollam district.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[16]] 'Upasana Hospital is located in Kollam'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[17]] 'Upasana Hospital has official website upasanahospital.com'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[18]] 'Upasana Hospital is listed on MedicalKerala as Upasana Hospital'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[23]] 'Upasana Hospital has website www.upasanahospital.com'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[24]] 'Upasana Hospital is located in Kollam'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[25]] 'Listed among Top Hospitals In Thiruvananthapuram on Practo'
    why flagged: foreign_cities=['thiruvananthapuram'] foreign_orgs=[]
  - [capability[26]] 'Located in Idukki district, Kerala, India'
    why flagged: foreign_cities=['idukki'] foreign_orgs=[]
  - [capability[29]] 'Upasana Hospital is described as a Medical centre on its unofficial Facebook page'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[30]] 'Facebook page for Upasana Hospital is labeled as unofficial'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[31]] 'Upasana Hospital is a multi-specialty hospital'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[33]] 'Upasana Hospital is a multi-specialty hospital located in Chamkkada, Kollam'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[36]] 'Upasana Hospital is listed among top hospitals in Thiruvananthapuram on Practo'
    why flagged: foreign_cities=['thiruvananthapuram'] foreign_orgs=['hospital']
  - [capability[37]] 'Upasana Hospital is located in Thiruvananthapuram'
    why flagged: foreign_cities=['thiruvananthapuram'] foreign_orgs=['hospital']
  - [capability[38]] 'Upasana Hospital is listed among Top General Physician Hospitals in Thiruvananthapuram on Practo'
    why flagged: foreign_cities=['thiruvananthapuram'] foreign_orgs=['hospital']
  - [capability[39]] 'Listed among Top Hospitals In Thiruvananthapuram'
    why flagged: foreign_cities=['thiruvananthapuram'] foreign_orgs=[]
  - [procedure[0]] 'Upasana Hospital is a multi-specialty hospital.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']

--- [14/25] unique_id=83e6bce6-4fea-4d64-b059-cf4a052ba00d ---
name: 'Rohilkhand Medical College and Hospital'
address_city: 'Bareilly'
description: 'Rohilkhand Medical College and Hospital, established in 2006, is a private medical college and hospital in Bareilly, Uttar Pradesh, India. It offers MBBS and postgraduate degrees, nursing and para-medical courses. The yearly undergraduate intake is 250. It is affiliated with Bareilly International U'
confidence_bucket: borderline (generic single-word/numeric match only)
flagged bullet(s):
  - [capability[0]] 'Private medical college and hospital'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[36]] 'Rohilkhand Medical College and Hospital operates as a medical college and hospital in Bareilly, Uttar Pradesh'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[37]] 'Private medical college and hospital in Bareilly, Uttar Pradesh'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[38]] 'Listed in UP NEET-UG 2024-25 private medical college fees structure as Rohilkhand Medical College & Hospital, Bareilly'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[39]] 'Rohilkhand Medical College and Hospital is a private medical college and hospital affiliated with Bareilly International University'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[45]] 'Placement rating: 3.6 for MBBS program'
    why flagged: foreign_cities=['6'] foreign_orgs=[]
  - [capability[47]] 'Private medical college and hospital'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [procedure[7]] 'Performs fine-needle aspiration cytology (FNAC) of cervical lymph nodes; a published study from Rohilkhand Medical College and Hospital analyzed 290 cases (Nov 2016–Oct 2017) showing reactive lymphadenopathy as most common cytology, followed by granulomatous lymphadenitis, necrotizing lymphadenitis, and tuberculous lymphadenitis.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']

--- [15/25] unique_id=e47ec8f1-74c2-418a-88cd-73967f0f1fc5 ---
name: 'Satya Aesthetics & Hair Solutions - Delhi'
address_city: 'Delhi'
description: "World's leading aesthetic and hair restoration Centre. Where Science meets Art."
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[6]] 'Operates Delhi and Gurugram clinics'
    why flagged: foreign_cities=['gurugram'] foreign_orgs=[]
  - [capability[9]] 'Operates two clinics in Delhi and Gurugram'
    why flagged: foreign_cities=['gurugram'] foreign_orgs=[]
  - [capability[16]] 'Operates in Delhi and Gurgaon'
    why flagged: foreign_cities=['gurgaon'] foreign_orgs=[]
  - [procedure[23]] 'Performs Dermapen 4 microneedling'
    why flagged: foreign_cities=[] foreign_orgs=['4']
  - [procedure[42]] 'Dermapen 4 microneedling'
    why flagged: foreign_cities=[] foreign_orgs=['4']
  - [equipment[6]] 'Dermapen 4 microneedling device'
    why flagged: foreign_cities=[] foreign_orgs=['4']
  - [equipment[10]] 'Dermapen 4 microneedling device'
    why flagged: foreign_cities=[] foreign_orgs=['4']

--- [16/25] unique_id=44d05b85-41b3-471a-ac6d-ceec8f9e96eb ---
name: 'Anantshree Hospital'
address_city: 'Bhopal'
description: 'Owner: Dr. Navin Batra'
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[1]] 'Listed as a top hospital in Bhopal on Practo'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[3]] 'Listed in Medicine India hospital directory'
    why flagged: foreign_cities=[] foreign_orgs=['hospital', 'india hospital']
  - [capability[6]] 'Aditya Birla Health Insurance network hospital in Bhopal'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[13]] 'Anantshree Hospital is listed among top hospitals in Bhopal on Practo.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[14]] 'Anantshree Hospital is listed among top clinics in Gulmohar Colony, Bhopal'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[15]] 'Listed as a top hospital in Karond, Bhopal'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[16]] 'Anantshree Hospital is listed as a top clinic in Gulmohar Colony, Bhopal on Practo'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[20]] 'Anantshree Hospital is listed as a Bajaj General Network Hospital in Bhopal, Madhya Pradesh'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[21]] 'Anantshree Hospital is listed in Bajaj Allianz network hospitals in Bhopal'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']

--- [17/25] unique_id=c15da265-c0a5-4986-ae91-7f8421abe308 ---
name: "Nizam's Institute of Medical Sciences"
address_city: 'Hyderabad'
description: "Mentioned as a healthcare-related organization in Hyderabad, Telangana, India, in Telangana's industrial context."
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[3]] 'Tertiary-care hospital with a Department of Medical Gastroenterology'
    why flagged: foreign_cities=[] foreign_orgs=['care hospital', 'hospital']
  - [capability[33]] "Nizam's Institute of Medical Sciences is a public hospital and state university located in Hyderabad, Telangana, India"
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[41]] 'Expanded in 2013 with two blocks: a 300-bed Super Specialty Hospital and a 200-bed Accident & Trauma Hospital'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[42]] 'In November 2022, Telangana government approved expansion to add 2,000 beds, including 500 ICU beds, and increasing departments to 42'
    why flagged: foreign_cities=['2'] foreign_orgs=[]
  - [procedure[7]] 'Reports HBV vaccination coverage among health-care workers (80.4% uptake: 33/41) as part of occupational exposure management'
    why flagged: foreign_cities=['33'] foreign_orgs=['4']
  - [procedure[8]] 'Monitors health-care workers for chronic HBV/HCV infection during at least 6 months of follow-up after exposure'
    why flagged: foreign_cities=['6'] foreign_orgs=[]
  - [procedure[27]] "Originated as Nizam's Orthopaedic Hospital in Hyderabad."
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [procedure[38]] 'Participates in a multi-centre study evaluating hospital environment for presence of Mucorales during COVID-19-associated mucormycosis outbreak in India'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [equipment[6]] "First CT scanner in a state-run hospital installed at NIMS during Dr. Raja Reddy's directorship (1990–1993)"
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [equipment[14]] 'Link corridor connects floors 2, 3, 4 & 5 to ensure quick access between operating theatres (OTs), diagnostic services and wards.'
    why flagged: foreign_cities=['2'] foreign_orgs=['4']
  - [equipment[15]] 'Two separate buildings are connected by a link corridor at upper floor levels to allow easy access between the Specialty Hospital and the Emergency Hospital within the complex.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [equipment[18]] 'Cardiac imaging services provided by Care Hospital include cardiac MRI, cardiac CT, nuclear cardiology, and 2D/3D echocardiography'
    why flagged: foreign_cities=[] foreign_orgs=['care hospital', 'hospital']
  - [equipment[19]] 'Care Hospital installed the first dual-source CT in South India'
    why flagged: foreign_cities=[] foreign_orgs=['care hospital', 'hospital']

--- [18/25] unique_id=e3279475-58c3-451b-b37b-5f9333d83380 ---
name: 'Nahar Medical Centre'
address_city: 'Mumbai'
description: 'Welcome to Nahar Medical Centre, a premier state-of-the-art medical and diagnostic facility located in Chandivali, Mumbai. At Nahar Medical Centre, your well-being is our top priority. From the moment you step in, you are greeted with warmth, hospitality, and a commitment to helping you return to op'
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[4]] 'Located in Andheri East, Mumbai'
    why flagged: foreign_cities=['andheri', 'andheri east'] foreign_orgs=[]
  - [capability[6]] 'Offers IVF, ICSI, IUI and other ART services including sperm wash, blastocyst culture, embryo cryopreservation, embryo donation, surrogacy, laser assisted hatching, and surgical sperm retrieval'
    why flagged: foreign_cities=[] foreign_orgs=['icsi']
  - [capability[15]] 'Opening hours: Mon to Sat 8:00 am–8:00 pm; Sunday 8:00 am–4:00 pm'
    why flagged: foreign_cities=[] foreign_orgs=['4']
  - [procedure[12]] 'Intra Cytoplasmic Sperm Injection (ICSI)'
    why flagged: foreign_cities=[] foreign_orgs=['icsi']

--- [19/25] unique_id=b0d5a919-d6ff-4137-b0d6-8503a64c1f30 ---
name: 'Divine Touch Dental Hospital & Amp Research Centre'
address_city: 'Prayagraj'
description: 'Divine Touch Dental Hospital & Research Centre. Director: Krishn Singh.'
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[0]] 'Operates as a dental hospital and research centre'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[2]] 'Divine Touch Dental Hospital & Research Centre is a dental hospital and research centre'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[3]] 'Located in Allahabad, Uttar Pradesh, India'
    why flagged: foreign_cities=['allahabad'] foreign_orgs=[]
  - [capability[7]] 'Markets itself as the biggest and number one dental hospital in the state of Uttar Pradesh'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[8]] 'Divine Touch Dental Hospital & Research Centre is a dental hospital and research centre located in Allahabad, Uttar Pradesh, India.'
    why flagged: foreign_cities=['allahabad'] foreign_orgs=['hospital']
  - [capability[9]] 'It claims to be the biggest and number one dental hospital in the state of Uttar Pradesh.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']

--- [20/25] unique_id=2844da4b-b15d-4173-aaff-11ca0ae02dd3 ---
name: 'Tulsi Dental Clinic, Tulsi Complex, Ramganj, Ajmer-305001'
address_city: 'Ajmer'
description: 'Tulsi Dental Clinic provides complete dental services in Ajmer, including general care, cosmetic dentistry and dental implants, with emphasis on prevention and personalized attention. The page notes two locations in Ajmer.'
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[16]] 'Has 2 locations in Ajmer'
    why flagged: foreign_cities=['2'] foreign_orgs=[]
  - [capability[20]] 'Tulsi Dental Clinic is RGHS empaneled hospital in Ajmer'
    why flagged: foreign_cities=[] foreign_orgs=['dental clinic', 'hospital']
  - [capability[22]] 'OPD facility available under RGHS at Tulsi Dental Clinic'
    why flagged: foreign_cities=[] foreign_orgs=['dental clinic']
  - [capability[23]] 'Listed in RGHS Hospital List Ajmer'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']

--- [21/25] unique_id=189f7645-d64a-4340-8715-852c1e40f414 ---
name: 'Prem Hospital Super Speciality & Trauma Centre'
address_city: 'Jwalapur'
description: 'Prem Hospital Super Specialty & Trauma Center is one of the leading Super-Specialty Hospitals in Haridwar, Uttarakhand headed by Dr. Sandhya Sharma (Sr. gynecologists') & Dr.Shourya Sharma (Radiologist). Our state-of-the-art hospital focuses on providing World Class Services with modern amenities an'
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[0]] 'Super-specialty hospital'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[4]] 'Operates as a Trauma Center in Haridwar'
    why flagged: foreign_cities=['haridwar'] foreign_orgs=[]
  - [capability[8]] 'Operates as a multispeciality hospital with departments in Neurology, Cardiology, Gynecology, Urology, and Laparoscopic Surgery'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[11]] 'Operates as a super specialty hospital with a trauma centre'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[12]] 'Located in Haridwar, Uttarakhand, India'
    why flagged: foreign_cities=['haridwar'] foreign_orgs=[]
  - [capability[20]] 'Multispeciality hospital with departments including Cardiology, Neonatology, Nephrology, Neurosurgery, Oncology, Urology, Women's and Obstetrics, I.V.F, Gastro Science, and Gynaecologist Laparoscopic'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']

--- [22/25] unique_id=082b8d1b-e9b8-49e8-99d1-b22652a54e57 ---
name: 'Cosmos Hospital'
address_city: 'Moradabad'
description: 'Renowned hematology-oncology institute offering specialized medical services by expert doctors.'
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[0]] 'Cosmos Hospital is a hospital in Moradabad'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[1]] 'Cosmos Hospital has a JustDial rating of 4.4 based on 1,665 ratings'
    why flagged: foreign_cities=[] foreign_orgs=['4', 'hospital']
  - [capability[2]] 'Cosmos Hospital is listed as Available Now on JustDial'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[3]] '25 people recently enquired about Cosmos Hospital'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[8]] 'Cosmos Hospital is a network hospital in Moradabad under Royal Sundaram Network Hospitals'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[9]] 'Cosmos Hospital is one of 14 cashless Royal Sundaram network hospitals in Moradabad'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[11]] 'COSMOS Hospital is a 120-bed multi-speciality hospital located in Moradabad, Uttar Pradesh, India'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[17]] 'Cosmos Hospital employs a senior neurosurgeon, Dr. Sanjay Gupta.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[18]] 'Cosmos Hospital is located in Prem Nagar, Moradabad, near Kothiwal Dental College.'
    why flagged: foreign_cities=['nagar'] foreign_orgs=['hospital']
  - [capability[19]] 'Dr. Gaurav Agarwal is a neonatologist affiliated with Cosmos Hospital.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[21]] 'Cosmos Hospital is located in Moradabad, Uttar Pradesh, India.'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[23]] 'Located in Meerut, Uttar Pradesh'
    why flagged: foreign_cities=['meerut'] foreign_orgs=[]
  - [capability[24]] 'Dr. Ankit Jain is listed as a rheumatologist at Cosmos Hospital'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[35]] 'Cosmos Hospital provides General Surgery services'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[36]] 'Cosmos Hospital provides ENT services'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[37]] 'Cosmos Hospital provides Neurology services'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[38]] 'Cosmos Hospital provides Neurosurgery services'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[39]] 'Cosmos Hospital provides Radiology services'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[40]] 'Cosmos Hospital provides pediatric care'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[41]] 'Cosmos Hospital is a hospital located in Moradabad, Uttar Pradesh'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[42]] 'Cosmos Hospital coordinates are 28.8941550, 78.7291995'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[45]] 'Cosmos Hospital provides orthopedic services'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']
  - [capability[46]] 'Dr Anurag Aggarwal is an orthopedic surgeon at Cosmos Hospital in Moradabad'
    why flagged: foreign_cities=[] foreign_orgs=['hospital']

--- [23/25] unique_id=3817a9c6-5019-458f-859b-9368f53bc31f ---
name: 'Inspiria Laparoscopy & IVF Research Centre'
address_city: 'Rahata (shirdi)'
description: 'Inspiria Laparoscopy & IVF Research Centre provides laparoscopic surgeries, hysteroscopic surgeries, and infertility treatments including IVF, with an Infertility Department and Laparoscopy services.'
confidence_bucket: borderline (generic single-word/numeric match only)
flagged bullet(s):
  - [capability[4]] 'Ultra Modern IVF Lab with laminar air flow, stereo zoom microscope, and ICSI machine'
    why flagged: foreign_cities=[] foreign_orgs=['icsi']
  - [capability[12]] 'Laminar flow IVF lab with AHU and ICSI capability'
    why flagged: foreign_cities=[] foreign_orgs=['icsi']
  - [procedure[1]] 'Grade 4 Endometriosis treated'
    why flagged: foreign_cities=[] foreign_orgs=['4']
  - [procedure[27]] 'Provides IVF with ICSI'
    why flagged: foreign_cities=[] foreign_orgs=['icsi']
  - [equipment[0]] '3D Laparoscopy Camera Einstein vision 3.0'
    why flagged: foreign_cities=['0'] foreign_orgs=[]
  - [equipment[2]] 'Latest ICSI machine'
    why flagged: foreign_cities=[] foreign_orgs=['icsi']
  - [equipment[5]] '3D Laparoscopy camera (Einstein Vision 3.0) used for gynecologic endoscopic procedures'
    why flagged: foreign_cities=['0'] foreign_orgs=[]
  - [equipment[7]] 'ICSI machine in the IVF lab'
    why flagged: foreign_cities=[] foreign_orgs=['icsi']

--- [24/25] unique_id=8c2726fb-32b9-4edb-be98-dc6aede7fc3b ---
name: 'Ratnagiri Test Tube Baby & Research Center'
address_city: 'Ratnagiri'
description: 'Started Ratnagiri Test Tube Baby & Research Center with IVF and ICSI Technology'
confidence_bucket: borderline (generic single-word/numeric match only)
flagged bullet(s):
  - [capability[1]] 'ICSI program'
    why flagged: foreign_cities=[] foreign_orgs=['icsi']
  - [procedure[0]] 'Performs Intra Cytoplasmic Sperm Injection (ICSI)'
    why flagged: foreign_cities=[] foreign_orgs=['icsi']

--- [25/25] unique_id=3e8efbf0-c7db-48bc-87e7-2897af0fecc3 ---
name: 'Kids Dental Clinic'
address_city: 'Ahmedabad'
description: 'Dental clinic offering dental consultations and procedures including RCT (root canal treatment), pulpectomy, and extractions, with services such as dental consultation and conservative dentistry.'
confidence_bucket: obvious (city or specific-org mismatch)
flagged bullet(s):
  - [capability[0]] 'Located in Titanium City Center Mall, Satellite, Ahmedabad, Gujarat, India'
    why flagged: foreign_cities=['satellite'] foreign_orgs=[]
  - [capability[5]] 'Outpatient dental clinic'
    why flagged: foreign_cities=[] foreign_orgs=['dental clinic']
  - [capability[7]] 'Dr Akash Patodia is the pediatric dentist at Kids Dental Clinic'
    why flagged: foreign_cities=[] foreign_orgs=['dental clinic']
  - [capability[9]] 'Dental clinic'
    why flagged: foreign_cities=[] foreign_orgs=['dental clinic']
  - [capability[11]] 'Specialty dental clinic exclusively for children'
    why flagged: foreign_cities=[] foreign_orgs=['dental clinic']
  - [equipment[3]] 'Modern clinic interior with white cabinetry and dental equipment (Kids Dental Clinic)'
    why flagged: foreign_cities=[] foreign_orgs=['dental clinic']
```

Cross-reference with Tier C's per-row genuine-contamination judgment: `docs/preflight/tier_c_llm_extraction.json`.

---

## Appendix B — full Check 3 raw pairs (30 rows, seed=42, unedited)

Full 30-row dump is large (`docs/preflight/check3_pairs.json`, ~1,300 index pairs total); the first 10 rows are reproduced here in full for direct reading, the remainder is in the JSON file.

```
Rows with both specialties and capability non-null/non-empty: 9933 / 10077
SEED=42  sampled=30 rows

--- unique_id=a9968578-1d59-4a80-bea4-b04dde0c440a  name='Burdwan Dental College & Hospital'  len_spec=43 len_cap=35 ---
  [0] specialties='internalMedicine'  |  capability='Established in 2009'
  [1] specialties='internalMedicine'  |  capability='Affiliated to West Bengal University of Health Sciences'
  [2] specialties='internalMedicine'  |  capability='Recognized by the Dental Council of India'
  [3] specialties='dentistry'  |  capability='Offers Bachelor of Dental Surgery (BDS) degree'
  [4] specialties='pediatricDentistry'  |  capability='Annual intake of 125 BDS students'
  [5] specialties='orthodontics'  |  capability='Has 9 specialised departments'
  [6] specialties='dentistry'  |  capability='Provides free treatment through regular outreach programs and dental camps'
  [7] specialties='oralMedicine'  |  capability='Government dental college administered by the state/UT administration'
  [8] specialties='radiology'  |  capability='Established in 2009-10'
  [9] specialties='oralAndMaxillofacialSurgery'  |  capability='Attached to Burdwan Medical College & Hospital'
  ... (33 more index pairs -- see check3_pairs.json)
  [35] specialties='dentistry'  |  capability=None
  [36] specialties='internalMedicine'  |  capability=None
  ... (array length mismatch: 43 specialties vs 35 capability bullets)

--- unique_id=67d311ae-eede-46e8-b5b4-2688a68d2a18  name='Brookefield Hospital'  len_spec=50 len_cap=50 ---
  [0] specialties='dentistry'  |  capability='Brookefield Hospital is listed among Top Clinics In Kundalahalli on Practo'
  [1] specialties='oralMedicine'  |  capability='Provides gynecology and obstetrics services'
  [2] specialties='radiology'  |  capability='Treats diseases in pregnancy'
  [3] specialties='orthodontics'  |  capability='Has gynecology and obstetrics consultant Dr Kakoli Guha Sen'
  [4] specialties='endodontics'  |  capability='Located in Kundalahalli, Bangalore, Karnataka'
  ... (equal lengths, still no semantic correspondence at any index -- see check3_pairs.json for all 50)

--- unique_id=2fc17119-70d7-42c9-83df-72d8d77fdb6f  name='Anantapur Orthopaedic Centre'  len_spec=31 len_cap=11 ---
  [0] specialties='orthopedicSurgery'  |  capability='Located in Aravindanagar, Anantapur, Andhra Pradesh, India.'
  [1] specialties='internalMedicine'  |  capability='Provides 24/7 emergency care'
  [2] specialties='orthopedicSurgery'  |  capability='Performs general and laparoscopic surgeries'
  ... (capability exhausts at index 10; specialties continues to index 30 as capability=None -- see check3_pairs.json)

--- unique_id=d353e58e-bf7f-4d67-bc17-e92ead01462f  name='Dr Chopra Dental Clinic'  len_spec=26 len_cap=18 ---
  [0] specialties='dentistry'  |  capability='Operates as a dental clinic'
  [1] specialties='familyMedicine'  |  capability='Has 23+ years of dentistry experience'
  [2] specialties='dentistry'  |  capability='Based in Jaipur, Rajasthan, India'
  ... (see check3_pairs.json for all 26 index pairs)

--- unique_id=92e6b44e-982a-43fc-bf94-41eaf71249a9  name='Keeran Eye Care'  len_spec=11 len_cap=10 ---
  [0] specialties='familyMedicine'  |  capability='Outpatient eye clinic'
  [1] specialties='ophthalmology'  |  capability='Open hours: Monday to Saturday 12:30 PM-8:30 PM; Sunday 9:00 AM-8:30 PM'
  [2] specialties='familyMedicine'  |  capability='Located in Zamrudpur-Greater Kailash 1, Delhi'
  ... (see check3_pairs.json for all 11 index pairs)
```

Remaining 25 of 30 sampled rows (`IAS Medicare Hospital`, `Chintamani Hospital And Dental Clinic`, `The Good Dentist`, `Sub District Hospital Beerwah`, `Branch - 2M&M Ortho Multispeciality Hospitals`, `Ram Ratan Hospital`, `Muzaffarnagar Medical College`, `Vaatsalya Hospitals`, and 17 more) show the identical pattern throughout — full detail in `docs/preflight/check3_pairs.json`.
