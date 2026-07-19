"""Archived Stage 4 full-corpus LLM experiment.

This module preserves the core design of the abandoned July 19 experiment for
auditability. It is not imported by the active pipeline and must not be used as
Stage 5 input.

Why it was retired:
* 285,966 distinct texts / 30 per batch required about 9,533 model calls.
* Databricks Model Serving rate-limited concurrent calls.
* exhausted retries were cached as empty matches, conflating failure with
  "no evidence";
* the resulting map covered only a scoped/partial corpus.

The useful lessons retained here are strict output-schema checking, verbatim
quote validation, checkpointing, and bounded retries.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pandas as pd

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
}
LOCKED_DOMAINS = {"staff", "equipment", "procedures", "diagnostics"}
ARCHIVED_MODEL = "databricks-meta-llama-3-3-70b-instruct"
ARCHIVED_BATCH_SIZE = 30


def cache_key(batch: list[str], taxonomy_version: int) -> str:
    payload = {"batch": batch, "taxonomy_version": taxonomy_version}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records = {}
    for line in path.read_text().splitlines():
        if line.strip():
            record = json.loads(line)
            records[record["cache_key"]] = record
    return records


def append_cache(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_prompt(batch: list[str], checklist: str) -> str:
    bullets = "\n".join(f'[{index}] "{text}"' for index, text in enumerate(batch))
    return f"""Classify each bullet conservatively against the tracer checklist.
Return JSON mapping each numeric bullet index to zero or more objects containing
capability_id, domain, tracer_id, and an exact supporting_quote copied from the
bullet. Do not infer absent evidence. Return JSON only.

CHECKLIST:
{checklist}

BULLETS:
{bullets}
"""


def call_databricks(
    prompt: str,
    host: str,
    token: str,
    model: str = ARCHIVED_MODEL,
    max_retries: int = 6,
) -> str:
    request = urllib.request.Request(
        f"https://{host}/serving-endpoints/{model}/invocations",
        data=json.dumps(
            {
                "messages": [
                    {
                        "role": "system",
                        "content": "Return conservative quote-grounded JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                "max_tokens": 4000,
            }
        ).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code == 429 or exc.code >= 500:
                time.sleep(min(2**attempt, 30) + random.uniform(0, 1))
                continue
            raise
    if last_error is None:
        raise RuntimeError("Databricks request failed without an HTTP error")
    raise last_error


def extract_json_object(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("No JSON object in model response")
    return json.loads(text[start : end + 1])


def validate_output(
    raw_result: dict[str, Any],
    batch: list[str],
    valid_tracers: set[tuple[str, str, str]],
) -> dict[int, list[dict[str, str]]]:
    validated: dict[int, list[dict[str, str]]] = {}
    for raw_index, matches in raw_result.items():
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            continue
        if not 0 <= index < len(batch):
            continue
        kept = []
        for match in matches or []:
            identity = (
                match.get("capability_id", ""),
                match.get("domain", ""),
                match.get("tracer_id", ""),
            )
            quote = match.get("supporting_quote", "")
            if identity not in valid_tracers or not quote or quote not in batch[index]:
                continue
            kept.append(
                {
                    "capability_id": identity[0],
                    "domain": identity[1],
                    "tracer_id": identity[2],
                    "supporting_quote": quote,
                }
            )
        validated[index] = kept
    return validated


def materialize_partial_cache(
    cache_path: Path,
    bullets: pd.DataFrame,
    flags: pd.DataFrame,
) -> pd.DataFrame:
    """Reproduce the retired checkpoint materializer without writing output."""
    per_text_matches: dict[str, list[dict[str, str]]] = {}
    for record in load_cache(cache_path).values():
        for index, text in enumerate(record["batch"]):
            per_text_matches[text] = record["result"].get(str(index), [])

    rows = []
    for bullet in bullets.itertuples(index=False):
        for match in per_text_matches.get(bullet.text, []):
            rows.append(
                {
                    "bullet_id": bullet.bullet_id,
                    "facility_id": bullet.facility_id,
                    "source_field": bullet.source_field,
                    **match,
                    "llm_model": ARCHIVED_MODEL,
                }
            )
    columns = [
        "bullet_id",
        "facility_id",
        "source_field",
        "capability_id",
        "domain",
        "tracer_id",
        "supporting_quote",
        "llm_model",
    ]
    result = pd.DataFrame(rows, columns=columns)
    return result.merge(
        flags[["bullet_id", "status", "exclude_from_scoring"]].rename(
            columns={"status": "provenance_status"}
        ),
        on="bullet_id",
        how="left",
        validate="m:1",
    )


if __name__ == "__main__":
    raise SystemExit(
        "Archived experiment only. Run pipeline/stage4_taxonomy_mapping.py "
        "for the active deterministic Stage 4."
    )
