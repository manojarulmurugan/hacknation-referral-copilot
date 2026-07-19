"""Bounded official-source corroboration for Stage 4 facility-capability pairs.

This is deliberately not a full-dataset web crawler. It selects the strongest
candidate pairs per capability, reuses known source URLs, performs cached Tavily
searches when needed, and optionally asks OpenAI for quote-bound extraction.

Required live-call environment variables:
    TAVILY_API_KEY
    OPENAI_API_KEY
Optional:
    OPENAI_MODEL (default: gpt-4.1-mini)

Use ``--dry-run`` to verify candidate selection and queries without API calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATES = ROOT / "docs" / "preflight" / "stage4_sample_matches.csv"
DEFAULT_FACILITIES = ROOT / "data" / "facilities_local.parquet"
PROCESSED_DIR = ROOT / "data" / "processed"
CACHE_DIR = ROOT / "data" / "corroboration"
DOCS_DIR = ROOT / "docs" / "corroboration"

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

GOVERNMENT_DOMAINS = (
    ".gov.in",
    ".nic.in",
    "pmjay.gov.in",
    "nabh.co",
    "mohfw.gov.in",
)
WEAK_DIRECTORY_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "justdial.com",
    "practo.com",
    "sulekha.com",
    "youtube.com",
)


def load_env_file(path: Path) -> None:
    """Load missing keys from a simple .env file without logging values."""
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def parse_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [value] if value.startswith(("http://", "https://")) else []
    if isinstance(parsed, list):
        return [str(item) for item in parsed if item]
    return []


def normalized_domain(url: object) -> str:
    if not isinstance(url, str) or not url:
        return ""
    parsed = urllib.parse.urlparse(
        url if "://" in url else f"https://{url}"
    )
    return parsed.netloc.casefold().removeprefix("www.")


def source_tier(url: str, official_website: object = "") -> str:
    domain = normalized_domain(url)
    official_domain = normalized_domain(official_website)
    if not domain:
        return "D"
    if official_domain and (
        domain == official_domain or domain.endswith(f".{official_domain}")
    ):
        return "A_official_facility"
    if any(domain.endswith(item) or item in domain for item in GOVERNMENT_DOMAINS):
        return "A_government_or_registry"
    if domain.endswith((".edu", ".ac.in", ".org")):
        return "B_institutional"
    if any(item in domain for item in WEAK_DIRECTORY_DOMAINS):
        return "D_directory_or_social"
    return "C_other_web"


def read_candidates(path: Path) -> pd.DataFrame:
    if path.suffix.casefold() == ".parquet":
        frame = pd.read_parquet(path)
    else:
        frame = pd.read_csv(path)
    aliases = {
        "unique_id": "facility_id",
        "id": "facility_id",
        "capability": "capability_id",
    }
    frame = frame.rename(
        columns={key: value for key, value in aliases.items() if key in frame.columns}
    )
    required = {"facility_id", "capability_id"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(
            f"Candidate file must contain facility/capability IDs; missing {sorted(missing)}"
        )
    frame["facility_id"] = frame["facility_id"].astype(str)
    frame["capability_id"] = frame["capability_id"].astype(str)
    return frame


def select_bounded_candidates(
    matches: pd.DataFrame, per_capability: int, total_cap: int
) -> pd.DataFrame:
    aggregations: dict[str, tuple[str, Any]] = {
        "matched_rows": ("facility_id", "size"),
    }
    if "tracer_id" in matches.columns:
        aggregations["distinct_tracers"] = ("tracer_id", "nunique")
    if "domain" in matches.columns:
        aggregations["distinct_domains"] = ("domain", "nunique")
    grouped = (
        matches.groupby(["facility_id", "capability_id"], as_index=False)
        .agg(**aggregations)
    )
    for column in ("distinct_tracers", "distinct_domains"):
        if column not in grouped:
            grouped[column] = 0
    grouped = grouped.sort_values(
        [
            "capability_id",
            "distinct_domains",
            "distinct_tracers",
            "matched_rows",
            "facility_id",
        ],
        ascending=[True, False, False, False, True],
    )
    selected = grouped.groupby("capability_id", as_index=False).head(per_capability)
    return selected.head(total_cap).reset_index(drop=True)


def join_facility_metadata(
    candidates: pd.DataFrame, facilities_path: Path
) -> pd.DataFrame:
    columns = [
        "unique_id",
        "name",
        "address_city",
        "address_stateOrRegion",
        "officialWebsite",
        "websites",
        "source_urls",
    ]
    facilities = pd.read_parquet(facilities_path, columns=columns).drop_duplicates(
        "unique_id", keep="first"
    )
    facilities["facility_id"] = facilities["unique_id"].astype(str)
    facilities = facilities.rename(
        columns={
            "name": "facility_name",
            "address_city": "facility_city",
            "address_stateOrRegion": "facility_state",
            "officialWebsite": "official_website",
        }
    )
    return candidates.merge(
        facilities.drop(columns=["unique_id"]), on="facility_id", how="left"
    )


def build_query(row: object) -> str:
    parts = [
        f'"{row.facility_name}"',
        str(row.facility_city or ""),
        str(row.facility_state or ""),
        str(row.capability_id).replace("_", " "),
        "official",
    ]
    return " ".join(part for part in parts if part and part != "nan")


def known_sources(row: object) -> list[dict[str, str]]:
    urls = []
    urls.extend(parse_list(row.source_urls))
    urls.extend(parse_list(row.websites))
    if isinstance(row.official_website, str) and row.official_website:
        urls.append(row.official_website)
    deduped: dict[str, dict[str, str]] = {}
    for url in urls:
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"
        deduped[url] = {
            "url": url,
            "title": "Existing facility source",
            "content": "",
            "source_tier": source_tier(url, row.official_website),
            "origin": "existing_source_url",
        }
    return sorted(deduped.values(), key=lambda item: item["source_tier"])


def cache_key(facility_id: str, capability_id: str, query: str) -> str:
    value = f"{facility_id}|{capability_id}|{query}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    cache: dict[str, dict[str, Any]] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        cache[record["cache_key"]] = record
    return cache


def append_cache(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = 45,
) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def tavily_search(query: str, api_key: str, max_results: int = 5) -> list[dict[str, str]]:
    response = post_json(
        TAVILY_SEARCH_URL,
        {
            "api_key": api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        },
    )
    results = []
    for item in response.get("results", []):
        url = str(item.get("url", ""))
        if not url:
            continue
        results.append(
            {
                "url": url,
                "title": str(item.get("title", "")),
                "content": str(item.get("content", "")),
                "source_tier": source_tier(url),
                "origin": "tavily_search",
            }
        )
    return sorted(results, key=lambda item: item["source_tier"])


def extraction_prompt(row: object, sources: list[dict[str, str]]) -> str:
    source_text = "\n\n".join(
        f"SOURCE {index}\nURL: {item['url']}\nTIER: {item['source_tier']}\n"
        f"TITLE: {item['title']}\nTEXT: {item['content'][:3000]}"
        for index, item in enumerate(sources, start=1)
    )
    return f"""Determine whether the supplied sources explicitly corroborate the
