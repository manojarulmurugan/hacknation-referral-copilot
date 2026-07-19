"""Stage 8 -- the one live-LLM path in this project, per the brief's original
design (deterministic matching was always meant for Stage 4's bulk
classification; Stage 8's live query parsing was always meant to be
LLM-based). Called by app/query.py ONLY as a fallback when the deterministic
parser (PhraseMatcher over normalization_vocab.json + city names) can't fully
resolve a query -- most queries, including both of the brief's own literal
examples, never reach this module at all.

Never on the critical path when the deterministic parser already succeeds;
never blocks the UI on failure -- the caller catches LLMParseError (and
anything else) and falls back to whatever the deterministic parser already
had. Reuses the exact call_databricks/backoff pattern proven working in
pipeline/experimental/stage4_llm_attempt.py, with a shorter timeout and
fewer retries since this sits on a live user-facing request, not a batch job.
"""

from __future__ import annotations

import json
import os
import random
import time

import requests
from dotenv import load_dotenv

from pipeline.stage4_taxonomy_mapping import LOCKED_CAPABILITY_IDS

load_dotenv()

MODEL = os.environ.get(
    "SERVING_ENDPOINT_NAME",
    "databricks-meta-llama-3-3-70b-instruct",
)
TIMEOUT_S = 12
MAX_RETRIES = 1  # one retry only -- bounds worst-case latency on a live fallback path


class LLMParseError(Exception):
    """Raised on any failure to get a validated parse. Callers must catch
    this (and generally Exception, defensively) and fall back silently."""


def _prompt(text: str) -> str:
    capability_ids = ", ".join(sorted(LOCKED_CAPABILITY_IDS))
    return (
        "Extract a single healthcare capability and a location from this user query.\n"
        f"Valid capability_id values (pick exactly one, or null if none clearly apply): {capability_ids}\n"
        'Return ONLY a JSON object: {"capability_id": <one of the ids above or null>, '
        '"location_text": <city/place name as free text, or null>}\n'
        'If the query implies urgency plus a specific surgical/medical need (e.g. "emergency '
        "surgery\"), prefer the specific capability (e.g. general_surgery) over a generic urgency word.\n"
        f'Query: "{text}"'
    )


def _auth_headers() -> tuple[str, dict[str, str]]:
    """Use an explicit local PAT when present, otherwise App OAuth."""
    host = os.environ.get("DATABRICKS_SERVER_HOSTNAME") or os.environ.get("DATABRICKS_HOST")
    token = os.environ.get("DATABRICKS_ACCESS_TOKEN")
    if host and token:
        return host.removeprefix("https://").rstrip("/"), {"Authorization": f"Bearer {token}"}

    try:
        from databricks.sdk import WorkspaceClient

        workspace = WorkspaceClient()
        sdk_host = workspace.config.host
        if not sdk_host:
            raise ValueError("Databricks host is not configured")
        return sdk_host.removeprefix("https://").rstrip("/"), workspace.config.authenticate()
    except Exception as exc:
        raise LLMParseError("Databricks credentials not configured") from exc


def _call(prompt: str, host: str, auth_headers: dict[str, str]) -> str:
    url = f"https://{host}/serving-endpoints/{MODEL}/invocations"
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.post(
                url,
                headers={**auth_headers, "Content-Type": "application/json"},
                json={
                    "messages": [
                        {"role": "system", "content": "Return strict JSON only, no prose."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0,
                    "max_tokens": 100,
                },
                timeout=TIMEOUT_S,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except (requests.exceptions.RequestException, KeyError, IndexError, ValueError) as exc:
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if attempt < MAX_RETRIES and (status == 429 or (status is not None and status >= 500)):
                time.sleep(min(2**attempt, 4) + random.uniform(0, 0.5))
                continue
            break
    raise LLMParseError(str(last_error))


def _extract_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise LLMParseError(f"No JSON object in model response: {text!r}")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMParseError(str(exc)) from exc


def parse_query_llm(text: str) -> dict:
    """Returns {"capability_id": str|None, "location_text": str|None}.
    Both fields are validated (capability_id must be one of the 20 locked
    ids; location_text must be a non-empty string) before being returned --
    the caller re-resolves location_text through the same deterministic
    city index used everywhere else, never trusting the LLM for geocoding
    itself, only for pulling the place-name phrase out of free text.
    Raises LLMParseError on any failure; callers must catch it."""
    host, auth_headers = _auth_headers()
    raw = _call(_prompt(text), host, auth_headers)
    parsed = _extract_json(raw)

    capability_id = parsed.get("capability_id")
    if capability_id not in LOCKED_CAPABILITY_IDS:
        capability_id = None

    location_text = parsed.get("location_text")
    if not isinstance(location_text, str) or not location_text.strip():
        location_text = None

    return {"capability_id": capability_id, "location_text": location_text}
