"""Unit tests for app/ranking.py."""

from __future__ import annotations

import unittest

import pandas as pd

from app.ranking import haversine_km, rank_candidates


class HaversineTests(unittest.TestCase):
    def test_known_distance_jaipur_to_delhi(self) -> None:
        d = haversine_km(26.9124, 75.7873, 28.6139, 77.2090)
        self.assertTrue(230 <= d <= 280, d)

    def test_zero_distance_same_point(self) -> None:
        self.assertAlmostEqual(haversine_km(10.0, 20.0, 10.0, 20.0), 0.0, places=6)


class RankCandidatesTests(unittest.TestCase):
    """Fixture: f4 (evidence, very close), f2 (evidence, far), f1 (zero
    evidence, close), f5 (zero evidence, closer than f2 but farther than
    f1), f3 (no geo -- must never appear)."""

    def setUp(self) -> None:
        self.origin = (26.9124, 75.7873)
        self.facilities = pd.DataFrame(
            [
                {"unique_id": "f1", "name": "Near Zero-Evidence", "address_city": "Jaipur", "address_stateOrRegion": "Rajasthan", "latitude": 26.95, "longitude": 75.80},
                {"unique_id": "f2", "name": "Far Corroborated", "address_city": "Jaipur", "address_stateOrRegion": "Rajasthan", "latitude": 27.5, "longitude": 76.5},
                {"unique_id": "f3", "name": "No Geo", "address_city": "Jaipur", "address_stateOrRegion": "Rajasthan", "latitude": None, "longitude": None},
                {"unique_id": "f4", "name": "Very Near Claimed", "address_city": "Jaipur", "address_stateOrRegion": "Rajasthan", "latitude": 26.93, "longitude": 75.79},
                {"unique_id": "f5", "name": "Mid Zero-Evidence", "address_city": "Jaipur", "address_stateOrRegion": "Rajasthan", "latitude": 27.0, "longitude": 75.85},
            ]
        )
        self.readiness = pd.DataFrame(
            [
                {"facility_id": "f1", "capability_id": "dialysis", "verdict": "insufficient_evidence", "distinct_tracer_count": 0},
                {"facility_id": "f2", "capability_id": "dialysis", "verdict": "corroborated", "distinct_tracer_count": 3},
                {"facility_id": "f3", "capability_id": "dialysis", "verdict": "corroborated", "distinct_tracer_count": 5},
                {"facility_id": "f4", "capability_id": "dialysis", "verdict": "claimed_only", "distinct_tracer_count": 1},
                {"facility_id": "f5", "capability_id": "dialysis", "verdict": "insufficient_evidence", "distinct_tracer_count": 0},
            ]
        )

    def _rank(self, sort_mode: str = "nearest", limit: int = 20) -> pd.DataFrame:
        return rank_candidates(self.readiness, self.facilities, "dialysis", *self.origin, sort_mode=sort_mode, limit=limit)

    def test_evidence_bearing_facilities_never_buried_by_distance(self) -> None:
        """Second-opinion review catch: pure distance sort could put an
        entire page of zero-evidence facilities ahead of an evidenced one.
        f4 and f2 (evidence) must both rank ahead of f1 and f5 (zero
        evidence), even though f1 is geographically closer than f2."""
        ranked = self._rank("nearest")
        order = list(ranked["facility_id"])
        self.assertLess(order.index("f4"), order.index("f1"))
        self.assertLess(order.index("f2"), order.index("f1"))
        self.assertLess(order.index("f2"), order.index("f5"))

    def test_nearest_orders_within_evidence_tier_by_distance(self) -> None:
        ranked = self._rank("nearest")
        evidence_rows = ranked[ranked["is_evidence_tier"]]
        self.assertEqual(list(evidence_rows["facility_id"]), ["f4", "f2"])

    def test_nearest_backfills_zero_evidence_tier_by_distance(self) -> None:
        ranked = self._rank("nearest")
        zero_rows = ranked[~ranked["is_evidence_tier"]]
        self.assertEqual(list(zero_rows["facility_id"]), ["f1", "f5"])

    def test_backfill_respects_limit(self) -> None:
        # limit=3: 2 evidence-tier facilities (f4, f2) + 1 backfill slot -> nearest zero-evidence (f1) only
        ranked = self._rank("nearest", limit=3)
        self.assertEqual(list(ranked["facility_id"]), ["f4", "f2", "f1"])

    def test_limit_smaller_than_evidence_tier_excludes_zero_evidence_entirely(self) -> None:
        ranked = self._rank("nearest", limit=1)
        self.assertEqual(list(ranked["facility_id"]), ["f4"])

    def test_most_evidence_sort_prefers_verdict_then_tracer_count_within_tier(self) -> None:
        ranked = self._rank("most_evidence")
        evidence_rows = ranked[ranked["is_evidence_tier"]]
        # f2 is corroborated (verdict_rank 0), f4 is claimed_only (verdict_rank 1) -- f2 first despite being farther
        self.assertEqual(list(evidence_rows["facility_id"]), ["f2", "f4"])

    def test_insufficient_evidence_never_dropped_entirely(self) -> None:
        ranked = self._rank()
        self.assertIn("f1", set(ranked["facility_id"]))

    def test_missing_geo_facility_excluded_not_crashed(self) -> None:
        ranked = self._rank()
        self.assertNotIn("f3", set(ranked["facility_id"]))

    def test_unmatched_capability_returns_empty_not_error(self) -> None:
        ranked = rank_candidates(self.readiness, self.facilities, "icu", *self.origin)
        self.assertTrue(ranked.empty)


