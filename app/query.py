"""Pure query-parsing: free text -> (capability_id, city_label, origin lat/lon).

Deterministic, dependency-free -- reuses store.py's PhraseMatcher indexes.
Kept as pure functions (matcher + lookup dicts passed in explicitly, not a
whole FacilityStore) so tests can build tiny synthetic indexes without
loading any parquet files.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from pipeline.stage3_provenance_audit import PhraseMatcher, normalize


@dataclass
class ParsedQuery:
    raw_text: str
    capability_id: str | None
    capability_phrase: str | None
    city_label: str | None
    origin_lat: float | None
    origin_lon: float | None
    city_facility_count: int | None
    used_llm_fallback: bool = False
    llm_error: str | None = None


def _best_match(matcher: PhraseMatcher, text: str) -> str | None:
    """Longest-span match wins (PhraseMatcher.find returns every valid
    prefix match at every start position); ties broken by earliest start."""
    hits = matcher.find(normalize(text))
    if not hits:
        return None
    best = max(hits, key=lambda hit: (hit[1] - hit[0], -hit[0]))
    return best[2]


def resolve_capability(
    text: str, capability_matcher: PhraseMatcher, phrase_to_id: dict[str, str]
) -> tuple[str | None, str | None]:
    phrase = _best_match(capability_matcher, text)
    if phrase is None:
        return None, None
    return phrase_to_id[phrase], phrase


def resolve_city(
    text: str,
    city_matcher: PhraseMatcher,
    phrase_to_label: dict[str, str],
    centroids: dict[str, tuple[float, float, int]],
) -> tuple[str | None, float | None, float | None, int | None]:
    phrase = _best_match(city_matcher, text)
    if phrase is None:
        return None, None, None, None
    lat, lon, count = centroids[phrase]
    return phrase_to_label[phrase], lat, lon, count


def parse_query(text: str, store) -> ParsedQuery:
    """``store`` only needs to duck-type FacilityStore's matcher/index attrs."""
    capability_id, capability_phrase = resolve_capability(
        text, store.capability_matcher, store.capability_phrase_to_id
    )
    city_label, lat, lon, count = resolve_city(
        text, store.city_matcher, store.city_phrase_to_label, store.city_centroids
    )
    return ParsedQuery(
        raw_text=text,
        capability_id=capability_id,
        capability_phrase=capability_phrase,
        city_label=city_label,
        origin_lat=lat,
        origin_lon=lon,
        city_facility_count=count,
    )


def parse_query_with_fallback(text: str, store, llm_parser=None) -> ParsedQuery:
    """Stage 8: deterministic parse first (fast, free, covers the brief's
    own literal example queries with zero latency); only queries the
    deterministic parser can't fully resolve fall through to one live LLM
    call. ``llm_parser`` defaults to app.llm_query.parse_query_llm (imported
    lazily so pure-deterministic tests never need network/credentials);
    tests can inject a fake callable instead. Any LLM failure is caught and
    silently discarded -- the deterministic result (however partial) is
    always what gets returned, per Stage 8's own "never blocks the user"
    design in the brief.
    """
    parsed = parse_query(text, store)
    if parsed.capability_id is not None and parsed.city_label is not None:
        return parsed

    if llm_parser is None:
        from app.llm_query import parse_query_llm

        llm_parser = parse_query_llm

    try:
        llm_result = llm_parser(text)
    except Exception as exc:  # noqa: BLE001 -- never let an LLM failure block the UI
        return replace(parsed, llm_error=str(exc))

    capability_id, capability_phrase = parsed.capability_id, parsed.capability_phrase
    city_label, origin_lat, origin_lon, city_count = (
        parsed.city_label, parsed.origin_lat, parsed.origin_lon, parsed.city_facility_count
    )
    used_llm = False

    if capability_id is None and llm_result.get("capability_id") is not None:
        capability_id = llm_result["capability_id"]
        capability_phrase = f"AI-interpreted: {capability_id}"
        used_llm = True

    if city_label is None and llm_result.get("location_text") is not None:
        # Never trust the LLM for geocoding itself -- re-resolve the place
        # name it extracted through the same deterministic city index used
        # everywhere else, so the origin point is always a real centroid.
        resolved_label, lat, lon, count = resolve_city(
            llm_result["location_text"], store.city_matcher, store.city_phrase_to_label, store.city_centroids
        )
        if resolved_label is not None:
            city_label, origin_lat, origin_lon, city_count = resolved_label, lat, lon, count
            used_llm = True

    return ParsedQuery(
        raw_text=text,
        capability_id=capability_id,
        capability_phrase=capability_phrase,
        city_label=city_label,
        origin_lat=origin_lat,
        origin_lon=origin_lon,
        city_facility_count=city_count,
        used_llm_fallback=used_llm,
    )
