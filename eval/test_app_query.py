"""Unit tests for app/query.py's pure resolution functions."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.query import parse_query, parse_query_with_fallback, resolve_capability, resolve_city
from pipeline.stage3_provenance_audit import PhraseMatcher, normalize


class ResolveCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        vocab_phrases = {
            "dialysis": ["hemodialysis", "kidney dialysis"],
            "icu": ["intensive care unit", "critical care"],
        }
        phrase_to_id: dict[str, str] = {}
        for capability_id, phrases in vocab_phrases.items():
            for phrase in [capability_id] + phrases:
                phrase_to_id[normalize(phrase)] = capability_id
        self.matcher = PhraseMatcher(phrase_to_id.keys())
        self.phrase_to_id = phrase_to_id

    def test_resolves_bare_capability_word(self) -> None:
        capability_id, _ = resolve_capability("dialysis near Jaipur", self.matcher, self.phrase_to_id)
        self.assertEqual(capability_id, "dialysis")

    def test_resolves_multiword_phrase(self) -> None:
        capability_id, _ = resolve_capability("need critical care unit please", self.matcher, self.phrase_to_id)
        self.assertEqual(capability_id, "icu")

    def test_no_match_returns_none(self) -> None:
        capability_id, phrase = resolve_capability("random unrelated text", self.matcher, self.phrase_to_id)
        self.assertIsNone(capability_id)
        self.assertIsNone(phrase)


class ResolveCityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.phrase_to_label = {
            normalize("Jaipur"): "Jaipur, Rajasthan",
            normalize("New Delhi"): "New Delhi, Delhi",
        }
        self.centroids = {
            normalize("Jaipur"): (26.9, 75.8, 42),
            normalize("New Delhi"): (28.6, 77.2, 100),
        }
        self.matcher = PhraseMatcher(self.phrase_to_label.keys())

    def test_resolves_city_and_returns_centroid(self) -> None:
        label, lat, lon, count = resolve_city(
            "dialysis near Jaipur", self.matcher, self.phrase_to_label, self.centroids
        )
        self.assertEqual(label, "Jaipur, Rajasthan")
        self.assertAlmostEqual(lat, 26.9)
        self.assertEqual(count, 42)

    def test_multiword_city_resolves(self) -> None:
        label, *_ = resolve_city("ICU near New Delhi", self.matcher, self.phrase_to_label, self.centroids)
        self.assertEqual(label, "New Delhi, Delhi")

    def test_unresolved_city_returns_none(self) -> None:
        label, lat, lon, count = resolve_city(
            "dialysis somewhere unspecified", self.matcher, self.phrase_to_label, self.centroids
        )
        self.assertIsNone(label)
        self.assertIsNone(lat)


class ParseQueryTests(unittest.TestCase):
    def _store(self, with_capability: bool = True) -> SimpleNamespace:
        cap_phrase_to_id = {normalize("dialysis"): "dialysis"} if with_capability else {}
        city_phrase_to_label = {normalize("Jaipur"): "Jaipur, Rajasthan"}
        city_centroids = {normalize("Jaipur"): (26.9, 75.8, 42)}
        return SimpleNamespace(
            capability_matcher=PhraseMatcher(cap_phrase_to_id.keys()),
            capability_phrase_to_id=cap_phrase_to_id,
            city_matcher=PhraseMatcher(city_phrase_to_label.keys()),
            city_phrase_to_label=city_phrase_to_label,
            city_centroids=city_centroids,
        )

    def test_parse_query_combines_both_resolutions(self) -> None:
        parsed = parse_query("dialysis near Jaipur", self._store())
        self.assertEqual(parsed.capability_id, "dialysis")
        self.assertEqual(parsed.city_label, "Jaipur, Rajasthan")

    def test_parse_query_missing_capability_still_resolves_city(self) -> None:
        parsed = parse_query("something near Jaipur", self._store(with_capability=False))
        self.assertIsNone(parsed.capability_id)
        self.assertEqual(parsed.city_label, "Jaipur, Rajasthan")


class ParseQueryWithFallbackTests(unittest.TestCase):
    def _store(self, with_capability: bool = True) -> SimpleNamespace:
        cap_phrase_to_id = {normalize("dialysis"): "dialysis"} if with_capability else {}
        city_phrase_to_label = {normalize("Jaipur"): "Jaipur, Rajasthan"}
        city_centroids = {normalize("Jaipur"): (26.9, 75.8, 42)}
        return SimpleNamespace(
            capability_matcher=PhraseMatcher(cap_phrase_to_id.keys()),
            capability_phrase_to_id=cap_phrase_to_id,
            city_matcher=PhraseMatcher(city_phrase_to_label.keys()),
            city_phrase_to_label=city_phrase_to_label,
            city_centroids=city_centroids,
        )

    def test_llm_never_called_when_deterministic_fully_resolves(self) -> None:
        def blow_up(_text: str) -> dict:
            raise AssertionError("LLM must not be called when deterministic parse already succeeded")

        parsed = parse_query_with_fallback("dialysis near Jaipur", self._store(), llm_parser=blow_up)
        self.assertEqual(parsed.capability_id, "dialysis")
        self.assertFalse(parsed.used_llm_fallback)

    def test_llm_fills_in_missing_capability(self) -> None:
        fake_llm = lambda _text: {"capability_id": "cardiac", "location_text": None}
        parsed = parse_query_with_fallback(
            "open heart surgery near Jaipur", self._store(with_capability=False), llm_parser=fake_llm
        )
        self.assertEqual(parsed.capability_id, "cardiac")
        self.assertEqual(parsed.city_label, "Jaipur, Rajasthan")  # deterministic already had this
        self.assertTrue(parsed.used_llm_fallback)

    def test_llm_location_text_is_reresolved_deterministically_not_trusted_blindly(self) -> None:
        # LLM extracts "Jaipur" as free text; must be re-resolved through the
        # real city index (never geocoded directly from LLM output).
        fake_llm = lambda _text: {"capability_id": None, "location_text": "Jaipur"}
        parsed = parse_query_with_fallback("dialysis somewhere unspecified", self._store(), llm_parser=fake_llm)
        self.assertEqual(parsed.city_label, "Jaipur, Rajasthan")
        self.assertEqual(parsed.origin_lat, 26.9)
        self.assertTrue(parsed.used_llm_fallback)

    def test_llm_location_text_unresolvable_stays_none(self) -> None:
        fake_llm = lambda _text: {"capability_id": None, "location_text": "Atlantis"}
        parsed = parse_query_with_fallback("dialysis somewhere unspecified", self._store(), llm_parser=fake_llm)
        self.assertIsNone(parsed.city_label)

    def test_llm_failure_falls_back_silently_never_raises(self) -> None:
        def blow_up(_text: str) -> dict:
            raise RuntimeError("endpoint timeout")

        parsed = parse_query_with_fallback("dialysis somewhere unspecified", self._store(), llm_parser=blow_up)
        self.assertEqual(parsed.capability_id, "dialysis")  # deterministic result preserved
        self.assertIsNone(parsed.city_label)
        self.assertFalse(parsed.used_llm_fallback)
        self.assertIn("endpoint timeout", parsed.llm_error)


if __name__ == "__main__":
    unittest.main()
