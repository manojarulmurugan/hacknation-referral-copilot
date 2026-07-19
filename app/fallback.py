"""Transparent raw-evidence fallback for needs outside the scored taxonomy."""

from __future__ import annotations

import math
import re

import pandas as pd

from app.ranking import BANDS_KM, D0_KM, haversine_km
from pipeline.stage3_provenance_audit import normalize

STOPWORDS = {
    "a", "an", "and", "around", "at", "care", "find", "for", "hospital",
    "hospitals", "i", "in", "looking", "me", "my", "near", "need", "needs",
    "please", "service", "services", "somewhere", "the", "want",
}


def extract_need_terms(query_text: str, city_label: str | None = None) -> list[str]:
    """Extract conservative content terms, preferring text before 'near/in'."""
    normalized = normalize(query_text)
    prefix = re.split(r"\b(?:near|around|in)\b", normalized, maxsplit=1)[0]
    source = prefix or normalized
    city_tokens = set(normalize((city_label or "").split(",")[0]).split())
    return [
        token
        for token in source.split()
        if len(token) >= 3 and token not in STOPWORDS and token not in city_tokens
    ]


def search_raw_evidence(
    evidence_bullets: pd.DataFrame,
    facilities: pd.DataFrame,
    query_text: str,
    city_label: str,
    origin_lat: float,
    origin_lon: float,
    sort_mode: str = "best",
    limit: int = 20,
) -> tuple[pd.DataFrame, list[str]]:
    """Return facilities with raw bullet text containing every extracted term.

    These are explicitly not readiness-scored matches. Exact source bullets are
    carried through so the UI can show what matched and why.
    """
    terms = extract_need_terms(query_text, city_label)
    if not terms:
        return pd.DataFrame(), []

    pattern = "".join(f"(?=.*\\b{re.escape(term)}\\b)" for term in terms)
    matched = evidence_bullets[
        evidence_bullets["text_norm"].str.contains(pattern, regex=True, na=False)
    ].copy()
    if matched.empty:
        return pd.DataFrame(), terms

    matched = matched.drop_duplicates(["facility_id", "text"])
    counts = matched.groupby("facility_id").size().rename("raw_evidence_count")
    items = (
        matched.groupby("facility_id", sort=False)
        .apply(
            lambda group: group[["bullet_id", "source_field", "text"]]
            .head(5)
            .to_dict("records"),
            include_groups=False,
        )
        .rename("raw_evidence_items")
    )
    candidates = pd.concat([counts, items], axis=1).reset_index()
    candidates = candidates.merge(
        facilities,
        left_on="facility_id",
        right_on="unique_id",
        how="inner",
    ).dropna(subset=["latitude", "longitude"])
    if candidates.empty:
        return candidates, terms

    candidates["distance_km"] = candidates.apply(
        lambda row: haversine_km(
            origin_lat, origin_lon, row["latitude"], row["longitude"]
        ),
        axis=1,
    )

    band_km = None
    pool = candidates
    for band in BANDS_KM:
        within = candidates[candidates["distance_km"] <= band]
        if len(within) >= 5:
            band_km, pool = band, within
            break

    pool = pool.copy()
    pool["proximity_component"] = 1.0 / (
        1.0 + (pool["distance_km"] / D0_KM) ** 2
    )
    max_count = max(1, int(pool["raw_evidence_count"].max()))
    pool["evidence_component"] = pool["raw_evidence_count"].map(
        lambda count: math.log1p(count) / math.log1p(max_count)
    )
    pool["composite_score"] = (
        0.6 * pool["evidence_component"] + 0.4 * pool["proximity_component"]
    )

    if sort_mode == "nearest":
        pool = pool.sort_values("distance_km")
    elif sort_mode == "most_evidence":
        pool = pool.sort_values(
            ["raw_evidence_count", "distance_km"], ascending=[False, True]
        )
    else:
        pool = pool.sort_values(
            ["composite_score", "distance_km"], ascending=[False, True]
        )
    pool["search_band_km"] = band_km
    pool["search_widened"] = band_km != BANDS_KM[0]
    return pool.head(limit).reset_index(drop=True), terms
