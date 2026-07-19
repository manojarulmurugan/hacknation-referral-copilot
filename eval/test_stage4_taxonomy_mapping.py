"""Regression tests for deterministic Stage 4 taxonomy mapping."""

from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from pipeline.stage4_taxonomy_mapping import (
    LOCKED_CAPABILITY_IDS,
    LOCKED_DOMAINS,
    build_capability_map,
    build_distinct_match_index,
    compile_rules,
    load_taxonomy,
    match_text,
    validate_taxonomy,
)

ROOT = Path(__file__).resolve().parent.parent
TAXONOMY_PATH = ROOT / "taxonomy" / "capability_taxonomy.yaml"
OUT_PATH = ROOT / "data" / "processed" / "bullet_capability_map.parquet"
FLAGS_PATH = ROOT / "data" / "processed" / "bullet_provenance_flags.parquet"
BULLETS_PATH = ROOT / "data" / "processed" / "evidence_bullets.parquet"


def minimal_taxonomy() -> dict:
    return {
        "version": 1,
        "domains": sorted(LOCKED_DOMAINS),
        "capabilities": {
            capability_id: {
                "staff": [
                    {
                        "tracer_id": f"{capability_id}_staff",
                        "description": "fixture",
                        "include_patterns": [rf"\b{capability_id}\b"],
                    }
                ]
            }
            for capability_id in LOCKED_CAPABILITY_IDS
        },
    }


class TaxonomyValidationTests(unittest.TestCase):
    def test_real_taxonomy_is_complete_and_compilable(self) -> None:
        taxonomy = load_taxonomy(TAXONOMY_PATH)
        validate_taxonomy(taxonomy)
        self.assertGreater(len(compile_rules(taxonomy)), 50)

    def test_every_tracer_has_executable_include_rules(self) -> None:
        taxonomy = load_taxonomy(TAXONOMY_PATH)
        for capability_id, domains in taxonomy["capabilities"].items():
            for domain, tracers in domains.items():
                for tracer in tracers:
                    self.assertTrue(
                        tracer.get("include_patterns"),
                        f"{capability_id}/{domain}/{tracer['tracer_id']}",
                    )

    def test_rejects_unknown_capability(self) -> None:
        taxonomy = minimal_taxonomy()
        taxonomy["capabilities"]["unknown"] = taxonomy["capabilities"]["icu"]
        with self.assertRaises(ValueError):
            validate_taxonomy(taxonomy)

    def test_rejects_invalid_regex(self) -> None:
        taxonomy = minimal_taxonomy()
        taxonomy["capabilities"]["icu"]["staff"][0]["include_patterns"] = ["("]
        with self.assertRaises(ValueError):
            validate_taxonomy(taxonomy)


class AmbiguityGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = compile_rules(load_taxonomy(TAXONOMY_PATH))

    def identities(self, text: str) -> set[tuple[str, str]]:
        return {
            (match["capability_id"], match["tracer_id"])
            for match in match_text(text, self.rules)
        }

    def test_specific_general_surgeon_matches(self) -> None:
        self.assertIn(
            ("general_surgery", "general_surgeon"),
            self.identities("Dr Rao is a senior General Surgeon"),
        )

    def test_specialty_surgeons_do_not_imply_general_surgery(self) -> None:
        for text in (
            "Neurosurgeon available",
            "Liver transplant surgeon",
            "Cardiac surgeon on staff",
        ):
            self.assertNotIn(
                ("general_surgery", "general_surgeon"), self.identities(text)
            )

    def test_dental_trauma_is_not_trauma_centre_evidence(self) -> None:
        self.assertNotIn(
            ("trauma", "trauma_care_service"),
            self.identities("Management of dental trauma and broken teeth"),
        )

    def test_oncology_ldr_is_not_maternity_ldr(self) -> None:
        identities = self.identities(
            "HDR and LDR techniques for cancers of the cervix using brachytherapy"
        )
        self.assertNotIn(("maternity", "ldr_suite"), identities)
        self.assertIn(("oncology", "radiotherapy"), identities)

    def test_intensive_care_ambulance_is_not_an_icu(self) -> None:
        identities = self.identities("Intensive care ambulance service available")
        self.assertNotIn(("icu", "icu_service"), identities)
        self.assertIn(("emergency", "ambulance_service"), identities)

    def test_bare_ot_token_is_not_an_operation_theatre(self) -> None:
        self.assertNotIn(
            ("general_surgery", "operation_theatre"),
            self.identities("OT timings may change"),
        )
        self.assertIn(
            ("general_surgery", "operation_theatre"),
            self.identities("Two modular operation theatres are available"),
        )

    def test_department_name_is_service_not_staff(self) -> None:
        identities = self.identities("Gynaecology Department")
        self.assertIn(("maternity", "maternity_service"), identities)
        self.assertNotIn(("maternity", "obstetrician"), identities)
        self.assertEqual(self.identities("Neurosurgery Department"), set())

    def test_negated_capability_is_not_positive_evidence(self) -> None:
        self.assertNotIn(
            ("dialysis", "dialysis_machine"),
            self.identities("The hospital does not have a dialysis unit"),
        )

    def test_bounded_sample_false_positives_are_guarded(self) -> None:
        cases = [
            (
                "Underwent coronary angiography at Lilavati Hospital",
                ("cardiac", "angiography"),
            ),
            (
                "Implants implantable cardioverter-defibrillators (ICDs)",
                ("emergency", "defibrillator"),
            ),
            (
                "Non-invasive ventilation (NIV) management",
                ("icu", "mechanical_ventilation"),
            ),
            (
                "Positron Emission Tomography brain imaging for epilepsy",
                ("oncology", "pet_ct"),
            ),
            (
                "Training Centre for Blood Bank Medical Officers",
                ("blood_bank", "transfusion_specialist"),
            ),
        ]
        for text, identity in cases:
            with self.subTest(text=text):
                self.assertNotIn(identity, self.identities(text))


class MappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.taxonomy = load_taxonomy(TAXONOMY_PATH)
        self.bullets = pd.DataFrame(
            [
                {
                    "bullet_id": "b1",
                    "facility_id": "f1",
                    "source_field": "equipment",
                    "text": "Has 3 dialysis machines",
                },
                {
                    "bullet_id": "b2",
                    "facility_id": "f2",
                    "source_field": "equipment",
                    "text": "Has 3 dialysis machines",
                },
                {
                    "bullet_id": "b3",
                    "facility_id": "f3",
                    "source_field": "services",
                    "text": "Dental clinic only",
                },
            ]
        )
        self.flags = pd.DataFrame(
            [
                {"bullet_id": "b1", "status": "accepted", "exclude_from_scoring": False},
                {"bullet_id": "b2", "status": "rejected", "exclude_from_scoring": True},
                {"bullet_id": "b3", "status": "accepted", "exclude_from_scoring": False},
            ]
        )

    def test_distinct_text_is_scanned_once_then_broadcast(self) -> None:
        index, summary = build_distinct_match_index(
            self.bullets, compile_rules(self.taxonomy)
        )
        self.assertEqual(summary["distinct_texts_scanned"], 2)
        self.assertEqual(
            len(index[index["tracer_id"] == "dialysis_machine"]), 1
        )
        result, _ = build_capability_map(self.bullets, self.flags, self.taxonomy)
        machine_rows = result[result["tracer_id"] == "dialysis_machine"]
        self.assertEqual(set(machine_rows["bullet_id"]), {"b1", "b2"})

    def test_provenance_rejection_is_visible_and_passed_through(self) -> None:
        result, _ = build_capability_map(self.bullets, self.flags, self.taxonomy)
        rejected = result[result["bullet_id"] == "b2"].iloc[0]
        self.assertEqual(rejected.provenance_status, "rejected")
        self.assertTrue(bool(rejected.exclude_from_scoring))

    def test_supporting_quote_is_verbatim(self) -> None:
        result, _ = build_capability_map(self.bullets, self.flags, self.taxonomy)
        text_by_id = self.bullets.set_index("bullet_id")["text"]
        self.assertTrue(
            all(
                row.supporting_quote in text_by_id.loc[row.bullet_id]
                for row in result.itertuples(index=False)
            )
        )


@unittest.skipUnless(
    OUT_PATH.exists() and BULLETS_PATH.exists() and FLAGS_PATH.exists(),
    "run the deterministic Stage 4 pipeline first",
)
class GeneratedArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.map_df = pd.read_parquet(OUT_PATH)
        if (
            "matcher_method" not in cls.map_df.columns
            or not cls.map_df["matcher_method"].eq("deterministic_regex_v1").all()
        ):
            raise unittest.SkipTest("existing artifact is the superseded LLM map")
        cls.bullets = pd.read_parquet(BULLETS_PATH)
        cls.flags = pd.read_parquet(FLAGS_PATH)

    def test_output_identifiers_are_locked_and_unique(self) -> None:
        self.assertTrue(set(self.map_df["capability_id"]) <= LOCKED_CAPABILITY_IDS)
        self.assertTrue(set(self.map_df["domain"]) <= LOCKED_DOMAINS)
        key = ["bullet_id", "capability_id", "domain", "tracer_id"]
        self.assertFalse(self.map_df.duplicated(key).any())

    def test_all_quotes_and_provenance_flags_are_exact(self) -> None:
        text_by_id = self.bullets.set_index("bullet_id")["text"]
        flags_by_id = self.flags.set_index("bullet_id")["exclude_from_scoring"]
        for row in self.map_df.itertuples(index=False):
            self.assertIn(row.supporting_quote, text_by_id.loc[row.bullet_id])
            self.assertEqual(
                bool(row.exclude_from_scoring),
                bool(flags_by_id.loc[row.bullet_id]),
            )


if __name__ == "__main__":
    unittest.main()
