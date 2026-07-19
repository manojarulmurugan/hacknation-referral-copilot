"""Stage 3 MVP: conservative, non-destructive evidence provenance audit.

The audit processes every capability/procedure/equipment bullet and writes:

* data/processed/evidence_bullets.parquet
* data/processed/bullet_provenance_flags.parquet
* data/processed/facility_provenance_summary.parquet
* docs/provenance/stage3_metrics.json

It detects identity conflicts, not medical truth. A bullet is excluded from
downstream scoring only when a high-precision rule finds an explicit conflict.
All uncertain signals are retained with status ``review``.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import re
import unicodedata
import urllib.request
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_PATH = ROOT / "data" / "facilities_local.parquet"
PROCESSED_DIR = ROOT / "data" / "processed"
REFERENCE_DIR = ROOT / "data" / "reference" / "geonames"
DOCS_DIR = ROOT / "docs" / "provenance"
TIER_C_PATH = ROOT / "docs" / "preflight" / "tier_c_llm_extraction.json"

GEONAMES_URL = "https://download.geonames.org/export/dump/IN.zip"
GEONAMES_ZIP = REFERENCE_DIR / "IN.zip"
GEONAMES_TXT = "IN.txt"
BULLET_FIELDS = ("capability", "procedure", "equipment")

LEGAL_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "llc",
    "llp",
    "ltd",
    "limited",
    "pvt",
    "private",
    "plc",
}
HEALTHCARE_DESCRIPTORS = {
    "advanced",
    "care",
    "center",
    "centre",
    "clinic",
    "college",
    "dental",
    "general",
    "health",
    "healthcare",
    "hospital",
    "hospitals",
    "institute",
    "medical",
    "multispeciality",
    "multispecialty",
    "research",
    "speciality",
    "specialty",
    "super",
}
GENERIC_ORG_PHRASES = {
    "care hospital",
    "city hospital",
    "dental clinic",
    "general hospital",
    "hospital",
    "india hospital",
    "medical center",
    "medical centre",
    "medical college",
    "medical college hospital",
    "multispeciality hospital",
    "multispecialty hospital",
}
EXPLICIT_LOCATION_END = re.compile(
    r"(?:\b(?:based|located|situated|headquartered)\s+(?:at|in|near)|"
    r"\b(?:hospital|clinic|centre|center|facility)\s+(?:at|in|near)|"
    r"\b(?:services?|testing|treatment)\s+(?:at|in|near)|"
    r"\b(?:operates?|established)\s+in)\s*$"
)
OWNERSHIP_VERBS = (
    "has",
    "have",
    "provides",
    "provide",
    "offers",
    "offer",
    "installed",
    "operates",
    "is",
    "runs",
    "features",
)
CONTEXTUAL_CUES = re.compile(
    r"\b(?:affiliated|association|directory|empanelled|network|listed|trained|training|"
    r"worked|consultant|credential|graduate|degree|study|studied|insurance)\b"
)
BRANCH_CUES = re.compile(r"\b(?:branch|branches|locations|clinics|network)\b")


@dataclass(frozen=True)
class Place:
    geoname_id: int
    name: str
    latitude: float
    longitude: float
    feature_class: str
    feature_code: str
    admin1: str
    admin2: str


@dataclass
class Gazetteer:
    places: dict[int, Place]
    aliases: dict[str, tuple[int, ...]]
    automaton: "PhraseMatcher"
    source: str


class PhraseMatcher:
    """Dependency-free token trie for exact, longest-phrase matching."""

    END = "\0"

    def __init__(self, terms: Iterable[str]) -> None:
        self.trie: dict[str, dict] = {}
        for term in sorted(set(terms)):
            tokens = term.split()
            if not tokens:
                continue
            node = self.trie
            for token in tokens:
                node = node.setdefault(token, {})
            node[self.END] = term

    def find(self, text: str) -> list[tuple[int, int, str]]:
        token_matches = list(re.finditer(r"[a-z0-9]+", text))
        found: list[tuple[int, int, str]] = []
        for start_index, token_match in enumerate(token_matches):
            node = self.trie.get(token_match.group())
            if node is None:
                continue
            cursor = start_index
            while node is not None:
                term = node.get(self.END)
                if term is not None:
                    found.append(
                        (
                            token_match.start(),
                            token_matches[cursor].end(),
                            term,
                        )
                    )
                cursor += 1
                if cursor >= len(token_matches):
                    break
                node = node.get(token_matches[cursor].group())
        return found


def normalize(value: object) -> str:
    """Unicode-fold and normalize text for deterministic matching."""
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold().replace("&amp;", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def parse_json_array(value: object) -> list[str]:
    if isinstance(value, list):
        arr = value
    elif isinstance(value, str):
        try:
            arr = json.loads(value)
        except json.JSONDecodeError:
            return []
    else:
        return []
    if not isinstance(arr, list):
        return []
    return [item.strip() for item in arr if isinstance(item, str) and item.strip()]


def make_bullet_id(facility_id: str, source_field: str, position: int, text: str) -> str:
    raw = f"{facility_id}|{source_field}|{position}|{text}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def safe_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def valid_place_alias(alias: str) -> bool:
    return (
        len(alias) >= 3
        and any(ch.isalpha() for ch in alias)
        and not alias.isdigit()
        and len(alias.split()) <= 8
    )


def build_automaton(terms: Iterable[str]) -> PhraseMatcher:
    return PhraseMatcher(terms)


def longest_matches(
    automaton: PhraseMatcher, text: str
) -> list[tuple[int, int, str]]:
    """Return non-contained, word-boundary-safe matches."""
    candidates = automaton.find(text)

    # Prefer longer spans, then restore text order.
    selected: list[tuple[int, int, str]] = []
    for candidate in sorted(
        candidates, key=lambda item: (-(item[1] - item[0]), item[0])
    ):
        start, end, _ = candidate
        if any(start >= kept[0] and end <= kept[1] for kept in selected):
            continue
        selected.append(candidate)
    return sorted(selected)


def download_geonames(destination: Path = GEONAMES_ZIP) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    print(f"Downloading GeoNames India dump: {GEONAMES_URL}")
    request = urllib.request.Request(
        GEONAMES_URL, headers={"User-Agent": "hacknation-referral-copilot/1.0"}
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        destination.write_bytes(response.read())
    return destination


def load_geonames(path: Path = GEONAMES_ZIP) -> Gazetteer:
    places: dict[int, Place] = {}
    alias_ids: dict[str, set[int]] = defaultdict(set)
    with zipfile.ZipFile(path) as archive:
        with archive.open(GEONAMES_TXT) as raw:
            text_stream = io.TextIOWrapper(raw, encoding="utf-8")
            for line in text_stream:
                fields = line.rstrip("\n").split("\t")
                if len(fields) < 19:
                    continue
                feature_class, feature_code = fields[6], fields[7]
                if feature_class not in {"P", "A"}:
                    continue
                try:
                    geoname_id = int(fields[0])
                    latitude = float(fields[4])
                    longitude = float(fields[5])
                except ValueError:
                    continue
                place = Place(
                    geoname_id=geoname_id,
                    name=fields[1],
                    latitude=latitude,
                    longitude=longitude,
                    feature_class=feature_class,
                    feature_code=feature_code,
                    admin1=fields[10],
                    admin2=fields[11],
                )
                places[geoname_id] = place
                raw_aliases = [fields[1], fields[2], *fields[3].split(",")]
                for raw_alias in raw_aliases:
                    alias = normalize(raw_alias)
                    if valid_place_alias(alias):
                        alias_ids[alias].add(geoname_id)

    aliases = {key: tuple(sorted(value)) for key, value in alias_ids.items()}
    return Gazetteer(
        places=places,
        aliases=aliases,
        automaton=build_automaton(aliases),
        source="GeoNames IN.zip (CC BY 4.0)",
    )


def build_dataset_gazetteer(df: pd.DataFrame) -> Gazetteer:
    """Fallback gazetteer from dataset city names and robust centroids."""
    places: dict[int, Place] = {}
    aliases: dict[str, tuple[int, ...]] = {}
    work = df.copy()
    work["_city_norm"] = work["address_city"].map(normalize)
    work["_lat"] = pd.to_numeric(work["latitude"], errors="coerce")
    work["_lon"] = pd.to_numeric(work["longitude"], errors="coerce")
    work = work[
        work["_city_norm"].map(valid_place_alias)
        & work["_lat"].between(-90, 90)
        & work["_lon"].between(-180, 180)
    ]
    for index, (city, group) in enumerate(work.groupby("_city_norm"), start=1):
        place = Place(
            geoname_id=-index,
            name=city,
            latitude=float(group["_lat"].median()),
            longitude=float(group["_lon"].median()),
            feature_class="P",
            feature_code="DATASET",
            admin1="",
            admin2="",
        )
        places[place.geoname_id] = place
        aliases[city] = (place.geoname_id,)
    return Gazetteer(
        places=places,
        aliases=aliases,
        automaton=build_automaton(aliases),
        source="dataset city centroids (GeoNames fallback)",
    )


def restrict_gazetteer_to_facility_places(
    gazetteer: Gazetteer, facilities: pd.DataFrame
) -> Gazetteer:
    """Keep aliases only for places represented by facility address cities.

    The full GeoNames dump contains hundreds of thousands of tiny features whose
    names collide with ordinary words and personal names. It is a canonical
    resolver, not a safe text vocabulary by itself. Restricting it to places
    represented in this corpus preserves aliases while removing that noise.
    """
    selected_ids: set[int] = set()
    unresolved_cities: set[str] = set()
    for row in facilities.itertuples(index=False):
        city = normalize(row.address_city)
        if not valid_place_alias(city):
            continue
        place, _ = resolve_place(
            city,
            gazetteer,
            reference_lat=safe_float(row.latitude),
            reference_lon=safe_float(row.longitude),
        )
        if place is None:
            unresolved_cities.add(city)
        else:
            selected_ids.add(place.geoname_id)

    places = {
        geoname_id: place
        for geoname_id, place in gazetteer.places.items()
        if geoname_id in selected_ids
    }
    aliases: dict[str, tuple[int, ...]] = {}
    for alias, ids in gazetteer.aliases.items():
        retained = tuple(item for item in ids if item in selected_ids)
        if retained:
            aliases[alias] = retained

    # Preserve unresolved dataset cities using robust dataset-derived centroids.
    fallback = build_dataset_gazetteer(facilities)
    next_id = -1
    for city in sorted(unresolved_cities):
        fallback_ids = fallback.aliases.get(city, ())
        if not fallback_ids:
            continue
        fallback_place = fallback.places[fallback_ids[0]]
        place = Place(
            geoname_id=next_id,
            name=fallback_place.name,
            latitude=fallback_place.latitude,
            longitude=fallback_place.longitude,
            feature_class=fallback_place.feature_class,
            feature_code=fallback_place.feature_code,
            admin1="",
            admin2="",
        )
        places[next_id] = place
        aliases[city] = (next_id,)
        next_id -= 1

    return Gazetteer(
        places=places,
        aliases=aliases,
        automaton=build_automaton(aliases),
        source=f"{gazetteer.source}; restricted to facility-address places",
    )


def resolve_place(
    term: str,
    gazetteer: Gazetteer,
    reference_lat: float | None = None,
    reference_lon: float | None = None,
) -> tuple[Place | None, bool]:
    ids = gazetteer.aliases.get(term, ())
    candidates = [gazetteer.places[item] for item in ids if item in gazetteer.places]
    if not candidates:
        return None, True
    if len(candidates) == 1:
        return candidates[0], False
    if reference_lat is not None and reference_lon is not None:
        ranked = sorted(
            [
                (
                    haversine_km(
                        reference_lat,
                        reference_lon,
                        candidate.latitude,
                        candidate.longitude,
                    ),
                    candidate,
                )
                for candidate in candidates
            ],
            key=lambda item: (item[0], item[1].geoname_id),
        )
        if len(ranked) == 1 or ranked[1][0] - ranked[0][0] >= 25:
            return ranked[0][1], False
    return None, True


def distinctive_org_core(name: str) -> tuple[str, ...]:
    tokens = normalize(name).split()
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return tuple(
        token
        for token in tokens
        if token not in LEGAL_SUFFIXES and token not in HEALTHCARE_DESCRIPTORS
    )


def build_org_index(
    facilities: pd.DataFrame,
) -> tuple[PhraseMatcher, dict[str, dict[str, object]]]:
    entries: dict[str, dict[str, object]] = {}
    for row in facilities.itertuples(index=False):
        name = normalize(row.name)
        if len(name) < 4 or len(name.split()) > 18:
            continue
        core = distinctive_org_core(name)
        is_generic = name in GENERIC_ORG_PHRASES or not core
        # Generic phrases remain available for duplicate reinforcement only.
        if name not in entries:
            entries[name] = {
                "facility_ids": set(),
                "core": core,
                "generic": is_generic,
            }
        entries[name]["facility_ids"].add(str(row.unique_id))
    return build_automaton(entries), entries


def explode_bullets(facilities: pd.DataFrame) -> pd.DataFrame:
    records: list[tuple[object, ...]] = []
    for row in facilities.itertuples(index=False):
        facility_id = str(row.unique_id)
        for source_field in BULLET_FIELDS:
            for position, text in enumerate(
                parse_json_array(getattr(row, source_field))
            ):
                records.append(
                    (
                        make_bullet_id(facility_id, source_field, position, text),
                        facility_id,
                        row.name,
                        row.address_city,
                        row.address_stateOrRegion,
                        safe_float(row.latitude),
                        safe_float(row.longitude),
                        source_field,
                        position,
                        text,
                        normalize(text),
                    )
                )
    return pd.DataFrame(
        records,
        columns=[
            "bullet_id",
            "facility_id",
            "facility_name",
            "facility_city",
            "facility_state",
            "facility_latitude",
            "facility_longitude",
            "source_field",
            "position",
            "text",
            "text_norm",
        ],
    )


def duplicate_evidence_map(bullets: pd.DataFrame) -> dict[str, tuple[str, ...]]:
    eligible = bullets[
        bullets["text_norm"].str.len().ge(80)
        & bullets["text_norm"].str.split().str.len().ge(10)
    ]
    grouped = eligible.groupby("text_norm")["facility_id"].agg(
        lambda values: tuple(sorted(set(values)))
    )
    return {
        text: facilities
        for text, facilities in grouped.items()
        if len(facilities) > 1
    }


def has_location_context(text: str, start: int, end: int) -> bool:
    del end
    before = text[max(0, start - 55) : start]
    return bool(EXPLICIT_LOCATION_END.search(before))


def org_asserts_ownership(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 35) : start]
    after = text[end : min(len(text), end + 55)]
    if CONTEXTUAL_CUES.search(before) or CONTEXTUAL_CUES.search(after):
        return False
    verb_pattern = r"^\s*(?:" + "|".join(OWNERSHIP_VERBS) + r")\b"
    return bool(re.search(verb_pattern, after) or re.search(r"\b(?:by|at)\s*$", before))


def looks_like_named_org(original_text: str, normalized_term: str) -> bool:
    """Identify title-cased multiword names even when their words are generic."""
    tokens = normalized_term.split()
    if len(tokens) < 2:
        return False
    pattern = r"\b" + r"[\W_]+".join(re.escape(token) for token in tokens) + r"\b"
    match = re.search(pattern, original_text, flags=re.IGNORECASE)
    if match is None:
        return False
    words = re.findall(r"[A-Za-z]+", match.group())
    return len(words) >= 2 and all(word[:1].isupper() for word in words)


def audit_bullet(
    row: object,
    gazetteer: Gazetteer,
    org_automaton: PhraseMatcher,
    org_entries: dict[str, dict[str, object]],
    duplicate_map: dict[str, tuple[str, ...]],
) -> dict[str, object]:
    text = row.text_norm
    own_city = normalize(row.facility_city)
    own_name = normalize(row.facility_name)
    own_core = set(distinctive_org_core(own_name))
    latitude = safe_float(row.facility_latitude)
    longitude = safe_float(row.facility_longitude)

    own_place, _ = resolve_place(
        own_city, gazetteer, reference_lat=latitude, reference_lon=longitude
    )
    reasons: list[str] = []
    matched_places: list[dict[str, object]] = []
    matched_orgs: list[str] = []
    high_conflict = False
    review = False

    for start, end, term in longest_matches(gazetteer.automaton, text):
        if term == own_city:
            continue
        place, ambiguous = resolve_place(
            term, gazetteer, reference_lat=latitude, reference_lon=longitude
        )
        if ambiguous or place is None:
            if has_location_context(text, start, end):
                review = True
                reasons.append("ambiguous_place_mention")
            continue
        same_place = own_place is not None and place.geoname_id == own_place.geoname_id
        distance = (
            haversine_km(latitude, longitude, place.latitude, place.longitude)
            if latitude is not None and longitude is not None
            else None
        )
        matched_places.append(
            {
                "mention": term,
                "canonical": place.name,
                "geoname_id": place.geoname_id,
                "distance_km": round(distance, 1) if distance is not None else None,
            }
        )
        own_city_nearby = bool(
            own_city
            and own_city in text[max(0, start - 60) : min(len(text), end + 60)]
        )
        if same_place or own_city_nearby or (distance is not None and distance <= 50):
            continue
        if has_location_context(text, start, end):
            if BRANCH_CUES.search(text):
                review = True
                reasons.append("possible_branch_location")
            elif distance is not None and distance >= 75:
                high_conflict = True
                reasons.append("explicit_distant_location")
            else:
                review = True
                reasons.append("foreign_location_needs_review")

    org_matches = longest_matches(org_automaton, text)
    for start, end, term in org_matches:
        entry = org_entries[term]
        term_core = set(entry["core"])
        is_own = term == own_name or term in own_name or (
            own_core and term_core and len(own_core & term_core) / len(term_core) >= 0.8
        )
        if is_own:
            continue
        if not org_asserts_ownership(text, start, end):
            continue
        matched_orgs.append(term)
        if not entry["generic"]:
            high_conflict = True
            reasons.append("foreign_org_owns_claim")
        elif looks_like_named_org(row.text, term):
            high_conflict = True
            reasons.append("title_cased_foreign_org_owns_claim")
        else:
            review = True
            reasons.append("generic_foreign_org_needs_review")

    duplicate_facilities = duplicate_map.get(text, ())
    is_cross_facility_duplicate = len(duplicate_facilities) > 1
    if is_cross_facility_duplicate:
        if matched_orgs or high_conflict:
            high_conflict = True
            reasons.append("detailed_duplicate_reinforces_conflict")
        else:
            review = True
            reasons.append("detailed_cross_facility_duplicate")

    reasons = sorted(set(reasons))
    if high_conflict:
        status = "suspected_conflict"
    elif review:
        status = "review"
    else:
        status = "consistent_or_no_conflict"
    return {
        "bullet_id": row.bullet_id,
        "facility_id": row.facility_id,
        "status": status,
        "exclude_from_scoring": high_conflict,
        "reason_codes": json.dumps(reasons),
        "matched_places": json.dumps(matched_places),
        "matched_organizations": json.dumps(sorted(set(matched_orgs))),
        "duplicate_facility_ids": json.dumps(duplicate_facilities),
    }


def aggregate_facilities(
    facilities: pd.DataFrame, flags: pd.DataFrame
) -> pd.DataFrame:
    grouped = flags.groupby("facility_id", as_index=False).agg(
        total_bullets=("bullet_id", "count"),
        suspected_conflict_bullets=(
            "status",
            lambda values: int((values == "suspected_conflict").sum()),
        ),
        review_bullets=("status", lambda values: int((values == "review").sum())),
        rejected_bullet_ids=(
            "bullet_id",
            lambda values: json.dumps(
                flags.loc[values.index]
                .loc[flags.loc[values.index, "exclude_from_scoring"], "bullet_id"]
                .tolist()
            ),
        ),
    )
    grouped["has_provenance_conflict"] = (
        grouped["suspected_conflict_bullets"] > 0
    )
    metadata = facilities[
        ["unique_id", "name", "address_city", "address_stateOrRegion"]
    ].rename(columns={"unique_id": "facility_id", "name": "facility_name"})
    metadata["facility_id"] = metadata["facility_id"].astype(str)
    result = metadata.merge(grouped, on="facility_id", how="left")
    for column in (
        "total_bullets",
        "suspected_conflict_bullets",
        "review_bullets",
    ):
        result[column] = result[column].fillna(0).astype(int)
    result["rejected_bullet_ids"] = result["rejected_bullet_ids"].fillna("[]")
    result["has_provenance_conflict"] = (
        result["has_provenance_conflict"].fillna(False).astype(bool)
    )
    return result


def compare_tier_c(facility_summary: pd.DataFrame) -> dict[str, object] | None:
    if not TIER_C_PATH.exists():
        return None
    labels = pd.DataFrame(json.loads(TIER_C_PATH.read_text()))
    predictions = facility_summary[
        ["facility_id", "has_provenance_conflict"]
    ].rename(columns={"facility_id": "unique_id"})
    comparison = labels.merge(predictions, on="unique_id", how="left")
    comparison["has_provenance_conflict"] = comparison[
        "has_provenance_conflict"
    ].fillna(False)
    actual = comparison["llm_judged_genuine_contamination"].astype(bool)
    predicted = comparison["has_provenance_conflict"].astype(bool)
    tp = int((actual & predicted).sum())
    fp = int((~actual & predicted).sum())
    fn = int((actual & ~predicted).sum())
    tn = int((~actual & ~predicted).sum())
    return {
        "sample_size": len(comparison),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall_on_six_claude_confirmed_cases": round(tp / (tp + fn), 4)
        if tp + fn
        else None,
        "caveat": (
            "Biased development sample drawn from previously flagged rows; "
            "not an estimate of population prevalence."
        ),
    }


def metrics_for(
    facilities: pd.DataFrame,
    bullets: pd.DataFrame,
    flags: pd.DataFrame,
    summary: pd.DataFrame,
    gazetteer: Gazetteer,
) -> dict[str, object]:
    total_facilities = len(facilities)
    conflict_facilities = int(summary["has_provenance_conflict"].sum())
    review_facilities = int((summary["review_bullets"] > 0).sum())
    return {
        "method": "conservative deterministic provenance audit",
        "total_facilities": total_facilities,
        "total_bullets": len(bullets),
        "suspected_conflict_bullets": int(
            (flags["status"] == "suspected_conflict").sum()
        ),
        "review_bullets": int((flags["status"] == "review").sum()),
        "facilities_with_suspected_conflict": conflict_facilities,
        "suspected_conflict_facility_rate_pct": round(
            100 * conflict_facilities / total_facilities, 4
        ),
        "facilities_with_review_flags": review_facilities,
        "review_facility_rate_pct": round(
            100 * review_facilities / total_facilities, 4
        ),
        "gazetteer_source": gazetteer.source,
        "gazetteer_place_count": len(gazetteer.places),
        "gazetteer_alias_count": len(gazetteer.aliases),
        "tier_c_development_comparison": compare_tier_c(summary),
        "interpretation": (
            "The reported rate is the share of facilities flagged by this "
            "deterministic audit, not verified contamination prevalence."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_PATH)
    parser.add_argument(
        "--skip-geonames",
        action="store_true",
        help="Use dataset city centroids instead of downloading GeoNames.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    facilities_raw = pd.read_parquet(args.data)
    required = {
        "unique_id",
        "name",
        "address_city",
        "address_stateOrRegion",
        "latitude",
        "longitude",
        *BULLET_FIELDS,
    }
    missing = required - set(facilities_raw.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    duplicate_ids = facilities_raw["unique_id"].duplicated(keep=False)
    facilities = facilities_raw.drop_duplicates("unique_id", keep="first").copy()
    print(
        f"Loaded {len(facilities_raw):,} rows; auditing "
        f"{len(facilities):,} distinct facility IDs "
        f"({facilities_raw.loc[duplicate_ids, 'unique_id'].nunique()} duplicate IDs)."
    )

    if args.skip_geonames:
        gazetteer = build_dataset_gazetteer(facilities)
    else:
        try:
            gazetteer = restrict_gazetteer_to_facility_places(
                load_geonames(download_geonames()), facilities
            )
        except Exception as exc:  # deterministic fallback is preferable to aborting
            print(f"GeoNames unavailable ({exc!r}); using dataset centroids.")
            gazetteer = build_dataset_gazetteer(facilities)
    print(
        f"Gazetteer: {gazetteer.source}; "
        f"{len(gazetteer.places):,} places / {len(gazetteer.aliases):,} aliases."
    )

    bullets = explode_bullets(facilities)
    duplicate_map = duplicate_evidence_map(bullets)
    org_automaton, org_entries = build_org_index(facilities)
    print(
        f"Exploded {len(bullets):,} bullets; "
        f"{len(duplicate_map):,} detailed texts repeat across facilities."
    )

    flag_records = [
        audit_bullet(
            row, gazetteer, org_automaton, org_entries, duplicate_map
        )
        for row in bullets.itertuples(index=False)
    ]
    flags = pd.DataFrame(flag_records)
    facility_summary = aggregate_facilities(facilities, flags)
    metrics = metrics_for(facilities, bullets, flags, facility_summary, gazetteer)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    bullets.drop(columns=["text_norm"]).to_parquet(
        PROCESSED_DIR / "evidence_bullets.parquet", index=False
    )
    flags.to_parquet(PROCESSED_DIR / "bullet_provenance_flags.parquet", index=False)
    facility_summary.to_parquet(
        PROCESSED_DIR / "facility_provenance_summary.parquet", index=False
    )
    (DOCS_DIR / "stage3_metrics.json").write_text(
        json.dumps(metrics, indent=2, default=str)
    )
    print(json.dumps(metrics, indent=2, default=str))


if __name__ == "__main__":
    main()