class BestRankingTests(unittest.TestCase):
    def _rank(self, facilities: list[dict], readiness: list[dict]) -> pd.DataFrame:
        return rank_candidates(
            pd.DataFrame(readiness),
            pd.DataFrame(facilities),
            "cardiac",
            10.0,
            20.0,
            sort_mode="best",
        )

    def test_farther_stronger_hospital_beats_closer_weaker_hospital(self) -> None:
        facilities = [
            {"unique_id": "close", "facilityTypeId": "hospital", "latitude": 10.01, "longitude": 20.0},
            {"unique_id": "far", "facilityTypeId": "hospital", "latitude": 10.18, "longitude": 20.0},
        ]
        readiness = [
            {"facility_id": "close", "capability_id": "cardiac", "verdict": "claimed_only", "readiness_score": 0.0, "distinct_tracer_count": 1},
            {"facility_id": "far", "capability_id": "cardiac", "verdict": "corroborated", "readiness_score": 1.0, "distinct_tracer_count": 3},
        ]
        ranked = self._rank(facilities, readiness)
        self.assertEqual(list(ranked["facility_id"]), ["far", "close"])

    def test_implausible_type_is_flagged_and_score_penalized(self) -> None:
        facilities = [
            {"unique_id": "hospital", "facilityTypeId": "hospital", "latitude": 10.01, "longitude": 20.0},
            {"unique_id": "dentist", "facilityTypeId": "dentist", "latitude": 10.01, "longitude": 20.0},
        ]
        readiness = [
            {"facility_id": facility_id, "capability_id": "cardiac", "verdict": "corroborated", "readiness_score": 1.0, "distinct_tracer_count": 2}
            for facility_id in ["hospital", "dentist"]
        ]
        ranked = self._rank(facilities, readiness).set_index("facility_id")
        self.assertFalse(bool(ranked.loc["hospital", "type_implausible"]))
        self.assertTrue(bool(ranked.loc["dentist", "type_implausible"]))
        self.assertAlmostEqual(
            ranked.loc["dentist", "composite_score"],
            ranked.loc["hospital", "composite_score"] * 0.2,
        )

    def test_geo_mismatch_discounts_proximity(self) -> None:
        facilities = [
            {"unique_id": "consistent", "facilityTypeId": "hospital", "latitude": 10.01, "longitude": 20.0, "geo_mismatch_km": 0.0},
            {"unique_id": "mismatch", "facilityTypeId": "hospital", "latitude": 10.01, "longitude": 20.0, "geo_mismatch_km": 100.0},
        ]
        readiness = [
            {"facility_id": facility_id, "capability_id": "cardiac", "verdict": "corroborated", "readiness_score": 1.0, "distinct_tracer_count": 2}
            for facility_id in ["consistent", "mismatch"]
        ]
        ranked = self._rank(facilities, readiness).set_index("facility_id")
        self.assertFalse(bool(ranked.loc["consistent", "geo_discounted"]))
        self.assertTrue(bool(ranked.loc["mismatch", "geo_discounted"]))
        self.assertAlmostEqual(
            ranked.loc["mismatch", "proximity_component"],
            ranked.loc["consistent", "proximity_component"] * 0.5,
        )


