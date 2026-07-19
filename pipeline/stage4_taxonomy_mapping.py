"""Stage 4: deterministic capability/tracer mapping over distinct bullets.

The active Stage 4 is local, reproducible, and makes no model/API calls. Each
distinct bullet text is matched once against executable taxonomy rules, then
matches are broadcast to every bullet_id sharing that text. Stage 3 provenance
flags are retained; rejected evidence remains visible but is excluded by Stage
5 through ``exclude_from_scoring``.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent
TAXONOMY_PATH = ROOT / "taxonomy" / "capability_taxonomy.yaml"
BULLETS_PATH = ROOT / "data" / "processed" / "evidence_bullets.parquet"
FLAGS_PATH = ROOT / "data" / "processed" / "bullet_provenance_flags.parquet"
OUT_PATH = ROOT / "data" / "processed" / "bullet_capability_map.parquet"
DOCS_DIR = ROOT / "docs" / "taxonomy"
METRICS_PATH = DOCS_DIR / "stage4_metrics.json"
VALIDATION_SAMPLE_PATH = DOCS_DIR / "stage4_validation_sample.csv"

LOCKED_CAPABILITY_IDS = {
    "dialysis",
    "icu",
    "maternity",
    "nicu",
    "emergency",
    "trauma",
    "oncology",
    "cardiac",
    "general_surgery",
    "blood_bank",
    "pediatric_intensive_care",
    "stroke_care",
    "neurosurgery",
    "orthopaedic_surgery",
    "respiratory_care",
    "gastroenterology",
    "urology",
    "ophthalmology",
    "diagnostic_imaging",
    "mental_health",
}
LOCKED_DOMAINS = {"staff", "equipment", "procedures", "diagnostics"}
MATCH_COLUMNS = [
    "text",
    "capability_id",
    "domain",
    "tracer_id",
    "supporting_quote",
    "matched_pattern",
]
OUTPUT_COLUMNS = [
    "bullet_id",
    "facility_id",
    "source_field",
    "capability_id",
    "domain",
    "tracer_id",
    "supporting_quote",
    "matched_pattern",
    "matcher_method",
    "provenance_status",
    "exclude_from_scoring",
]

NEGATION_BEFORE = re.compile(
    r"\b(?:no|not|without|lacks?|lacking|unavailable|does not|do not|"
    r"doesn't|don't|cannot|can't|never)\b",
    re.IGNORECASE,
)
THIRD_PARTY_BEFORE = re.compile(
    r"\b(?:underwent|referred to|referral to|nearby|nearest|external|outsourced to|"
    r"tie[- ]?up with|affiliated with)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Rule:
    capability_id: str
    domain: str
    tracer_id: str
    include_text: str
    include: re.Pattern[str]
    excludes: tuple[re.Pattern[str], ...]
    contexts: tuple[re.Pattern[str], ...]
    source_fields: frozenset[str]


def load_taxonomy(path: Path = TAXONOMY_PATH) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def iter_tracers(
    taxonomy: dict[str, Any],
) -> Iterable[tuple[str, str, dict[str, Any]]]:
    for capability_id, domains in taxonomy.get("capabilities", {}).items():
        for domain, tracers in domains.items():
            for tracer in tracers:
                yield capability_id, domain, tracer


def validate_taxonomy(taxonomy: dict[str, Any]) -> None:
    capability_ids = set(taxonomy.get("capabilities", {}))
    if capability_ids != LOCKED_CAPABILITY_IDS:
        raise ValueError(
            "Capability id mismatch. "
            f"Missing={sorted(LOCKED_CAPABILITY_IDS - capability_ids)}, "
            f"extra={sorted(capability_ids - LOCKED_CAPABILITY_IDS)}"
        )
    if set(taxonomy.get("domains", [])) != LOCKED_DOMAINS:
        raise ValueError("Taxonomy domains must be exactly the four locked SARA domains")

    identities: set[tuple[str, str]] = set()
    for capability_id, domains in taxonomy["capabilities"].items():
        if set(domains) - LOCKED_DOMAINS:
            raise ValueError(f"{capability_id} contains an unknown domain")
        seen_tracers: set[str] = set()
        for domain, tracers in domains.items():
            if not isinstance(tracers, list) or not tracers:
                raise ValueError(f"{capability_id}/{domain} must contain tracers")
            for tracer in tracers:
                tracer_id = tracer.get("tracer_id")
                if not tracer_id or tracer_id in seen_tracers:
                    raise ValueError(
                        f"Missing/duplicate tracer_id in {capability_id}: {tracer_id!r}"
                    )
                seen_tracers.add(tracer_id)
                if not tracer.get("description"):
                    raise ValueError(f"{capability_id}/{tracer_id} lacks description")
                patterns = tracer.get("include_patterns")
                if not isinstance(patterns, list) or not patterns:
                    raise ValueError(
                        f"{capability_id}/{tracer_id} requires include_patterns"
                    )
                identities.add((capability_id, tracer_id))
                for key in ("include_patterns", "exclude_patterns", "context_patterns"):
                    values = tracer.get(key, [])
                    if not isinstance(values, list):
                        raise ValueError(f"{capability_id}/{tracer_id} {key} must be a list")
                    for pattern in values:
                        try:
                            re.compile(pattern, re.IGNORECASE)
                        except re.error as exc:
                            raise ValueError(
                                f"Invalid {key} regex for {capability_id}/{tracer_id}: "
                                f"{pattern!r}: {exc}"
                            ) from exc
                source_fields = tracer.get("source_fields", [])
                if not isinstance(source_fields, list):
                    raise ValueError(
                        f"{capability_id}/{tracer_id} source_fields must be a list"
                    )
    if not identities:
        raise ValueError("Taxonomy contains no tracer identities")


def compile_rules(taxonomy: dict[str, Any]) -> list[Rule]:
    validate_taxonomy(taxonomy)
    rules: list[Rule] = []
    for capability_id, domain, tracer in iter_tracers(taxonomy):
        excludes = tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in tracer.get("exclude_patterns", [])
        )
        contexts = tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in tracer.get("context_patterns", [])
        )
        source_fields = frozenset(tracer.get("source_fields", []))
        for pattern in tracer["include_patterns"]:
            rules.append(
                Rule(
                    capability_id=capability_id,
                    domain=domain,
                    tracer_id=tracer["tracer_id"],
                    include_text=pattern,
                    include=re.compile(pattern, re.IGNORECASE),
                    excludes=excludes,
                    contexts=contexts,
                    source_fields=source_fields,
                )
            )
    return rules


def _blocked_by_context(text: str, match: re.Match[str], rule: Rule) -> bool:
    if any(pattern.search(text) for pattern in rule.excludes):
        return True
    if rule.contexts and not any(pattern.search(text) for pattern in rule.contexts):
        return True

    prefix = text[max(0, match.start() - 55) : match.start()]
    if NEGATION_BEFORE.search(prefix):
        return True
    if THIRD_PARTY_BEFORE.search(prefix):
        return True
    return False


def match_text(text: object, rules: list[Rule]) -> list[dict[str, str]]:
    if not isinstance(text, str) or not text.strip():
        return []
    matches: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for rule in rules:
        identity = (rule.capability_id, rule.domain, rule.tracer_id)
        if identity in seen:
            continue
        match = rule.include.search(text)
        if match is None or _blocked_by_context(text, match, rule):
            continue
        matches.append(
            {
                "capability_id": rule.capability_id,
                "domain": rule.domain,
                "tracer_id": rule.tracer_id,
                "supporting_quote": match.group(0),
                "matched_pattern": rule.include_text,
                "_source_fields": json.dumps(sorted(rule.source_fields)),
            }
        )
        seen.add(identity)
    return matches


def build_distinct_match_index(
    bullets: pd.DataFrame,
    rules: list[Rule],
) -> tuple[pd.DataFrame, dict[str, int]]:
    required = {"bullet_id", "facility_id", "source_field", "text"}
    missing = required - set(bullets.columns)
    if missing:
        raise ValueError(f"evidence_bullets is missing columns: {sorted(missing)}")
    if bullets["bullet_id"].duplicated().any():
        raise ValueError("evidence_bullets contains duplicate bullet_id values")

    distinct_texts = bullets["text"].drop_duplicates()
    rows = []
    for text in distinct_texts:
        for match in match_text(text, rules):
            rows.append({"text": text, **match})

    columns = MATCH_COLUMNS + ["_source_fields"]
    index = pd.DataFrame(rows, columns=columns)
    summary = {
        "input_bullets": int(len(bullets)),
        "distinct_texts_scanned": int(len(distinct_texts)),
        # pandas.Series.nunique under-counts this corpus by one because two
        # unequal strings share a hash; drop_duplicates preserves exact equality.
        "distinct_texts_expected": int(len(bullets["text"].drop_duplicates())),
        "distinct_texts_with_matches": int(index["text"].nunique()) if len(index) else 0,
    }
    if summary["distinct_texts_scanned"] != summary["distinct_texts_expected"]:
        raise AssertionError("Distinct-text scan did not cover the complete input")
    return index, summary


def _source_field_allowed(source_field: object, encoded_allowed: str) -> bool:
    allowed = json.loads(encoded_allowed)
    return not allowed or str(source_field) in allowed


def build_capability_map(
    bullets: pd.DataFrame,
    flags: pd.DataFrame,
    taxonomy: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, int]]:
    rules = compile_rules(taxonomy)
    distinct_matches, scan_summary = build_distinct_match_index(bullets, rules)

    if distinct_matches.empty:
        result = pd.DataFrame(columns=OUTPUT_COLUMNS)
        return result, scan_summary

    broadcast = bullets[["bullet_id", "facility_id", "source_field", "text"]].merge(
        distinct_matches,
        on="text",
        how="inner",
        validate="m:m",
    )
    allowed = [
        _source_field_allowed(source_field, encoded)
        for source_field, encoded in zip(
            broadcast["source_field"], broadcast["_source_fields"]
        )
    ]
    broadcast = broadcast.loc[allowed].copy()
    broadcast["matcher_method"] = "deterministic_regex_v1"

    required_flag_columns = {"bullet_id", "status", "exclude_from_scoring"}
    missing_flag_columns = required_flag_columns - set(flags.columns)
    if missing_flag_columns:
        raise ValueError(
            f"bullet_provenance_flags is missing columns: {sorted(missing_flag_columns)}"
        )
    if flags["bullet_id"].duplicated().any():
        raise ValueError("bullet_provenance_flags contains duplicate bullet_id values")

    result = broadcast.merge(
        flags[["bullet_id", "status", "exclude_from_scoring"]].rename(
            columns={"status": "provenance_status"}
        ),
        on="bullet_id",
        how="left",
        validate="m:1",
    )
    missing_flags = int(result["provenance_status"].isna().sum())
    if missing_flags:
        raise ValueError(f"{missing_flags} matched bullets lack Stage 3 flags")
    result["exclude_from_scoring"] = result["exclude_from_scoring"].astype(bool)
    result = result[OUTPUT_COLUMNS].drop_duplicates().reset_index(drop=True)

    duplicate_key = ["bullet_id", "capability_id", "domain", "tracer_id"]
    if result.duplicated(duplicate_key).any():
        raise AssertionError("Duplicate bullet/tracer matches remain after deduplication")
    if not set(result["capability_id"]).issubset(LOCKED_CAPABILITY_IDS):
        raise AssertionError("Output contains an unlocked capability")
    if not set(result["domain"]).issubset(LOCKED_DOMAINS):
        raise AssertionError("Output contains an unlocked domain")
    return result, scan_summary


def sample_for_validation(
    map_df: pd.DataFrame,
    bullets: pd.DataFrame,
    seed: int = 42,
    per_tracer: int = 3,
) -> pd.DataFrame:
    columns = [
        "bullet_id",
        "facility_id",
        "source_field",
        "text",
        "capability_id",
        "domain",
        "tracer_id",
        "supporting_quote",
        "matched_pattern",
        "exclude_from_scoring",
        "review_label",
        "review_notes",
    ]
    if map_df.empty:
        return pd.DataFrame(columns=columns)

    review = map_df.merge(
        bullets[["bullet_id", "text"]],
        on="bullet_id",
        how="left",
        validate="m:1",
    ).drop_duplicates(["capability_id", "tracer_id", "text"])
    sampled_groups = []
    for _, group in review.groupby(["capability_id", "tracer_id"], sort=True):
        sampled_groups.append(
            group.sample(min(per_tracer, len(group)), random_state=seed)
        )
    sampled = pd.concat(sampled_groups, ignore_index=True)
    sampled["review_label"] = ""
    sampled["review_notes"] = ""
    return sampled.reindex(columns=columns)


def _nested_counts(
    frame: pd.DataFrame,
    columns: list[str],
) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    counts = frame.groupby(columns, dropna=False).size().rename("match_rows")
    records = []
    for key, count in counts.items():
        values = key if isinstance(key, tuple) else (key,)
        records.append(
            {
                **{column: value for column, value in zip(columns, values)},
                "match_rows": int(count),
            }
        )
    return records


def metrics_for(
    bullets: pd.DataFrame,
    map_df: pd.DataFrame,
    scan_summary: dict[str, int],
    validation_sample: pd.DataFrame,
    taxonomy_version: int = 1,
) -> dict[str, Any]:
    warnings = []
    if not map_df.empty:
        tracer_counts = map_df.groupby(["capability_id", "tracer_id"]).size()
        for capability_id, capability_rows in map_df.groupby("capability_id"):
            top_identity, top_count = max(
                (
                    (identity, int(count))
                    for identity, count in tracer_counts.loc[capability_id].items()
                ),
                key=lambda item: item[1],
            )
            share = top_count / len(capability_rows)
            if top_count >= 100 and share >= 0.70:
                warnings.append(
                    f"{capability_id}/{top_identity} contributes {share:.1%} "
                    "of capability match rows; inspect for pattern dominance."
                )

    return {
        "status": "complete",
        "method": "deterministic distinct-text regex mapping",
        "taxonomy_version": taxonomy_version,
        "total_input_bullets": int(len(bullets)),
        "distinct_bullet_texts": int(len(bullets["text"].drop_duplicates())),
        **scan_summary,
        "complete_distinct_text_coverage": (
            scan_summary["distinct_texts_scanned"]
            == scan_summary["distinct_texts_expected"]
        ),
        "matched_output_rows": int(len(map_df)),
        "bullets_with_at_least_one_match": int(map_df["bullet_id"].nunique()),
        "facilities_with_at_least_one_match": int(map_df["facility_id"].nunique()),
        "matched_rows_excluded_by_stage3": int(
            map_df["exclude_from_scoring"].sum()
        ),
        "counts_by_capability": _nested_counts(map_df, ["capability_id"]),
        "counts_by_domain": _nested_counts(map_df, ["domain"]),
        "counts_by_tracer": _nested_counts(
            map_df, ["capability_id", "domain", "tracer_id"]
        ),
        "validation_sample_rows": int(len(validation_sample)),
        "validation_sample_seed": 42,
        "validation_sample_per_tracer_max": 3,
        "validation_claim": (
            "Bounded review sample for precision-oriented error inspection only; "
            "it does not establish population recall."
        ),
        "warnings": warnings,
    }


def verify_artifact(
    bullets: pd.DataFrame,
    flags: pd.DataFrame,
    map_df: pd.DataFrame,
    scan_summary: dict[str, int],
) -> None:
    if scan_summary["distinct_texts_scanned"] != len(
        bullets["text"].drop_duplicates()
    ):
        raise AssertionError("Not every distinct input text was scanned")
    if map_df.empty:
        raise AssertionError("Deterministic mapper produced no capability matches")

    bullet_text = bullets.set_index("bullet_id")["text"]
    if any(
        quote not in bullet_text.loc[bullet_id]
        for bullet_id, quote in zip(map_df["bullet_id"], map_df["supporting_quote"])
    ):
        raise AssertionError("At least one supporting quote is not verbatim")
    flags_by_id = flags.set_index("bullet_id")["exclude_from_scoring"].astype(bool)
    expected = map_df["bullet_id"].map(flags_by_id)
    if not expected.equals(map_df["exclude_from_scoring"].astype(bool)):
        raise AssertionError("Stage 3 exclusion flags were not preserved")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taxonomy", type=Path, default=TAXONOMY_PATH)
    parser.add_argument("--bullets", type=Path, default=BULLETS_PATH)
    parser.add_argument("--flags", type=Path, default=FLAGS_PATH)
    parser.add_argument("--output", type=Path, default=OUT_PATH)
    parser.add_argument("--validation-per-tracer", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    taxonomy = load_taxonomy(args.taxonomy)
    bullets = pd.read_parquet(args.bullets)
    flags = pd.read_parquet(args.flags)
    distinct_text_count = len(bullets["text"].drop_duplicates())
    print(
        f"Loaded {len(bullets):,} bullets / "
        f"{distinct_text_count:,} distinct texts."
    )

    map_df, scan_summary = build_capability_map(bullets, flags, taxonomy)
    verify_artifact(bullets, flags, map_df, scan_summary)
    validation_sample = sample_for_validation(
        map_df,
        bullets,
        per_tracer=args.validation_per_tracer,
    )
    metrics = metrics_for(
        bullets,
        map_df,
        scan_summary,
        validation_sample,
        taxonomy_version=int(taxonomy.get("version", 1)),
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    map_df.to_parquet(args.output, index=False)
    validation_sample.to_csv(VALIDATION_SAMPLE_PATH, index=False)
    METRICS_PATH.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
