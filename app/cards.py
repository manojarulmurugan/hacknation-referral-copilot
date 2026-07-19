"""Pure card-data assembly: a ranked (facility, capability) row -> a plain
dict ready for rendering. Kept separate from Dash/app.py so this is testable
without a browser or a running server.
"""

from __future__ import annotations

import json

import pandas as pd

from app.store import GEO_MISMATCH_THRESHOLD_KM

DOMAIN_ORDER = ["staff", "equipment", "procedures", "diagnostics"]

VERDICT_LABELS = {
    "corroborated": ("■", "Corroborated"),
    "claimed_only": ("□", "Claimed only"),
    "insufficient_evidence": ("–", "Insufficient evidence"),
}


def _loads(value) -> object:
    if isinstance(value, str):
        return json.loads(value)
    return value


def ordered_domain_scores(domain_scores: dict[str, float]) -> list[tuple[str, float]]:
    ordered = [(d, domain_scores[d]) for d in DOMAIN_ORDER if d in domain_scores]
    remaining = [(d, s) for d, s in domain_scores.items() if d not in DOMAIN_ORDER]
    return ordered + sorted(remaining)


def split_highlight(full_text: str, quote: str) -> tuple[str, str, str]:
    """Split full_text into (before, match, after) around quote's first
    case-insensitive occurrence, for rendering the matched substring
    highlighted within its real source sentence. If quote can't be found
    in full_text (shouldn't happen -- the matcher produced both from the
    same string -- but defensive), returns (full_text, "", "") so the
    caller still shows the full sentence, just unhighlighted."""
    if not quote:
        return full_text, "", ""
    idx = full_text.lower().find(quote.lower())
    if idx < 0:
        return full_text, "", ""
    return full_text[:idx], full_text[idx : idx + len(quote)], full_text[idx + len(quote):]


def _evidence_item(row, bullet_text_by_id: dict[str, str]) -> dict:
    quote = row.supporting_quote
    full_text = bullet_text_by_id.get(row.bullet_id, quote)
    before, match, after = split_highlight(full_text, quote)
    return {
        "tracer_id": row.tracer_id,
        "quote": quote,
        "full_text": full_text,
        "highlight_before": before,
        "highlight_match": match,
        "highlight_after": after,
        "source_field": row.source_field,
    }


def build_card_data(
    row: pd.Series, matches: pd.DataFrame, taxonomy: dict, bullet_text_by_id: dict[str, str] | None = None
) -> dict:
    bullet_text_by_id = bullet_text_by_id or {}
    domain_scores = _loads(row["domain_scores"]) or {}
    contradiction_flags = _loads(row["contradiction_flags"]) or []

    gaps = [domain for domain, score in domain_scores.items() if score == 0]

    pair = matches[
        (matches["facility_id"] == row["facility_id"]) & (matches["capability_id"] == row["capability_id"])
    ]
    accepted = pair[~pair["exclude_from_scoring"]]
    rejected = pair[pair["exclude_from_scoring"]]

    evidence_by_domain: dict[str, list[dict]] = {}
    for domain, group in accepted.groupby("domain"):
        evidence_by_domain[domain] = [_evidence_item(r, bullet_text_by_id) for r in group.itertuples(index=False)]

    rejected_evidence = [
        {**_evidence_item(r, bullet_text_by_id), "provenance_status": r.provenance_status}
        for r in rejected.itertuples(index=False)
    ]

    glyph, label = VERDICT_LABELS.get(row["verdict"], ("?", row["verdict"]))

    geo_mismatch_km = row.get("geo_mismatch_km")
    geo_mismatch = geo_mismatch_km is not None and pd.notna(geo_mismatch_km) and geo_mismatch_km >= GEO_MISMATCH_THRESHOLD_KM
    facility_type = row.get("facilityTypeId")
    facility_type = facility_type if facility_type is not None and pd.notna(facility_type) else None
    type_implausible = row.get("type_implausible", False)
    type_implausible = bool(type_implausible) if pd.notna(type_implausible) else False
    geo_discounted = row.get("geo_discounted", False)
    geo_discounted = bool(geo_discounted) if pd.notna(geo_discounted) else False

    return {
        "facility_id": row["facility_id"],
        "capability_id": row["capability_id"],
        "name": row.get("name"),
        "facility_type": facility_type,
        "type_implausible": type_implausible,
        "city": row.get("address_city"),
        "state": row.get("address_stateOrRegion"),
        "coordinate_city": row.get("coordinate_city"),
        "distance_km": row.get("distance_km"),
        "geo_mismatch": geo_mismatch,
        "geo_mismatch_km": geo_mismatch_km if geo_mismatch else None,
        "geo_discounted": geo_discounted,
        "verdict": row["verdict"],
        "verdict_glyph": glyph,
        "verdict_label": label,
        "domain_scores": ordered_domain_scores(domain_scores),
        "gaps": sorted(gaps),
        "readiness_score": row["readiness_score"],
        "distinct_tracer_count": row["distinct_tracer_count"],
        "distinct_bullet_count": row["distinct_bullet_count"],
        "distinct_source_field_count": row.get("distinct_source_field_count", 0),
        "completeness_bullet_count": row["completeness_bullet_count"],
        "composite_score": row.get("composite_score"),
        "evidence_component": row.get("evidence_component"),
        "proximity_component": row.get("proximity_component"),
        "contradiction_flags": contradiction_flags,
        "evidence_by_domain": evidence_by_domain,
        "rejected_evidence": rejected_evidence,
    }
