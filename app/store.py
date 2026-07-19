"""Loads facility/readiness/evidence tables once at process startup.

Reuses pipeline.stage3_provenance_audit's PhraseMatcher/normalize (already
proven at 451k-row scale) for both capability-keyword and city resolution --
no new matching algorithm is introduced here.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from app.ranking import haversine_km
from pipeline.stage3_provenance_audit import PhraseMatcher, normalize
from pipeline.stage4_taxonomy_mapping import LOCKED_CAPABILITY_IDS
from pipeline.stage5_readiness_scoring import load_taxonomy

# A facility whose own address_city centroid sits farther than this from its
# own recorded lat/lon is flagged as geo-inconsistent -- e.g. a facility
# labeled address_city="Chennai" whose coordinates actually sit next to
# Trichy (confirmed real case in this dataset, ~250km off). This is a
# distinct contamination signal from Stage 3's bullet-text provenance
# audit: it's a structured-field-vs-coordinate mismatch, not a free-text
# cross-facility mention. 75km comfortably covers real metro-area sprawl
# (a facility in a Chennai suburb is not "wrong") while catching a facility
# that's clearly nowhere near the city it claims.
GEO_MISMATCH_THRESHOLD_KM = 75.0

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("REFERRAL_DATA_DIR", ROOT / "data"))
TAXONOMY_DIR = Path(os.environ.get("REFERRAL_TAXONOMY_DIR", ROOT / "taxonomy"))

FACILITY_COLUMNS = [
    "unique_id", "name", "facilityTypeId", "address_city", "address_stateOrRegion", "latitude", "longitude",
]


def read_parquet_artifact(relative_path: str) -> pd.DataFrame:
    """Read a local single parquet or its partitioned deploy equivalent."""
    path = DATA_DIR / relative_path
    if path.is_file():
        return pd.read_parquet(path)
    partitioned = path.with_suffix("")
    if partitioned.is_dir():
        parts = sorted(partitioned.glob("*.parquet"))
        if not parts:
            raise FileNotFoundError(f"No parquet parts found under {partitioned}")
        return pd.concat((pd.read_parquet(part) for part in parts), ignore_index=True)
    raise FileNotFoundError(f"Missing data artifact: {path}")


@dataclass
class FacilityStore:
    facilities: pd.DataFrame
    readiness: pd.DataFrame
    matches: pd.DataFrame
    taxonomy: dict
    capability_matcher: PhraseMatcher
    capability_phrase_to_id: dict[str, str]
    city_matcher: PhraseMatcher
    city_phrase_to_label: dict[str, str]
    city_centroids: dict[str, tuple[float, float, int]]
    bullet_text_by_id: dict[str, str]
    evidence_bullets: pd.DataFrame


# Composite queries where a bare capability word alone would resolve to the
# wrong (or a merely partial) capability -- e.g. "emergency surgery near
# Patna" (one of only two example queries in the hackathon brief itself)
# would otherwise resolve to `emergency` alone, silently dropping "surgery".
# These phrases are longer than any single-capability match at the same
# start position, so the existing longest-span tie-break in
# app/query.py's _best_match() picks them automatically once present in
# the trie -- no separate priority logic needed.
CAPABILITY_PHRASE_OVERRIDES: dict[str, str] = {
    "surgery": "general_surgery",
    "emergency surgery": "general_surgery",
}


def build_capability_index(vocab: dict) -> tuple[PhraseMatcher, dict[str, str]]:
    """One phrase -> capability_id lookup, built from normalization_vocab.json
    plus the capability_id's own words (so "dialysis" alone resolves)."""
    phrase_to_id: dict[str, str] = {}
    for capability_id, phrases in vocab.items():
        if capability_id.startswith("_") or capability_id not in LOCKED_CAPABILITY_IDS:
            continue
        candidates = [capability_id.replace("_", " ")] + list(phrases)
        for phrase in candidates:
            norm = normalize(phrase)
            if norm:
                phrase_to_id[norm] = capability_id
    for phrase, capability_id in CAPABILITY_PHRASE_OVERRIDES.items():
        phrase_to_id[normalize(phrase)] = capability_id
    matcher = PhraseMatcher(phrase_to_id.keys())
    return matcher, phrase_to_id


