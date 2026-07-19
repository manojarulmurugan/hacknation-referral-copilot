"""Unit tests for shortlist persistence without a live database."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.persistence import SQLiteShortlistRepository, normalize_user_name


class SQLiteShortlistRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "shortlist.sqlite"
        self.repo = SQLiteShortlistRepository(self.db_path)
        self.context = {
            "query_text": "dialysis near Jaipur",
            "sort_mode": "best",
            "city_label": "Jaipur, Rajasthan",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_save_and_list_are_scoped_by_user(self) -> None:
        self.repo.save("Asha", "f1", "dialysis", self.context, "corroborated", 4.2)
        self.repo.save("Ravi", "f2", "dialysis", self.context, "claimed_only", 8.1)

        asha_items = self.repo.list_for_user("asha")
        self.assertEqual(len(asha_items), 1)
        self.assertEqual(asha_items[0].facility_id, "f1")
        self.assertEqual(asha_items[0].query_context, self.context)
        self.assertEqual(self.repo.list_for_user("Ravi")[0].facility_id, "f2")

    def test_exact_duplicate_is_idempotent(self) -> None:
        first = self.repo.save("Asha", "f1", "dialysis", self.context, "corroborated", 4.2)
        second = self.repo.save("Asha", "f1", "dialysis", self.context, "corroborated", 4.2)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(len(self.repo.list_for_user("Asha")), 1)

    def test_same_facility_can_be_saved_for_different_query_context(self) -> None:
        other_context = {**self.context, "query_text": "kidney care near Jaipur"}
        self.repo.save("Asha", "f1", "dialysis", self.context, "corroborated", 4.2)
        self.repo.save("Asha", "f1", "dialysis", other_context, "corroborated", 4.2)
        self.assertEqual(len(self.repo.list_for_user("Asha")), 2)

    def test_file_reopen_survives_repository_restart(self) -> None:
        self.repo.save("Asha", "f1", "dialysis", self.context, "corroborated", 4.2)
        reopened = SQLiteShortlistRepository(self.db_path)
        self.assertEqual(reopened.list_for_user("Asha")[0].facility_id, "f1")

    def test_blank_user_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.repo.list_for_user("   ")


class NormalizeUserNameTests(unittest.TestCase):
    def test_whitespace_is_collapsed(self) -> None:
        self.assertEqual(normalize_user_name("  Asha   Patel  "), "Asha Patel")


if __name__ == "__main__":
    unittest.main()
