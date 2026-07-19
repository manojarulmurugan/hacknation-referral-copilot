"""Regression tests for Stage 5 readiness scoring."""

from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from pipeline.stage5_readiness_scoring import (
    COMPLETENESS_FLOOR,
    OUT_PATH,
    score_facility_capability,
    verdict_for,
)
from pipeline.stage4_taxonomy_mapping import LOCKED_CAPABILITY_IDS

ROOT = Path(__file__).resolve().parent.parent


def make_group(rows: list[dict]) -> pd.DataFrame:
    defaults = {"provenance_status": "consistent_or_no_conflict", "exclude_from_scoring": False, "source_field": "description"}
    return pd.DataFrame([{**defaults, **r} for r in rows])


class ScoringMathTests(unittest.TestCase):
    def test_single_tracer_match_is_not_conditional_pass(self) -> None:
        group = make_group([
            {"bullet_id": "b1", "capability_id": "dialysis", "domain": "equipment", "tracer_id": "dialysis_machine"},
        ])
        result = score_facility_capability(group, {"staff": 1, "equipment": 2, "procedures": 3, "diagnostics": 1})
        self.assertEqual(result["distinct_tracer_count"], 1)
        self.assertFalse(result["conditional_pass"])

    def test_two_distinct_tracers_from_two_bullets_two_source_fields_pass(self) -> None:
        group = make_group([
            {"bullet_id": "b1", "capability_id": "dialysis", "domain": "procedures", "tracer_id": "hemodialysis", "source_field": "description"},
            {"bullet_id": "b2", "capability_id": "dialysis", "domain": "procedures", "tracer_id": "peritoneal_dialysis", "source_field": "procedure"},
        ])
        result = score_facility_capability(group, {"staff": 1, "equipment": 2, "procedures": 3, "diagnostics": 1})
        self.assertEqual(result["distinct_tracer_count"], 2)
        self.assertEqual(result["distinct_bullet_count"], 2)
        self.assertEqual(result["distinct_source_field_count"], 2)
        self.assertTrue(result["conditional_pass"])

    def test_two_distinct_tracers_from_one_bullet_do_not_pass(self) -> None:
        """Codex catch: 'ICU with ventilator support' matching two tracers is
        one claim, not independent corroboration -- must not pass alone."""
        group = make_group([
            {"bullet_id": "b1", "capability_id": "icu", "domain": "procedures", "tracer_id": "icu_service"},
            {"bullet_id": "b1", "capability_id": "icu", "domain": "equipment", "tracer_id": "ventilator"},
        ])
        result = score_facility_capability(group, {"staff": 1, "equipment": 3, "procedures": 2, "diagnostics": 1})
        self.assertEqual(result["distinct_tracer_count"], 2)
        self.assertEqual(result["distinct_bullet_count"], 1)
        self.assertFalse(result["conditional_pass"])

    def test_two_bullets_same_source_field_do_not_pass(self) -> None:
        """Second-opinion review catch: two distinct bullets that both come
        from the same source_field (e.g. two sentences split out of the same
        'description' text) are still one underlying claim, not independent
        corroboration -- 25% of pairs that passed the old >=2-bullets-only
        rule were exactly this case. Must not pass without a second,
        genuinely distinct source_field."""
        group = make_group([
            {"bullet_id": "b1", "capability_id": "dialysis", "domain": "procedures", "tracer_id": "hemodialysis", "source_field": "description"},
            {"bullet_id": "b2", "capability_id": "dialysis", "domain": "procedures", "tracer_id": "peritoneal_dialysis", "source_field": "description"},
        ])
        result = score_facility_capability(group, {"staff": 1, "equipment": 2, "procedures": 3, "diagnostics": 1})
        self.assertEqual(result["distinct_tracer_count"], 2)
        self.assertEqual(result["distinct_bullet_count"], 2)
        self.assertEqual(result["distinct_source_field_count"], 1)
        self.assertFalse(result["conditional_pass"])

    def test_domain_score_excludes_undefined_domains(self) -> None:
        group = make_group([
            {"bullet_id": "b1", "capability_id": "emergency", "domain": "procedures", "tracer_id": "emergency_service"},
        ])
        # emergency has no diagnostics domain defined
        result = score_facility_capability(group, {"staff": 1, "equipment": 2, "procedures": 1})
        self.assertNotIn("diagnostics", result["domain_scores"])

    def test_excluded_bullets_never_contribute_to_domain_scores(self) -> None:
        group = make_group([
            {"bullet_id": "b1", "capability_id": "icu", "domain": "equipment", "tracer_id": "ventilator", "exclude_from_scoring": True},
        ])
        result = score_facility_capability(group, {"staff": 1, "equipment": 3, "procedures": 2, "diagnostics": 1})
        self.assertEqual(result["domain_scores"]["equipment"], 0.0)
        self.assertEqual(result["distinct_tracer_count"], 0)
        self.assertFalse(result["conditional_pass"])

    def test_excluded_bullets_still_appear_in_rejected_bullet_ids(self) -> None:
        group = make_group([
            {"bullet_id": "b1", "capability_id": "icu", "domain": "equipment", "tracer_id": "ventilator", "exclude_from_scoring": True},
        ])
        result = score_facility_capability(group, {"staff": 1, "equipment": 3, "procedures": 2, "diagnostics": 1})
        self.assertEqual(result["rejected_bullet_ids"], ["b1"])
        self.assertEqual(result["supporting_bullet_ids"], [])

    def test_empty_group_scores_to_zero_with_no_error(self) -> None:
        empty = pd.DataFrame({
            "bullet_id": pd.Series(dtype="object"),
            "capability_id": pd.Series(dtype="object"),
            "domain": pd.Series(dtype="object"),
            "tracer_id": pd.Series(dtype="object"),
            "source_field": pd.Series(dtype="object"),
            "exclude_from_scoring": pd.Series(dtype="bool"),
            "provenance_status": pd.Series(dtype="object"),
        })
        result = score_facility_capability(empty, {"staff": 1, "equipment": 2, "procedures": 3, "diagnostics": 1})
        self.assertEqual(result["distinct_tracer_count"], 0)
        self.assertEqual(result["distinct_bullet_count"], 0)
        self.assertFalse(result["conditional_pass"])
        self.assertEqual(result["contradiction_flags"], [])


