"""Unit tests for app/cards.py."""

from __future__ import annotations

import json
import unittest

import pandas as pd

from app.cards import build_card_data, ordered_domain_scores, split_highlight


class OrderedDomainScoresTests(unittest.TestCase):
    def test_known_domains_ordered_staff_equipment_procedures_diagnostics(self) -> None:
        scores = {"diagnostics": 0.5, "staff": 1.0, "procedures": 0.0, "equipment": 0.25}
        ordered = ordered_domain_scores(scores)
        self.assertEqual([domain for domain, _ in ordered], ["staff", "equipment", "procedures", "diagnostics"])


class SplitHighlightTests(unittest.TestCase):
    def test_splits_around_case_insensitive_match(self) -> None:
        before, match, after = split_highlight("The unit runs a 12-bed Hemodialysis Unit daily.", "hemodialysis unit")
        self.assertEqual(before, "The unit runs a 12-bed ")
        self.assertEqual(match, "Hemodialysis Unit")
        self.assertEqual(after, " daily.")

    def test_quote_not_found_returns_full_text_unhighlighted(self) -> None:
        before, match, after = split_highlight("Some unrelated sentence.", "hemodialysis")
        self.assertEqual(before, "Some unrelated sentence.")
        self.assertEqual(match, "")
        self.assertEqual(after, "")


class BuildCardDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matches = pd.DataFrame(
            [
                {
                    "bullet_id": "b1", "facility_id": "f1", "capability_id": "dialysis", "domain": "equipment",
                    "tracer_id": "dialysis_machine", "supporting_quote": "12-bed hemodialysis unit",
                    "source_field": "description", "exclude_from_scoring": False,
                    "provenance_status": "consistent_or_no_conflict",
                },
                {
                    "bullet_id": "b2", "facility_id": "f1", "capability_id": "dialysis", "domain": "staff",
                    "tracer_id": "nephrologist", "supporting_quote": "resident nephrologist on call",
                    "source_field": "description", "exclude_from_scoring": True,
                    "provenance_status": "suspected_conflict",
                },
            ]
        )
        self.row = pd.Series(
            {
                "facility_id": "f1", "capability_id": "dialysis", "name": "Apex Kidney Care",
                "address_city": "Jaipur", "address_stateOrRegion": "Rajasthan", "distance_km": 4.2,
                "verdict": "claimed_only",
                "domain_scores": json.dumps({"staff": 0.0, "equipment": 0.5, "procedures": 0.0, "diagnostics": 0.0}),
                "readiness_score": 0.125, "distinct_tracer_count": 1, "distinct_bullet_count": 1,
                "completeness_bullet_count": 6,
                "contradiction_flags": json.dumps([]),
            }
        )
        # b1 has a full-sentence entry (real case); b2 deliberately has none,
        # to exercise the fallback-to-quote path.
        self.bullet_text_by_id = {
            "b1": "The facility operates a 12-bed hemodialysis unit for chronic kidney patients.",
        }

    def test_gaps_are_zero_score_domains(self) -> None:
        data = build_card_data(self.row, self.matches, taxonomy={})
        self.assertEqual(set(data["gaps"]), {"staff", "procedures", "diagnostics"})

    def test_accepted_evidence_grouped_by_domain_excludes_rejected(self) -> None:
        data = build_card_data(self.row, self.matches, taxonomy={})
        self.assertIn("equipment", data["evidence_by_domain"])
        self.assertNotIn("staff", data["evidence_by_domain"])

    def test_rejected_evidence_surfaced_separately(self) -> None:
        data = build_card_data(self.row, self.matches, taxonomy={})
        self.assertEqual(len(data["rejected_evidence"]), 1)
        self.assertEqual(data["rejected_evidence"][0]["tracer_id"], "nephrologist")

    def test_verdict_label_maps_correctly(self) -> None:
        data = build_card_data(self.row, self.matches, taxonomy={})
        self.assertEqual(data["verdict_label"], "Claimed only")

    def test_evidence_item_carries_full_sentence_not_just_short_quote(self) -> None:
        data = build_card_data(self.row, self.matches, taxonomy={}, bullet_text_by_id=self.bullet_text_by_id)
        item = data["evidence_by_domain"]["equipment"][0]
        self.assertEqual(item["full_text"], self.bullet_text_by_id["b1"])
        self.assertGreater(len(item["full_text"]), len(item["quote"]))
        self.assertEqual(item["highlight_match"].lower(), "12-bed hemodialysis unit")

    def test_evidence_item_falls_back_to_quote_when_bullet_text_missing(self) -> None:
        data = build_card_data(self.row, self.matches, taxonomy={}, bullet_text_by_id=self.bullet_text_by_id)
        rejected_item = data["rejected_evidence"][0]  # b2, not in bullet_text_by_id
        self.assertEqual(rejected_item["full_text"], rejected_item["quote"])

    def test_geo_mismatch_below_threshold_not_flagged(self) -> None:
        row = self.row.copy()
        row["geo_mismatch_km"] = 10.0
        data = build_card_data(row, self.matches, taxonomy={})
        self.assertFalse(data["geo_mismatch"])
        self.assertIsNone(data["geo_mismatch_km"])

    def test_geo_mismatch_above_threshold_flagged(self) -> None:
        row = self.row.copy()
        row["geo_mismatch_km"] = 250.0
        data = build_card_data(row, self.matches, taxonomy={})
        self.assertTrue(data["geo_mismatch"])
        self.assertEqual(data["geo_mismatch_km"], 250.0)

    def test_geo_mismatch_nan_treated_as_not_flagged(self) -> None:
        row = self.row.copy()
        row["geo_mismatch_km"] = float("nan")
        data = build_card_data(row, self.matches, taxonomy={})
        self.assertFalse(data["geo_mismatch"])

    def test_type_implausible_and_coordinate_city_are_surfaced(self) -> None:
        row = self.row.copy()
        row["facilityTypeId"] = "dentist"
        row["type_implausible"] = True
        row["geo_mismatch_km"] = 250.0
        row["coordinate_city"] = "Tiruchirappalli"
        data = build_card_data(row, self.matches, taxonomy={})
        self.assertEqual(data["facility_type"], "dentist")
        self.assertTrue(data["type_implausible"])
        self.assertEqual(data["coordinate_city"], "Tiruchirappalli")


if __name__ == "__main__":
    unittest.main()
