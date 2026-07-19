"""Tests for out-of-taxonomy raw-evidence search."""

from __future__ import annotations

import unittest

import pandas as pd

from app.fallback import extract_need_terms, search_raw_evidence


class RawEvidenceFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = pd.DataFrame(
            [
                {
                    "bullet_id": "b1",
                    "facility_id": "f1",
                    "source_field": "capability",
                    "text": "Provides dental surgery and emergency tooth care",
                    "text_norm": "provides dental surgery and emergency tooth care",
                },
                {
                    "bullet_id": "b2",
                    "facility_id": "f2",
                    "source_field": "procedure",
                    "text": "Dental implants and oral surgery",
                    "text_norm": "dental implants and oral surgery",
                },
                {
                    "bullet_id": "b3",
                    "facility_id": "f3",
                    "source_field": "capability",
                    "text": "Cardiac care only",
                    "text_norm": "cardiac care only",
                },
            ]
        )
        self.facilities = pd.DataFrame(
            [
                {
                    "unique_id": "f1",
                    "name": "Near Dental",
                    "address_city": "Madurai",
                    "address_stateOrRegion": "Tamil Nadu",
                    "latitude": 9.93,
                    "longitude": 78.12,
                },
                {
                    "unique_id": "f2",
                    "name": "Far Dental",
                    "address_city": "Chennai",
                    "address_stateOrRegion": "Tamil Nadu",
                    "latitude": 13.08,
                    "longitude": 80.27,
                },
                {
                    "unique_id": "f3",
                    "name": "Not Dental",
                    "address_city": "Madurai",
                    "address_stateOrRegion": "Tamil Nadu",
                    "latitude": 9.94,
                    "longitude": 78.13,
                },
            ]
        )

    def test_extracts_need_before_location(self) -> None:
        self.assertEqual(
            extract_need_terms("dental near Madurai", "Madurai, Tamil Nadu"),
            ["dental"],
        )

    def test_returns_only_literal_evidence_matches(self) -> None:
        result, terms = search_raw_evidence(
            self.evidence,
            self.facilities,
            "dental near Madurai",
            "Madurai, Tamil Nadu",
            9.9252,
            78.1198,
            sort_mode="nearest",
        )
        self.assertEqual(terms, ["dental"])
        self.assertEqual(set(result["facility_id"]), {"f1", "f2"})
        self.assertNotIn("f3", set(result["facility_id"]))
        self.assertEqual(result.iloc[0]["facility_id"], "f1")
        self.assertEqual(
            result.iloc[0]["raw_evidence_items"][0]["text"],
            "Provides dental surgery and emergency tooth care",
        )

    def test_multiple_terms_require_all_terms(self) -> None:
        result, _ = search_raw_evidence(
            self.evidence,
            self.facilities,
            "emergency dental near Madurai",
            "Madurai, Tamil Nadu",
            9.9252,
            78.1198,
        )
        self.assertEqual(set(result["facility_id"]), {"f1"})

    def test_no_match_returns_empty_with_terms(self) -> None:
        result, terms = search_raw_evidence(
            self.evidence,
            self.facilities,
            "burn rehabilitation near Madurai",
            "Madurai, Tamil Nadu",
            9.9252,
            78.1198,
        )
        self.assertTrue(result.empty)
        self.assertEqual(terms, ["burn", "rehabilitation"])


if __name__ == "__main__":
    unittest.main()