class ContradictionFlagTests(unittest.TestCase):
    def test_general_surgery_without_ot_is_flagged(self) -> None:
        group = make_group([
            {"bullet_id": "b1", "capability_id": "general_surgery", "domain": "procedures", "tracer_id": "hernia_repair"},
        ])
        result = score_facility_capability(group, {"staff": 2, "equipment": 2, "procedures": 4})
        self.assertIn("general_surgery claimed with no operation theatre evidenced", result["contradiction_flags"])

    def test_general_surgery_with_ot_is_not_flagged(self) -> None:
        group = make_group([
            {"bullet_id": "b1", "capability_id": "general_surgery", "domain": "procedures", "tracer_id": "hernia_repair"},
            {"bullet_id": "b2", "capability_id": "general_surgery", "domain": "equipment", "tracer_id": "operation_theatre"},
        ])
        result = score_facility_capability(group, {"staff": 2, "equipment": 2, "procedures": 4})
        self.assertEqual(result["contradiction_flags"], [])

    def test_icu_without_ventilator_is_flagged(self) -> None:
        group = make_group([
            {"bullet_id": "b1", "capability_id": "icu", "domain": "procedures", "tracer_id": "icu_service"},
        ])
        result = score_facility_capability(group, {"staff": 1, "equipment": 3, "procedures": 2, "diagnostics": 1})
        self.assertIn("icu claimed with no ventilator evidence", result["contradiction_flags"])

    def test_unrelated_capability_never_flagged(self) -> None:
        group = make_group([
            {"bullet_id": "b1", "capability_id": "dialysis", "domain": "procedures", "tracer_id": "hemodialysis"},
        ])
        result = score_facility_capability(group, {"staff": 1, "equipment": 2, "procedures": 3, "diagnostics": 1})
        self.assertEqual(result["contradiction_flags"], [])


