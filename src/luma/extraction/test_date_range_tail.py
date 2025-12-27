#!/usr/bin/env python3
"""
Unit-style checks for absolute date range tails (e.g., "oct 5th to 9th").

These tests ensure the extraction stage emits two absolute dates when a
month+day anchor is followed by a range marker and trailing day number.
"""
import sys
import unittest
from pathlib import Path

# Ensure src is on path
SCRIPT_DIR = Path(__file__).parent.resolve()
LUMA_DIR = SCRIPT_DIR.parent
SRC_DIR = LUMA_DIR.parent
SRC_PATH = str(SRC_DIR)
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from luma.extraction.matcher import EntityMatcher  # noqa: E402
from luma.grouping.reservation_intent_resolver import ReservationIntentResolver  # noqa: E402
from luma.config.core import STATUS_READY


class DateRangeTailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        normalization_dir = SRC_DIR / "luma" / "store" / "normalization"
        entity_file = normalization_dir / "101.v1.json"
        if not entity_file.exists():
            json_files = list(normalization_dir.glob("*.json"))
            if not json_files:
                raise FileNotFoundError(
                    f"Could not find normalization JSON in {normalization_dir}"
                )
            entity_file = json_files[0]
        cls.matcher = EntityMatcher(
            domain="service",
            entity_file=str(entity_file),
            lazy_load_spacy=False,
        )

    def _extract_dates_abs(self, sentence: str):
        result = self.matcher.extract_with_parameterization(sentence)
        return result.get("dates_absolute", [])

    def test_oct_5th_to_9th(self):
        dates_abs = self._extract_dates_abs("oct 5th to 9th")
        self.assertEqual(len(dates_abs), 2)
        self.assertIn("5", dates_abs[0]["text"])
        self.assertIn("9", dates_abs[1]["text"])

    def test_october_5_to_9(self):
        dates_abs = self._extract_dates_abs("october 5 to 9")
        self.assertEqual(len(dates_abs), 2)
        self.assertIn("5", dates_abs[0]["text"])
        self.assertIn("9", dates_abs[1]["text"])

    def test_oct_29th_to_2nd_no_cross_month(self):
        result = self.matcher.extract_with_parameterization("oct 29th to 2nd")
        dates_abs = result.get("dates_absolute", [])
        self.assertEqual(len(dates_abs), 1)
        self.assertIn("29", dates_abs[0]["text"])
        # Ambiguity should be flagged so downstream can clarify end date
        self.assertTrue(result.get("needs_clarification"))
        clar = result.get("clarification") or {}
        self.assertEqual(clar.get("data", {}).get("template"), "ask_end_date")

    def test_single_date_reservation_requires_end_date(self):
        """Single absolute date should require end_date for reservations."""
        sentence = "book room from oct 5th"
        result = self.matcher.extract_with_parameterization(sentence)
        resolver = ReservationIntentResolver()
        resp = resolver._build_response(
            intent="CREATE_RESERVATION",
            confidence=0.9,
            entities=result,
        )
        self.assertIn("end_date", resp.get("missing_slots", []))
        self.assertEqual(resp.get("clarification_template"), "ask_check_out")

    def test_range_reservation_is_ready(self):
        """Range with two dates should satisfy start_date and end_date."""
        sentence = "book room from oct 5th to 9th"
        result = self.matcher.extract_with_parameterization(sentence)
        resolver = ReservationIntentResolver()
        resp = resolver._build_response(
            intent="CREATE_RESERVATION",
            confidence=0.9,
            entities=result,
        )
        self.assertNotIn("end_date", resp.get("missing_slots", []))
        self.assertEqual(resp.get("status"), STATUS_READY)


if __name__ == "__main__":
    unittest.main()

