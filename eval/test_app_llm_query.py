"""Unit tests for app/llm_query.py -- the Stage 8 LLM fallback.

Mocks requests.post throughout: this file must never require network access
or real Databricks credentials to pass. Live connectivity (the actual
Databricks Model Serving endpoint, with real ambiguous/composite queries)
was verified manually and is not re-verified by this offline suite.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from app.llm_query import LLMParseError, _auth_headers, _extract_json, parse_query_llm


def _fake_response(content: str, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    response.raise_for_status.return_value = None
    return response


class ExtractJsonTests(unittest.TestCase):
    def test_extracts_json_object_from_surrounding_text(self) -> None:
        result = _extract_json('Sure, here it is: {"capability_id": "dialysis", "location_text": "Pune"} thanks')
        self.assertEqual(result, {"capability_id": "dialysis", "location_text": "Pune"})

    def test_no_json_object_raises_llm_parse_error(self) -> None:
        with self.assertRaises(LLMParseError):
            _extract_json("no json here")


@patch.dict(os.environ, {"DATABRICKS_SERVER_HOSTNAME": "fake-host", "DATABRICKS_ACCESS_TOKEN": "fake-token"})
class ParseQueryLLMTests(unittest.TestCase):
    @patch("app.llm_query.requests.post")
    def test_valid_response_parsed_and_returned(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _fake_response(json.dumps({"capability_id": "cardiac", "location_text": "Kochi"}))
        result = parse_query_llm("open heart surgery near Kochi")
        self.assertEqual(result, {"capability_id": "cardiac", "location_text": "Kochi"})

    @patch("app.llm_query.requests.post")
    def test_capability_id_not_in_locked_set_is_dropped(self, mock_post: MagicMock) -> None:
        """Never trust the model's capability_id verbatim -- must be one of
        the 10 locked ids or None, even if the model hallucinates something
        else entirely."""
        mock_post.return_value = _fake_response(json.dumps({"capability_id": "orthopedics", "location_text": "Pune"}))
        result = parse_query_llm("bone surgery near Pune")
        self.assertIsNone(result["capability_id"])
        self.assertEqual(result["location_text"], "Pune")

    @patch("app.llm_query.requests.post")
    def test_blank_location_text_becomes_none(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _fake_response(json.dumps({"capability_id": "dialysis", "location_text": "  "}))
        result = parse_query_llm("dialysis somewhere")
        self.assertIsNone(result["location_text"])

    @patch("app.llm_query.requests.post")
    def test_malformed_json_raises_llm_parse_error_not_crash(self, mock_post: MagicMock) -> None:
        mock_post.return_value = _fake_response("not json at all")
        with self.assertRaises(LLMParseError):
            parse_query_llm("dialysis near Jaipur")

    def test_missing_credentials_raises_llm_parse_error(self) -> None:
        with patch.dict(os.environ, {"DATABRICKS_SERVER_HOSTNAME": "", "DATABRICKS_ACCESS_TOKEN": ""}):
            with self.assertRaises(LLMParseError):
                parse_query_llm("dialysis near Jaipur")


class AuthHeadersTests(unittest.TestCase):
    @patch("app.llm_query.os.environ.get")
    def test_explicit_local_token_is_preferred(self, mock_get: MagicMock) -> None:
        values = {
            "DATABRICKS_SERVER_HOSTNAME": "https://fake-host/",
            "DATABRICKS_ACCESS_TOKEN": "fake-token",
        }
        mock_get.side_effect = values.get
        host, headers = _auth_headers()
        self.assertEqual(host, "fake-host")
        self.assertEqual(headers["Authorization"], "Bearer fake-token")

    @patch("app.llm_query._auth_headers")
    @patch("app.llm_query.requests.post")
    def test_sdk_auth_headers_can_drive_parse(
        self, mock_post: MagicMock, mock_auth: MagicMock
    ) -> None:
        mock_auth.return_value = ("workspace.cloud.databricks.com", {"Authorization": "Bearer oauth"})
        mock_post.return_value = _fake_response(
            json.dumps({"capability_id": "dialysis", "location_text": "Jaipur"})
        )
        result = parse_query_llm("kidney care near Jaipur")
        self.assertEqual(result["capability_id"], "dialysis")
        self.assertEqual(
            mock_post.call_args.kwargs["headers"]["Authorization"],
            "Bearer oauth",
        )


if __name__ == "__main__":
    unittest.main()
