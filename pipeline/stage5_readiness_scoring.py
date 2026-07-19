"""Stage 5: readiness scoring over Stage 4's deterministic capability map.

Per (facility_id, capability_id) pair, produces a defensible verdict --
corroborated / claimed_only / insufficient_evidence -- grounded entirely in
Stage 4's matched tracer evidence. Nothing new is collected here; this is
pure aggregation and thresholding, conforming to contract C2.

Method note: the brief's original conditional-pass rule (mean domain score
>= 80%, no domain at zero) was tested against the real, complete Stage 4
output during planning and found to pass only 0.2% of pairs -- calibrated
for in-person inspector surveys, not passively scraped web text. Recalibrated:
conditional_pass = >=2 distinct tracer matches, which empirically tracks
top-quartile readiness (23.8% vs 26.3% of pairs). See docs/readiness/
stage5_findings.md for the full writeup.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
TAXONOMY_PATH = ROOT / "taxonomy" / "capability_taxonomy.yaml"
MAP_PATH = ROOT / "data" / "processed" / "bullet_capability_map.parquet"
BULLETS_PATH = ROOT / "data" / "processed" / "evidence_bullets.parquet"
FACILITIES_PATH = ROOT / "data" / "facilities_local.parquet"
OUT_PATH = ROOT / "data" / "processed" / "facility_capability_readiness.parquet"
DOCS_DIR = ROOT / "docs" / "readiness"

COMPLETENESS_FLOOR = 5  # facility-level total evidence-bullet count

CONTRADICTION_RULES = [
    {
        "capability_id": "general_surgery",
        "claim_tracers": {"general_surgery_service", "hernia_repair", "appendectomy", "cholecystectomy"},
        "required_tracer": "operation_theatre",
        "message": "general_surgery claimed with no operation theatre evidenced",
    },
    {
        "capability_id": "icu",
        "claim_tracers": {"icu_service", "mechanical_ventilation"},
        "required_tracer": "ventilator",
        "message": "icu claimed with no ventilator evidence",
    },
]


def load_taxonomy(path: Path = TAXONOMY_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def domain_totals_by_capability(taxonomy: dict[str, Any]) -> dict[str, dict[str, int]]:
    return {
        cap: {domain: len(tracers) for domain, tracers in domains.items()}
        for cap, domains in taxonomy["capabilities"].items()
    }


def score_facility_capability(
    group: pd.DataFrame,
    domain_totals: dict[str, int],
) -> dict[str, Any]:
    """Pure function: score one (facility, capability) group of matched rows.

    ``group`` must contain columns: domain, tracer_id, exclude_from_scoring,
    bullet_id, provenance_status, capability_id (all rows share facility_id
    and capability_id already, both passed through by the caller).
    """
    scoring_rows = group[~group["exclude_from_scoring"]]
    rejected_rows = group[group["exclude_from_scoring"]]

    domain_scores: dict[str, float] = {}
    for domain, total in domain_totals.items():
        matched = scoring_rows.loc[scoring_rows["domain"] == domain, "tracer_id"].nunique()
        domain_scores[domain] = round(matched / total, 4) if total else 0.0

    readiness_score = round(sum(domain_scores.values()) / len(domain_scores), 4) if domain_scores else 0.0
    distinct_tracer_count = int(scoring_rows["tracer_id"].nunique())
    distinct_bullet_count = int(scoring_rows["bullet_id"].nunique())
    distinct_source_field_count = int(scoring_rows["source_field"].nunique())
    # Corroboration requires >=2 distinct tracers AND >=2 distinct source
    # fields -- a single bullet matching two tracer patterns at once (e.g.
    # "ICU with ventilator support") is one claim, not independent
    # corroboration, and neither are two bullets that both happen to come
    # from the same source field (e.g. two sentences split out of the same
    # "description" text) -- that's still one underlying claim about the
    # facility, not two independent statements. Requiring source-field
    # diversity subsumes the >=2-distinct-bullet requirement (two different
    # source fields are never the same bullet) while being strictly
    # stronger: a second-opinion review found 830 of 3,320 (25%) of pairs
    # that passed the old >=2-bullets rule still drew every bullet from one
    # source field. See docs/readiness/stage5_findings.md.
    conditional_pass = distinct_tracer_count >= 2 and distinct_source_field_count >= 2

    matched_tracer_ids = set(scoring_rows["tracer_id"].unique())
    contradiction_flags = []
    if len(scoring_rows):
        capability_id = scoring_rows["capability_id"].iloc[0]
        for rule in CONTRADICTION_RULES:
            if rule["capability_id"] != capability_id:
                continue
            if matched_tracer_ids & rule["claim_tracers"] and rule["required_tracer"] not in matched_tracer_ids:
                contradiction_flags.append(rule["message"])

    return {
        "domain_scores": domain_scores,
        "readiness_score": readiness_score,
        "distinct_tracer_count": distinct_tracer_count,
        "distinct_bullet_count": distinct_bullet_count,
        "distinct_source_field_count": distinct_source_field_count,
        "conditional_pass": conditional_pass,
        "contradiction_flags": contradiction_flags,
        "supporting_bullet_ids": sorted(scoring_rows["bullet_id"].unique().tolist()),
        "rejected_bullet_ids": sorted(rejected_rows["bullet_id"].unique().tolist()),
        "provenance_flags": sorted(group["provenance_status"].dropna().unique().tolist()),
    }


def verdict_for(conditional_pass: bool, completeness_bullet_count: int, distinct_tracer_count: int) -> str:
    # Zero matches for this specific capability is "we found no evidence",
    # not "claimed" -- distinct from the facility-wide completeness floor,
    # this is a capability-specific completeness signal (the simplest
    # possible one; a documented MVP simplification, see findings doc).
    # NOTE: distinct_tracer_count == 0 is checked FIRST and unconditionally
    # returns insufficient_evidence, regardless of completeness_bullet_count
    # -- a zero-match pair can never become claimed_only no matter how much
    # unrelated evidence the facility has on file elsewhere.
    if distinct_tracer_count == 0:
        return "insufficient_evidence"
    if completeness_bullet_count < COMPLETENESS_FLOOR:
        return "insufficient_evidence"
    if conditional_pass:
        return "corroborated"
    # claimed_only means "usable evidence exists but doesn't clear the
    # corroboration bar" -- NOT "exactly one tracer matched". A pair with
    # 3 distinct tracers all matched from a single bullet, or from bullets
    # that all share one source_field, lands here too (conditional_pass is
    # False for both cases). See docs/readiness/stage5_findings.md.
    return "claimed_only"


_EMPTY_GROUP = pd.DataFrame({
    "bullet_id": pd.Series(dtype="object"),
    "capability_id": pd.Series(dtype="object"),
    "domain": pd.Series(dtype="object"),
    "tracer_id": pd.Series(dtype="object"),
    "source_field": pd.Series(dtype="object"),
    "exclude_from_scoring": pd.Series(dtype="bool"),
    "provenance_status": pd.Series(dtype="object"),
})


def build_readiness_table(
    map_df: pd.DataFrame,
    bullets: pd.DataFrame,
    taxonomy: dict[str, Any],
    all_facility_ids: list[str],
) -> pd.DataFrame:
    """One row per (facility, capability) for EVERY facility x every locked
    capability -- not just pairs with a Stage 4 match. A facility with zero
    matches for a capability still gets an explicit insufficient_evidence (or
    claimed_only-eligible-but-empty) row, per the brief's own "data desert
    implemented at row level" requirement -- otherwise downstream code has to
    treat a missing row as a special case, which is fragile.
    """
    domain_totals = domain_totals_by_capability(taxonomy)
    completeness = bullets.groupby("facility_id").size().rename("completeness_bullet_count")
    groups = dict(iter(map_df.groupby(["facility_id", "capability_id"])))
    empty_scores = {
        capability_id: score_facility_capability(_EMPTY_GROUP, totals)
        for capability_id, totals in domain_totals.items()
    }

    rows = []
    for facility_id in all_facility_ids:
        bullet_count = int(completeness.get(facility_id, 0))
        for capability_id in sorted(domain_totals):
            group = groups.get((facility_id, capability_id))
            scored = (
                score_facility_capability(group, domain_totals[capability_id])
                if group is not None
                else empty_scores[capability_id]
            )
            rows.append({
                "facility_id": facility_id,
                "capability_id": capability_id,
                "domain_scores": json.dumps(scored["domain_scores"]),
                "readiness_score": scored["readiness_score"],
                "distinct_tracer_count": scored["distinct_tracer_count"],
                "distinct_bullet_count": scored["distinct_bullet_count"],
                "distinct_source_field_count": scored["distinct_source_field_count"],
                "conditional_pass": scored["conditional_pass"],
                "completeness_bullet_count": bullet_count,
                "verdict": verdict_for(scored["conditional_pass"], bullet_count, scored["distinct_tracer_count"]),
                "contradiction_flags": json.dumps(scored["contradiction_flags"]),
                "supporting_bullet_ids": json.dumps(scored["supporting_bullet_ids"]),
                "rejected_bullet_ids": json.dumps(scored["rejected_bullet_ids"]),
                "provenance_flags": json.dumps(scored["provenance_flags"]),
            })

    return pd.DataFrame(rows, columns=[
        "facility_id", "capability_id", "domain_scores", "readiness_score",
        "distinct_tracer_count", "distinct_bullet_count", "distinct_source_field_count",
        "conditional_pass", "completeness_bullet_count", "verdict", "contradiction_flags",
        "supporting_bullet_ids", "rejected_bullet_ids", "provenance_flags",
    ])


def contradiction_applicable_pair_count(map_df: pd.DataFrame) -> int:
    """Count of (facility, capability) pairs where a CONTRADICTION_RULES
    claim_tracer actually matched -- i.e. the rule could possibly fire.
    This is the correct denominator for a contradiction-rate percentage,
    NOT "every row for that capability" (most of those have no claim
    tracer at all, so the rule was never eligible to fire on them --
    counting them made an earlier version of this metric read as 49.8%
    when the real rate among eligible pairs is 85.7%)."""
    accepted = map_df[~map_df["exclude_from_scoring"]]
    applicable = 0
    for rule in CONTRADICTION_RULES:
        sub = accepted[accepted["capability_id"] == rule["capability_id"]]
        matched_sets = sub.groupby(["facility_id", "capability_id"])["tracer_id"].apply(set)
        applicable += int(matched_sets.apply(lambda tracers: bool(tracers & rule["claim_tracers"])).sum())
    return applicable


def metrics_for(readiness: pd.DataFrame, bullets: pd.DataFrame, map_df: pd.DataFrame) -> dict[str, Any]:
    verdict_counts = readiness["verdict"].value_counts().to_dict()
    contradiction_count = int((readiness["contradiction_flags"] != "[]").sum())
    applicable_count = contradiction_applicable_pair_count(map_df)
    capability_count = int(readiness["capability_id"].nunique())

    matched = readiness[readiness["distinct_tracer_count"] >= 1]
    matched_verdict_counts = matched["verdict"].value_counts().to_dict()

    def pct(counts: dict, key: str, total: int) -> float:
        return round(100 * counts.get(key, 0) / total, 2) if total else 0.0

    return {
        "method": "deterministic aggregation over Stage 4 bullet_capability_map (contract C2)",
        "total_facility_capability_pairs": len(readiness),
        "capability_count": capability_count,
        "note": (
            "full facility x capability cross product -- every facility has a row "
            f"for all {capability_count} capabilities, including zero-match pairs"
        ),
        "distinct_facilities": int(readiness["facility_id"].nunique()),
        "distinct_facilities_with_any_evidence_bullet": int(bullets["facility_id"].nunique()),
        "verdict_counts_full_population": verdict_counts,
        "pct_corroborated_full_population": pct(verdict_counts, "corroborated", len(readiness)),
        "pct_claimed_only_full_population": pct(verdict_counts, "claimed_only", len(readiness)),
        "pct_insufficient_evidence_full_population": pct(verdict_counts, "insufficient_evidence", len(readiness)),
        "matched_pairs_only": {
            "note": "restricted to pairs with >=1 Stage 4 tracer match, for comparability with pre-cross-product numbers",
            "count": len(matched),
            "verdict_counts": matched_verdict_counts,
            "pct_corroborated": pct(matched_verdict_counts, "corroborated", len(matched)),
            "pct_claimed_only": pct(matched_verdict_counts, "claimed_only", len(matched)),
            "pct_insufficient_evidence": pct(matched_verdict_counts, "insufficient_evidence", len(matched)),
        },
        "rows_with_contradiction_flags": contradiction_count,
        "contradiction_rule_applicable_pairs": applicable_count,
        "pct_contradiction_of_applicable_pairs": round(100 * contradiction_count / applicable_count, 2) if applicable_count else 0.0,
        "readiness_score_percentiles_matched_only": {
            str(p): round(matched["readiness_score"].quantile(p / 100), 4)
            for p in (25, 50, 75, 90, 95)
        },
        "completeness_floor": COMPLETENESS_FLOOR,
        "conditional_pass_rule": (
            "distinct_tracer_count >= 2 AND distinct_source_field_count >= 2 -- requires corroboration "
            "across independent source fields (not just independent bullets: two bullets split from the "
            "same source field are still one underlying claim). Recalibrated twice from the brief's "
            "original 80%-mean/no-zero-domain rule; see findings doc."
        ),
        "interpretation": (
            "readiness_score is informational (SARA-style, mean of domain scores); "
            "verdict is driven by completeness floor then conditional_pass, not by readiness_score directly. "
            "Every facility has a row for every capability -- a missing Stage 4 match always produces "
            "insufficient_evidence, never claimed_only, regardless of the facility's other evidence. "
            "claimed_only means usable evidence exists but doesn't clear the corroboration bar -- it is "
            "NOT limited to pairs with exactly one matched tracer; a pair with several tracers matched "
            "from a single bullet or a single source field also lands here."
        ),
    }


def main() -> None:
    taxonomy = load_taxonomy()
    map_df = pd.read_parquet(MAP_PATH)
    bullets = pd.read_parquet(BULLETS_PATH)
    facilities = pd.read_parquet(FACILITIES_PATH)
    all_facility_ids = sorted(facilities["unique_id"].astype(str).unique())

    readiness = build_readiness_table(map_df, bullets, taxonomy, all_facility_ids)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    readiness.to_parquet(OUT_PATH, index=False)

    metrics = metrics_for(readiness, bullets, map_df)
    (DOCS_DIR / "stage5_metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    print(f"Wrote {len(readiness):,} facility-capability rows to {OUT_PATH}")
    print(json.dumps(metrics, indent=2, default=str))


if __name__ == "__main__":
    main()
