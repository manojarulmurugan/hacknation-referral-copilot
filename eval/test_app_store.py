"""Unit tests for app/store.py's geo self-consistency check.

Confirmed real case this guards against: a facility in this dataset has
address_city="Chennai" but coordinates ~250km away, next to Trichy --
haversine ranking against its own coordinates was correct, but the card's
displayed city looked wrong. compute_geo_mismatch_km() flags that
disagreement so it can be surfaced instead of silently trusted either way.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from app.store import (
    GEO_MISMATCH_THRESHOLD_KM,
    compute_coordinate_city,
    compute_geo_mismatch_km,
    read_parquet_artifact,
)


class ComputeGeoMismatchKmTests(unittest.TestCase):
    def setUp(self) -> None:
        # 5 facilities correctly clustered near real coordinates for
        # "CityX, StateA" (mean stays close to the true location even with
        # a simple average -- a single outlier in a group this size barely
        # moves the centroid, matching the real 309-facility Chennai case).
        self.facilities = pd.DataFrame(
            [
                {"unique_id": "f1", "address_city": "CityX", "address_stateOrRegion": "StateA", "latitude": 10.000, "longitude": 20.000},
                {"unique_id": "f2", "address_city": "CityX", "address_stateOrRegion": "StateA", "latitude": 10.010, "longitude": 20.010},
                {"unique_id": "f3", "address_city": "CityX", "address_stateOrRegion": "StateA", "latitude": 9.995, "longitude": 19.995},
                {"unique_id": "f4", "address_city": "CityX", "address_stateOrRegion": "StateA", "latitude": 10.005, "longitude": 20.005},
                {"unique_id": "f5", "address_city": "CityX", "address_stateOrRegion": "StateA", "latitude": 10.002, "longitude": 19.998},
                # Claims CityX but is actually ~1,500km away -- the Chennai/Trichy case.
                {"unique_id": "f6", "address_city": "CityX", "address_stateOrRegion": "StateA", "latitude": 23.000, "longitude": 20.000},
                # No lat/lon on file at all.
                {"unique_id": "f7", "address_city": "CityX", "address_stateOrRegion": "StateA", "latitude": None, "longitude": None},
                # City with no group stats entry at all (e.g. dropped for being null elsewhere).
                {"unique_id": "f8", "address_city": "Nowhereville", "address_stateOrRegion": "StateZ", "latitude": 1.0, "longitude": 1.0},
            ]
        )
        # Mirrors build_city_index's per-(city,state) stats shape.
        self.city_state_stats = pd.DataFrame(
            [
                {"norm_city": "cityx", "address_stateOrRegion": "StateA", "lat": 10.002, "lon": 20.001, "count": 6, "display_city": "CityX"},
            ]
        )

    def test_consistent_facility_not_flagged(self) -> None:
        result = compute_geo_mismatch_km(self.facilities, self.city_state_stats)
        self.assertLess(result.loc[self.facilities["unique_id"] == "f1"].iloc[0], GEO_MISMATCH_THRESHOLD_KM)

    def test_outlier_facility_flagged_above_threshold(self) -> None:
        result = compute_geo_mismatch_km(self.facilities, self.city_state_stats)
        km = result.loc[self.facilities["unique_id"] == "f6"].iloc[0]
        self.assertGreaterEqual(km, GEO_MISMATCH_THRESHOLD_KM)

    def test_missing_coordinates_returns_nan_not_crash(self) -> None:
        result = compute_geo_mismatch_km(self.facilities, self.city_state_stats)
        self.assertTrue(pd.isna(result.loc[self.facilities["unique_id"] == "f7"].iloc[0]))

    def test_city_with_no_group_stats_returns_nan_not_crash(self) -> None:
        result = compute_geo_mismatch_km(self.facilities, self.city_state_stats)
        self.assertTrue(pd.isna(result.loc[self.facilities["unique_id"] == "f8"].iloc[0]))


class ComputeCoordinateCityTests(unittest.TestCase):
    def test_only_mismatched_facility_gets_nearest_cluster_label(self) -> None:
        facilities = pd.DataFrame(
            [
                {"unique_id": "mismatch", "latitude": 30.05, "longitude": 40.05, "geo_mismatch_km": 500.0},
                {"unique_id": "consistent", "latitude": 10.05, "longitude": 20.05, "geo_mismatch_km": 5.0},
            ]
        )
        city_state_stats = pd.DataFrame(
            [
                {"norm_city": "cityx", "display_city": "CityX", "lat": 10.0, "lon": 20.0},
                {"norm_city": "cityy", "display_city": "CityY", "lat": 30.0, "lon": 40.0},
            ]
        )
        result = compute_coordinate_city(facilities, city_state_stats)
        self.assertEqual(result.loc[0], "CityY")
        self.assertIsNone(result.loc[1])


class ReadParquetArtifactTests(unittest.TestCase):
    def test_partitioned_deploy_directory_is_read_as_one_frame(self) -> None:
        with TemporaryDirectory() as temp_dir:
            parts = Path(temp_dir) / "processed" / "evidence_bullets"
            parts.mkdir(parents=True)
            pd.DataFrame({"value": [1, 2]}).to_parquet(parts / "part-000.parquet")
            pd.DataFrame({"value": [3]}).to_parquet(parts / "part-001.parquet")
            with patch("app.store.DATA_DIR", Path(temp_dir)):
                result = read_parquet_artifact("processed/evidence_bullets.parquet")
        self.assertEqual(list(result["value"]), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