def build_city_index(facilities: pd.DataFrame) -> tuple[PhraseMatcher, dict[str, str], dict[str, tuple]]:
    """One phrase (normalized city name) -> (display label, centroid) lookup.

    Ambiguous city names (same city text in multiple states) are resolved
    deterministically to whichever (city, state) pair has the most
    facilities -- a documented simplification, not silent arbitrariness.
    """
    valid = facilities.dropna(subset=["address_city", "latitude", "longitude"]).copy()
    valid["norm_city"] = valid["address_city"].map(normalize)
    valid = valid[valid["norm_city"] != ""]

    grouped = valid.groupby(["norm_city", "address_stateOrRegion"], dropna=False)
    stats = grouped.agg(
        lat=("latitude", "mean"),
        lon=("longitude", "mean"),
        count=("unique_id", "size"),
        display_city=("address_city", lambda s: s.mode().iat[0]),
    ).reset_index()
    stats = stats.sort_values("count", ascending=False)
    best = stats.drop_duplicates(subset="norm_city", keep="first")

    phrase_to_label: dict[str, str] = {}
    centroids: dict[str, tuple[float, float, int]] = {}
    for row in best.itertuples(index=False):
        state = row.address_stateOrRegion if isinstance(row.address_stateOrRegion, str) and row.address_stateOrRegion else ""
        label = f"{row.display_city}, {state}" if state else row.display_city
        phrase_to_label[row.norm_city] = label
        centroids[row.norm_city] = (float(row.lat), float(row.lon), int(row.count))

    matcher = PhraseMatcher(phrase_to_label.keys())
    return matcher, phrase_to_label, centroids, stats


def compute_geo_mismatch_km(facilities: pd.DataFrame, city_state_stats: pd.DataFrame) -> pd.Series:
    """For each facility, the distance between its own recorded lat/lon and
    the centroid of every OTHER facility that shares its exact (city, state)
    label -- i.e. "does this facility's own address agree with its own
    coordinates?" Compared against the facility's own (city, state) group
    specifically (not just the single most-populous group for an ambiguous
    city name, which is all city_centroids keeps) so this stays correct even
    for ambiguous city names. NaN where address_city/lat/lon is missing or
    the city has no other facilities to compare against."""
    key = pd.Series(
        list(zip(facilities["address_city"].map(normalize), facilities["address_stateOrRegion"])),
        index=facilities.index,
    )
    lookup = {
        (row.norm_city, row.address_stateOrRegion): (row.lat, row.lon)
        for row in city_state_stats.itertuples(index=False)
    }

    def distance_for(idx) -> float:
        row = facilities.loc[idx]
        if pd.isna(row["latitude"]) or pd.isna(row["longitude"]):
            return float("nan")
        centroid = lookup.get(key.loc[idx])
        if centroid is None:
            return float("nan")
        return haversine_km(row["latitude"], row["longitude"], centroid[0], centroid[1])

    return pd.Series([distance_for(idx) for idx in facilities.index], index=facilities.index)