class SearchBandWideningTests(unittest.TestCase):
    """Confirmed live bug this fixes: "most evidence" mode had no distance
    cap and could surface a facility 1,558km away as the top result --
    accurate distance math, but useless for an actual referral decision."""

    def setUp(self) -> None:
        self.origin = (26.9124, 75.7873)  # Jaipur
        # 6 evidence-bearing facilities clustered within ~30km of origin.
        close_rows = []
        for i in range(6):
            fid = f"close{i}"
            close_rows.append({"unique_id": fid, "name": fid, "address_city": "Jaipur", "address_stateOrRegion": "Rajasthan", "latitude": 26.9124 + i * 0.03, "longitude": 75.7873})
        # One evidence-bearing facility ~1,750km away (Chennai coordinates).
        far_row = {"unique_id": "far1", "name": "Far Away Hospital", "address_city": "Chennai", "address_stateOrRegion": "Tamil Nadu", "latitude": 13.0827, "longitude": 80.2707}
        self.facilities = pd.DataFrame(close_rows + [far_row])
        self.readiness = pd.DataFrame(
            [{"facility_id": row["unique_id"], "capability_id": "dialysis", "verdict": "corroborated", "distinct_tracer_count": 3} for row in close_rows]
            + [{"facility_id": "far1", "capability_id": "dialysis", "verdict": "corroborated", "distinct_tracer_count": 10}]
        )

    def test_band_stays_tight_when_enough_close_evidence_exists(self) -> None:
        ranked = rank_candidates(self.readiness, self.facilities, "dialysis", *self.origin, sort_mode="most_evidence")
        self.assertEqual(ranked["search_band_km"].iloc[0], 50.0)
        self.assertFalse(ranked["search_widened"].iloc[0])
        self.assertNotIn("far1", set(ranked["facility_id"]))

    def test_far_high_evidence_facility_excluded_even_in_most_evidence_mode(self) -> None:
        """The core bug: far1 has more evidence (10 tracers) than any close
        facility (3 each) -- pure verdict/tracer sorting would put it first.
        With the band cap, it must not appear at all while enough close
        options exist."""
        ranked = rank_candidates(self.readiness, self.facilities, "dialysis", *self.origin, sort_mode="most_evidence")
        self.assertNotIn("far1", set(ranked["facility_id"]))

    def test_band_widens_when_not_enough_close_evidence(self) -> None:
        # Only 2 close evidence-bearing facilities -- below MIN_EVIDENCE_RESULTS (5).
        thin_facilities = self.facilities[self.facilities["unique_id"].isin(["close0", "close1", "far1"])]
        thin_readiness = self.readiness[self.readiness["facility_id"].isin(["close0", "close1", "far1"])]
        ranked = rank_candidates(thin_readiness, thin_facilities, "dialysis", *self.origin, sort_mode="most_evidence")
        self.assertTrue(ranked["search_widened"].iloc[0])
        # far1 becomes reachable once the band widens enough to include it.
        self.assertIn("far1", set(ranked["facility_id"]))

    def test_nationwide_fallback_when_no_band_meets_threshold(self) -> None:
        # Only 1 evidence-bearing facility total, nationally -- no band will
        # ever reach MIN_EVIDENCE_RESULTS=5, so this must still return
        # something (nationwide fallback), not an empty result.
        single = self.facilities[self.facilities["unique_id"] == "far1"]
        single_readiness = self.readiness[self.readiness["facility_id"] == "far1"]
        ranked = rank_candidates(single_readiness, single, "dialysis", *self.origin, sort_mode="nearest")
        self.assertFalse(ranked.empty)
        self.assertTrue(pd.isna(ranked["search_band_km"].iloc[0]))
        self.assertTrue(ranked["search_widened"].iloc[0])


if __name__ == "__main__":
    unittest.main()