class VerdictTests(unittest.TestCase):
    def test_insufficient_evidence_overrides_conditional_pass(self) -> None:
        self.assertEqual(
            verdict_for(conditional_pass=True, completeness_bullet_count=1, distinct_tracer_count=3),
            "insufficient_evidence",
        )

    def test_zero_tracers_is_insufficient_evidence_not_claimed(self) -> None:
        """Codex catch: a zero-match pair must not read as 'claimed'."""
        self.assertEqual(
            verdict_for(conditional_pass=False, completeness_bullet_count=COMPLETENESS_FLOOR, distinct_tracer_count=0),
            "insufficient_evidence",
        )

    def test_corroborated_requires_pass_and_completeness(self) -> None:
        self.assertEqual(
            verdict_for(conditional_pass=True, completeness_bullet_count=COMPLETENESS_FLOOR, distinct_tracer_count=2),
            "corroborated",
        )

    def test_claimed_only_when_pass_fails_but_complete_and_has_evidence(self) -> None:
        self.assertEqual(
            verdict_for(conditional_pass=False, completeness_bullet_count=COMPLETENESS_FLOOR, distinct_tracer_count=1),
            "claimed_only",
        )


@unittest.skipUnless(OUT_PATH.exists(), "run the Stage 5 pipeline first")
class GeneratedArtifactRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.readiness = pd.read_parquet(OUT_PATH)

    def test_no_duplicate_facility_capability_rows(self) -> None:
        self.assertFalse(self.readiness.duplicated(subset=["facility_id", "capability_id"]).any())

    def test_full_cross_product_every_facility_has_all_locked_capabilities(self) -> None:
        counts = self.readiness.groupby("facility_id").size()
        self.assertTrue((counts == len(LOCKED_CAPABILITY_IDS)).all())

    def test_verdicts_are_one_of_the_three_locked_values(self) -> None:
        self.assertTrue(
            set(self.readiness["verdict"].unique()) <= {"corroborated", "claimed_only", "insufficient_evidence"}
        )

    def test_zero_match_rows_are_insufficient_evidence(self) -> None:
        zero_match = self.readiness[self.readiness["distinct_tracer_count"] == 0]
        self.assertTrue((zero_match["verdict"] == "insufficient_evidence").all())

    def test_claimed_only_rows_always_have_at_least_one_tracer(self) -> None:
        claimed = self.readiness[self.readiness["verdict"] == "claimed_only"]
        self.assertTrue((claimed["distinct_tracer_count"] >= 1).all())

    def test_corroborated_rows_have_two_tracers_two_bullets_two_source_fields(self) -> None:
        corroborated = self.readiness[self.readiness["verdict"] == "corroborated"]
        self.assertTrue((corroborated["distinct_tracer_count"] >= 2).all())
        self.assertTrue((corroborated["distinct_bullet_count"] >= 2).all())
        self.assertTrue((corroborated["distinct_source_field_count"] >= 2).all())

    def test_claimed_only_is_not_limited_to_single_tracer_pairs(self) -> None:
        """P2 catch: claimed_only is 'usable evidence that doesn't clear the
        corroboration bar', not 'exactly one tracer'. Real data has pairs
        with multiple tracers (from one bullet, or one source_field) that
        correctly land here -- this test guards against re-narrowing the
        definition back to 'exactly one tracer' in either code or docs."""
        claimed = self.readiness[self.readiness["verdict"] == "claimed_only"]
        self.assertTrue((claimed["distinct_tracer_count"] >= 2).any())

    def test_meaningful_fraction_of_matched_pairs_are_corroborated(self) -> None:
        matched = self.readiness[self.readiness["distinct_tracer_count"] >= 1]
        pct = (matched["verdict"] == "corroborated").mean()
        self.assertGreater(pct, 0.10)  # sanity bound among pairs with any evidence, not brittle to exact tuning


if __name__ == "__main__":
    unittest.main()