def compute_coordinate_city(
    facilities: pd.DataFrame,
    city_state_stats: pd.DataFrame,
    threshold_km: float = GEO_MISMATCH_THRESHOLD_KM,
) -> pd.Series:
    """Nearest internal city-cluster label for geo-mismatched facilities.

    Uses every (city, state) centroid produced by build_city_index and no
    external geocoder. Consistent facilities retain None because their
    address label is already the most honest location description.
    """
    result_values = np.empty(len(facilities), dtype=object)
    result_values.fill(None)
    result = pd.Series(result_values, index=facilities.index, dtype=object)
    if "geo_mismatch_km" not in facilities or city_state_stats.empty:
        return result

    centroid_rows = city_state_stats.dropna(subset=["lat", "lon"])
    mismatch = (
        facilities["geo_mismatch_km"].ge(threshold_km)
        & facilities["latitude"].notna()
        & facilities["longitude"].notna()
    )
    if not mismatch.any() or centroid_rows.empty:
        return result

    facility_lat = np.radians(facilities.loc[mismatch, "latitude"].to_numpy(dtype=float))[:, None]
    facility_lon = np.radians(facilities.loc[mismatch, "longitude"].to_numpy(dtype=float))[:, None]
    centroid_lat = np.radians(centroid_rows["lat"].to_numpy(dtype=float))[None, :]
    centroid_lon = np.radians(centroid_rows["lon"].to_numpy(dtype=float))[None, :]

    dlat = centroid_lat - facility_lat
    dlon = centroid_lon - facility_lon
    a = np.sin(dlat / 2.0) ** 2 + np.cos(facility_lat) * np.cos(centroid_lat) * np.sin(dlon / 2.0) ** 2
    distances = 2.0 * np.arcsin(np.minimum(1.0, np.sqrt(a)))
    nearest = distances.argmin(axis=1)
    labels = centroid_rows["display_city"].fillna(centroid_rows["norm_city"]).to_numpy(dtype=object)
    result.loc[mismatch] = labels[nearest]
    return result


def load_store() -> FacilityStore:
    facilities = read_parquet_artifact("facilities_local.parquet")[FACILITY_COLUMNS].copy()
    facilities["unique_id"] = facilities["unique_id"].astype(str)

    readiness = read_parquet_artifact("processed/facility_capability_readiness.parquet")
    readiness["facility_id"] = readiness["facility_id"].astype(str)

    matches = read_parquet_artifact("processed/bullet_capability_map.parquet")
    matches["facility_id"] = matches["facility_id"].astype(str)

    # bullet_capability_map.supporting_quote is only the matched substring
    # (avg ~13 chars, e.g. "ICU", "dialysis machine") -- cards need the full
    # source sentence for a real row-level citation (second-opinion review
    # catch). evidence_bullets.text has that (avg ~43 chars); bullet_id is
    # the shared join key.
    evidence_bullets = read_parquet_artifact(
        "processed/evidence_bullets.parquet"
    )[["bullet_id", "facility_id", "source_field", "text"]].copy()
    evidence_bullets["facility_id"] = evidence_bullets["facility_id"].astype(str)
    evidence_bullets["text_norm"] = evidence_bullets["text"].map(normalize)
    bullet_text_by_id = dict(zip(evidence_bullets["bullet_id"], evidence_bullets["text"]))

    taxonomy = load_taxonomy(TAXONOMY_DIR / "capability_taxonomy.yaml")
    vocab = json.loads((TAXONOMY_DIR / "normalization_vocab.json").read_text(encoding="utf-8"))

    capability_matcher, capability_phrase_to_id = build_capability_index(vocab)
    city_matcher, city_phrase_to_label, city_centroids, city_state_stats = build_city_index(facilities)

    # Confirmed real case in this dataset: a facility labeled
    # address_city="Chennai" with coordinates that actually sit next to
    # Trichy (~250km off) -- ranking by coordinates is still correct
    # distance math, but the card's displayed city looked wrong to a user
    # searching "near Trichy". Flag it instead of silently trusting either
    # field.
    facilities["geo_mismatch_km"] = compute_geo_mismatch_km(facilities, city_state_stats)
    facilities["coordinate_city"] = compute_coordinate_city(facilities, city_state_stats)

    return FacilityStore(
        facilities=facilities,
        readiness=readiness,
        matches=matches,
        taxonomy=taxonomy,
        capability_matcher=capability_matcher,
        capability_phrase_to_id=capability_phrase_to_id,
        city_matcher=city_matcher,
        city_phrase_to_label=city_phrase_to_label,
        city_centroids=city_centroids,
        bullet_text_by_id=bullet_text_by_id,
        evidence_bullets=evidence_bullets,
    )
