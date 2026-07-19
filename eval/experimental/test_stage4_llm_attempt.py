"""Archived tests for the retired full-corpus Stage 4 LLM experiment."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pipeline.experimental.stage4_llm_attempt import (
    append_cache,
    cache_key,
    extract_json_object,
    load_cache,
    validate_output,
)


class ArchivedLlmHarnessTests(unittest.TestCase):
    def test_cache_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.jsonl"
            record = {
                "cache_key": cache_key(["dialysis machine"], 1),
                "batch": ["dialysis machine"],
                "result": {"0": []},
            }
            append_cache(path, record)
            self.assertEqual(load_cache(path)[record["cache_key"]], record)

    def test_extracts_json_from_fenced_response(self) -> None:
        self.assertEqual(extract_json_object('```json\n{"0": []}\n```'), {"0": []})

    def test_requires_verbatim_quote_and_locked_tracer(self) -> None:
        batch = ["Has a dialysis machine"]
        valid = {("dialysis", "equipment", "dialysis_machine")}
        raw = {
            "0": [
                {
                    "capability_id": "dialysis",
                    "domain": "equipment",
                    "tracer_id": "dialysis_machine",
                    "supporting_quote": "dialysis machine",
                },
                {
                    "capability_id": "dialysis",
                    "domain": "equipment",
                    "tracer_id": "dialysis_machine",
                    "supporting_quote": "renal replacement centre",
                },
            ]
        }
        self.assertEqual(len(validate_output(raw, batch, valid)[0]), 1)


if __name__ == "__main__":
    unittest.main()