following facility capability. Never infer from absence. A corroborating quote
must be copied exactly from SOURCE text and must clearly refer to this facility
or branch.

Facility: {row.facility_name}
City: {row.facility_city}
State: {row.facility_state}
Capability: {row.capability_id}

Return one JSON object with:
status: corroborated | unverified | identity_mismatch
exact_quote: exact supporting quote or empty string
source_url: supporting URL or empty string
source_tier: copied source tier or empty string
reason: concise explanation
facility_identity_match: true | false | null

{source_text}"""


def openai_extract(
    row: object,
    sources: list[dict[str, str]],
    api_key: str,
    model: str,
) -> dict[str, Any]:
    response = post_json(
        OPENAI_CHAT_URL,
        {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a conservative evidence extractor. Return JSON only. "
                        "Never manufacture a quote or treat not-found as false."
                    ),
                },
                {"role": "user", "content": extraction_prompt(row, sources)},
            ],
        },
        headers={"Authorization": f"Bearer {api_key}"},
    )
    content = response["choices"][0]["message"]["content"]
    result = json.loads(content)
    quote = str(result.get("exact_quote", ""))
    source_url = str(result.get("source_url", ""))
    source = next((item for item in sources if item["url"] == source_url), None)
    # Enforce quote binding locally; model output alone is not evidence.
    if quote and (source is None or quote not in source.get("content", "")):
        return {
            "status": "unverified",
            "exact_quote": "",
            "source_url": "",
            "source_tier": "",
            "reason": "Model quote was not present verbatim in retrieved source text.",
            "facility_identity_match": None,
        }
    return result


def dry_run_result(row: object, sources: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "status": "dry_run",
        "exact_quote": "",
        "source_url": "",
        "source_tier": "",
        "reason": (
            f"Prepared query and {len(sources)} existing source URL(s); "
            "no external calls executed."
        ),
        "facility_identity_match": None,
    }


def run(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    load_env_file(ROOT / ".env")
    tavily_key = os.environ.get("TAVILY_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
    if not args.dry_run and (not tavily_key or not openai_key):
        missing = [
            name
            for name, value in (
                ("TAVILY_API_KEY", tavily_key),
                ("OPENAI_API_KEY", openai_key),
            )
            if not value
        ]
        raise RuntimeError(
            f"Live corroboration requires {', '.join(missing)}. "
            "Use --dry-run to validate locally."
        )

    matches = read_candidates(args.candidates)
    selected = select_bounded_candidates(
        matches, per_capability=args.per_capability, total_cap=args.total_cap
    )
    candidates = join_facility_metadata(selected, args.facilities)
    cache_path = CACHE_DIR / "stage4_5_cache.jsonl"
    cache = load_cache(cache_path)
    output: list[dict[str, Any]] = []
    live_calls = 0

    for row in candidates.itertuples(index=False):
        query = build_query(row)
        key = cache_key(row.facility_id, row.capability_id, query)
        if key in cache and not args.refresh:
            output.append(cache[key]["result"])
            continue

        sources = known_sources(row)
        if args.dry_run:
            extraction = dry_run_result(row, sources)
        else:
            try:
                search_results = tavily_search(
                    query, tavily_key, max_results=args.max_results
                )
                live_calls += 1
                sources = sorted(
                    {item["url"]: item for item in [*sources, *search_results]}.values(),
                    key=lambda item: item["source_tier"],
                )
                extraction = openai_extract(
                    row, sources, api_key=openai_key, model=model
                )
                live_calls += 1
            except (urllib.error.URLError, KeyError, ValueError, json.JSONDecodeError) as exc:
                extraction = {
                    "status": "unverified",
                    "exact_quote": "",
                    "source_url": "",
                    "source_tier": "",
                    "reason": f"External corroboration failed safely: {type(exc).__name__}",
                    "facility_identity_match": None,
                }

        result = {
            "facility_id": row.facility_id,
            "facility_name": row.facility_name,
            "facility_city": row.facility_city,
            "capability_id": row.capability_id,
            "query": query,
            "matched_rows": int(row.matched_rows),
            "distinct_tracers": int(row.distinct_tracers),
            "distinct_domains": int(row.distinct_domains),
            "status": extraction.get("status", "unverified"),
            "exact_quote": extraction.get("exact_quote", ""),
            "source_url": extraction.get("source_url", ""),
            "source_tier": extraction.get("source_tier", ""),
            "reason": extraction.get("reason", ""),
            "facility_identity_match": extraction.get(
                "facility_identity_match"
            ),
            "known_source_count": len(sources),
        }
        output.append(result)
        if not args.dry_run:
            record = {
                "cache_key": key,
                "cached_at_epoch": time.time(),
                "result": result,
            }
            append_cache(cache_path, record)
            cache[key] = record

    frame = pd.DataFrame(output)
    status_counts = frame["status"].value_counts().to_dict() if len(frame) else {}
    summary = {
        "mode": "dry_run" if args.dry_run else "live",
        "candidate_pairs_available": int(
            matches[["facility_id", "capability_id"]].drop_duplicates().shape[0]
        ),
        "candidate_pairs_selected": len(candidates),
        "per_capability_cap": args.per_capability,
        "total_cap": args.total_cap,
        "live_api_calls": live_calls,
        "status_counts": status_counts,
        "policy": (
            "not_found/unverified is never interpreted as facility incapability; "
            "only verbatim quote-bound evidence can corroborate."
        ),
    }
    return frame, summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--facilities", type=Path, default=DEFAULT_FACILITIES)
    parser.add_argument("--per-capability", type=int, default=10)
    parser.add_argument("--total-cap", type=int, default=100)
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame, summary = run(args)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "_dry_run" if args.dry_run else ""
    frame.to_parquet(
        PROCESSED_DIR / f"external_corroboration{suffix}.parquet", index=False
    )
    (DOCS_DIR / f"stage4_5_summary{suffix}.json").write_text(
        json.dumps(summary, indent=2)
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
