"""Pure ranking: (readiness rows, facilities, origin) -> ordered candidate frame.

Never hard-filters by verdict -- a facility with insufficient_evidence is
still shown, ranked lower, clearly labeled (brief's own recommendation,
original Stage 9 open question). The only rows excluded are ones missing
lat/lon entirely (1.2% of facilities) -- a distance-based list has no
honest way to place them; that's a data-completeness limit, not a trust
judgement.
"""

from __future__ import annotations

import math

import pandas as pd

EARTH_RADIUS_KM = 6371.0088

VERDICT_RANK = {"corroborated": 0, "claimed_only": 1, "insufficient_evidence": 2}
VERDICT_BASE = {"corroborated": 1.0, "claimed_only": 0.5, "insufficient_evidence": 0.0}

W_EVIDENCE = 0.6
D0_KM = 30.0
GEO_CONFIDENCE_FACTOR = 0.5
TYPE_PENALTY_FACTOR = 0.2
IMPLAUSIBLE_TYPES = {"dentist", "pharmacy", "farmacy"}

# Search-radius widening: try the tightest band first, widen only if it
# doesn't have enough evidence-bearing facilities. Applies to BOTH sort
# modes -- confirmed live bug this fixes: "most evidence" mode had no
# distance cap at all and could return a facility 1,558km away as the top
# result, which isn't useful for an actual referral decision. Final
# fallback (falling off the end of BANDS_KM) is nationwide/uncapped, so a
# genuinely rare capability still returns something rather than nothing.
BANDS_KM = [50.0, 150.0, 300.0, 600.0]
MIN_EVIDENCE_RESULTS = 5


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def rank_candidates(
    readiness: pd.DataFrame,
    facilities: pd.DataFrame,
    capability_id: str,
    origin_lat: float,
    origin_lon: float,
    sort_mode: str = "nearest",
    limit: int = 20,
) -> pd.DataFrame:
    pool = readiness[readiness["capability_id"] == capability_id]
    merged = pool.merge(facilities, left_on="facility_id", right_on="unique_id", how="inner")
    merged = merged.dropna(subset=["latitude", "longitude"])
    if merged.empty:
        return merged

    merged = merged.copy()
    merged["distance_km"] = merged.apply(
        lambda row: haversine_km(origin_lat, origin_lon, row["latitude"], row["longitude"]), axis=1
    )
    merged["verdict_rank"] = merged["verdict"].map(VERDICT_RANK).fillna(3)

    readiness_score = (
        pd.to_numeric(merged["readiness_score"], errors="coerce").fillna(0.0)
        if "readiness_score" in merged
        else pd.Series(0.0, index=merged.index)
    )
    merged["evidence_component"] = (
        0.5 * merged["verdict"].map(VERDICT_BASE).fillna(0.0) + 0.5 * readiness_score
    )
    merged["proximity_component"] = 1.0 / (1.0 + (merged["distance_km"] / D0_KM) ** 2)

    # Import here to avoid a module cycle: store imports haversine_km while
    # constructing its city index.
    from app.store import GEO_MISMATCH_THRESHOLD_KM

    geo_mismatch = (
        pd.to_numeric(merged["geo_mismatch_km"], errors="coerce")
        if "geo_mismatch_km" in merged
        else pd.Series(float("nan"), index=merged.index)
    )
    merged["geo_discounted"] = geo_mismatch.ge(GEO_MISMATCH_THRESHOLD_KM)
    merged.loc[merged["geo_discounted"], "proximity_component"] *= GEO_CONFIDENCE_FACTOR

    facility_type = (
        merged["facilityTypeId"].fillna("").astype(str).str.strip().str.lower()
        if "facilityTypeId" in merged
        else pd.Series("", index=merged.index)
    )
    merged["type_implausible"] = facility_type.isin(IMPLAUSIBLE_TYPES)
    merged["composite_score"] = (
        W_EVIDENCE * merged["evidence_component"]
        + (1.0 - W_EVIDENCE) * merged["proximity_component"]
    )
    merged.loc[merged["type_implausible"], "composite_score"] *= TYPE_PENALTY_FACTOR

    band_km = None  # None means nationwide (no band satisfied the threshold)
    candidate_pool = merged
    for band in BANDS_KM:
        within_band = merged[merged["distance_km"] <= band]
        if (within_band["distinct_tracer_count"] >= 1).sum() >= MIN_EVIDENCE_RESULTS:
            band_km = band
            candidate_pool = within_band
            break
    search_widened = band_km != BANDS_KM[0]

    has_evidence = candidate_pool[candidate_pool["distinct_tracer_count"] >= 1].copy()
    zero_evidence = candidate_pool[candidate_pool["distinct_tracer_count"] == 0].copy()

    if sort_mode == "best":
        has_evidence = has_evidence.sort_values(
            by=["composite_score", "distance_km"], ascending=[False, True]
        )
    elif sort_mode == "most_evidence":
        has_evidence = has_evidence.sort_values(
            by=["verdict_rank", "distinct_tracer_count", "distance_km"], ascending=[True, False, True]
        )
    else:
        has_evidence = has_evidence.sort_values(by="distance_km", ascending=True)
    zero_evidence = zero_evidence.sort_values(by="distance_km", ascending=True)

    # Evidence-bearing facilities always fill the list first (never buried
    # by pure distance -- second-opinion review catch, reproduced live: an
    # unfixed "nearest" query returned 5/5 zero-evidence facilities ahead of
    # any evidenced one). Zero-evidence facilities only backfill remaining
    # slots -- a "clearly separated fallback when the region is genuinely
    # thin," not silently hidden (the brief's own "honest desert" language),
    # and app.py renders them under a distinct heading using is_evidence_tier.
    has_evidence["is_evidence_tier"] = True
    zero_evidence["is_evidence_tier"] = False
    remaining = max(0, limit - len(has_evidence))
    result = pd.concat([has_evidence.head(limit), zero_evidence.head(remaining)], ignore_index=True)
    result["search_band_km"] = band_km
    result["search_widened"] = search_widened

    return result
