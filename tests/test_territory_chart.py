import csv
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from scripts.territory_chart import build_summary, geometry_area_km2, load_history, write_history


class TerritoryChartTests(unittest.TestCase):
    def test_geodesic_polygon_area_is_plausible(self):
        geometry = {
            "type": "Polygon",
            "coordinates": [[[30, 50], [31, 50], [31, 51], [30, 51], [30, 50]]],
        }
        self.assertTrue(7_700 < geometry_area_km2(geometry) < 8_100)

    def test_csv_round_trip(self):
        rows = {date(2026, 1, 2): {"occupied_km2": 123.4567, "source": "test"}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.csv"
            write_history(rows, path)
            loaded = load_history(path)
        self.assertAlmostEqual(loaded[date(2026, 1, 2)]["occupied_km2"], 123.457)

    def test_monthly_summary_uses_last_observation_and_keeps_12_months(self):
        rows = {}
        for month in range(1, 13):
            rows[date(2025, month, 1)] = {"occupied_km2": 1000 + month, "source": "test"}
            rows[date(2025, month, 20)] = {"occupied_km2": 2000 + month, "source": "test"}
        rows[date(2026, 1, 10)] = {"occupied_km2": 2100, "source": "test"}
        summary = build_summary(rows, datetime(2026, 1, 11, tzinfo=timezone.utc))
        self.assertEqual(len(summary["monthly"]), 12)
        self.assertEqual(summary["monthly"][0]["month"], "2025-02")
        self.assertEqual(summary["monthly"][-1]["occupied_km2"], 2100)
        self.assertEqual(summary["monthly"][-2]["occupied_km2"], 2012)


if __name__ == "__main__":
    unittest.main()
