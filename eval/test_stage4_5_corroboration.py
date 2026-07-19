"""Tests for bounded Stage 4.5 external corroboration."""

from __future__ import annotations

import unittest

import pandas as pd

from pipeline.stage4_5_external_corroboration import (
    normalized_domain,
    select_bounded_candidates,
    source_tier,
)


class ExternalCorroborationTests(unittest.TestCase):
    def test_domain_normalization(self) -> None:
        self.assertEqual(
            normalized_domain("https://www.example.org/path"), "example.org"
        )

    def test_official_website_is_highest_tier(self) -> None:
        self.assertEqual(
            source_tier(
                "https://hospital.example.org/dialysis",
                "https://hospital.example.org",
            ),
            "A_official_facility",
        )
        self.assertEqual(
            source_tier("https://facility.pmjay.gov.in/item"),
            "A_government_or_registry",
        )

    def test_social_and_directory_sources_are_weak(self) -> None:
        self.assertEqual(
            source_tier("https://www.practo.com/hospital/example"),
            "D_directory_or_social",
        )

    def test_candidate_selection_obeys_both_caps(self) -> None:
        rows = []
        for capability in ("dialysis", "icu"):
            for index in range(5):
                rows.append(
                    {
                        "facility_id": f"{capability}-{index}",
                        "capability_id": capability,
                        "tracer_id": f"tracer-{index}",
                        "domain": "equipment",
                    }
                )
        selected = select_bounded_candidates(
            pd.DataFrame(rows), per_capability=2, total_cap=3
        )
        self.assertEqual(len(selected), 3)
        self.assertLessEqual(
            int(selected.groupby("capability_id").size().max()), 2
        )


if __name__ == "__main__":
    unittest.main()
