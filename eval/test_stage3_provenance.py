"""Regression tests for the Stage 3 provenance audit MVP."""

from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from pipeline.stage3_provenance_audit import (
    PhraseMatcher,
    distinctive_org_core,
    has_location_context,
    longest_matches,
    looks_like_named_org,
    normalize,
)

ROOT = Path(__file__).resolve().parent.parent
SUMMARY_PATH = ROOT / "data" / "processed" / "facility_provenance_summary.parquet"


class ProvenanceRuleTests(unittest.TestCase):
    def test_normalization_removes_formatting_noise(self) -> None:
        self.assertEqual(
            normalize("  Divine Touch &amp; Research—Centre  "),
            "divine touch and research centre",
        )

    def test_longest_place_match_suppresses_nested_city(self) -> None:
        matcher = PhraseMatcher(["mumbai", "navi mumbai"])
        self.assertEqual(
            longest_matches(matcher, "located in navi mumbai"),
            [(11, 22, "navi mumbai")],
        )

    def test_generic_healthcare_name_has_no_distinctive_core(self) -> None:
        self.assertEqual(distinctive_org_core("Dental Clinic"), ())
        self.assertEqual(
            distinctive_org_core("Shree Mahavir Multispeciality Hospital Pvt Ltd"),
            ("shree", "mahavir"),
        )

    def test_location_context_requires_an_assertion(self) -> None:
        direct = "services in palwal rural haryana"
        incidental = "participates in bhuj relief operation"
        self.assertTrue(has_location_context(direct, direct.index("palwal"), 19))
        self.assertFalse(has_location_context(incidental, incidental.index("bhuj"), 20))

    def test_title_cased_generic_words_can_form_a_named_org(self) -> None:
        self.assertTrue(
            looks_like_named_org(
                "Care Hospital installed the first dual-source CT", "care hospital"
            )
        )
        self.assertFalse(
            looks_like_named_org("A tertiary care hospital", "care hospital")
        )


@unittest.skipUnless(SUMMARY_PATH.exists(), "run the Stage 3 audit first")
class GeneratedArtifactRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.summary = pd.read_parquet(SUMMARY_PATH).set_index("facility_name")

    def test_confirmed_severe_examples_are_detected(self) -> None:
        expected = {
            "Wadhwa Pathology Lab",
            "Saravana Hospital",
            "Upasana Hospital",
            "Nizam's Institute of Medical Sciences",
            "Cosmos Hospital",
        }
        missing = {
            name
            for name in expected
            if name not in self.summary.index
            or not bool(
                self.summary.loc[[name], "has_provenance_conflict"].astype(bool).any()
            )
        }
        self.assertEqual(missing, set())

    def test_known_false_positive_examples_are_not_auto_rejected(self) -> None:
        expected_clean = {
            "Dr. Jairaj's Hospital",
            "Sadamangalam Ayurvedic Panchakarma Clinic & Fertiveda",
            "Nahar Medical Centre",
            "Divine Touch Dental Hospital & Amp Research Centre",
            "Kids Dental Clinic",
        }
        incorrectly_rejected = {
            name
            for name in expected_clean
            if name in self.summary.index
            and bool(
                self.summary.loc[[name], "has_provenance_conflict"].astype(bool).any()
            )
        }
        self.assertEqual(incorrectly_rejected, set())


if __name__ == "__main__":
    unittest.main()
