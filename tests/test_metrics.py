import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from test_ingestion import make_service, seed, series


class MetricsCompareTests(unittest.TestCase):
    def test_compare_before_and_after_handling(self):
        service = make_service()
        seed(service)  # 处置前：供水 38℃、温差 4℃、投诉 3 起
        service.ingest_batch("B-c", "complaints", [
            {"complaint_id": f"C-{i}", "node_id": "BLDG-101",
             "ts": (datetime(2026, 1, 5, 22, 0) + timedelta(hours=2 + i)).isoformat(),
             "detail": "室温 15℃"} for i in range(3)])
        after = datetime(2026, 1, 6, 22, 0)
        service.ingest_batch("B-after", "samples",
                             series("BLDG-101", 50, 41, 22, hours=8, start=after))
        result = service.compare_metrics(
            "BLDG-101",
            ("2026-01-05T22:00:00", "2026-01-06T06:00:00"),
            ("2026-01-06T22:00:00", "2026-01-07T06:00:00"))
        self.assertEqual(result["before"]["avg_supply_c"], 38.0)
        self.assertEqual(result["after"]["avg_supply_c"], 50.0)
        self.assertEqual(result["before"]["anomaly_ratio"], 1.0)
        self.assertEqual(result["after"]["anomaly_ratio"], 0.0)
        self.assertEqual(result["delta"]["avg_supply_c"], 12.0)
        self.assertEqual(result["before"]["complaint_count"], 3)
        self.assertEqual(result["after"]["complaint_count"], 0)
        self.assertIn("38.0→50.0", result["summary"])

    def test_compare_with_empty_window(self):
        service = make_service()
        seed(service)
        result = service.compare_metrics(
            "BLDG-101",
            ("2026-01-05T22:00:00", "2026-01-06T06:00:00"),
            ("2026-02-01T22:00:00", "2026-02-02T06:00:00"))
        self.assertEqual(result["after"]["sample_count"], 0)
        self.assertIn("样本不足", result["summary"])

    def test_explain_suggestion_lists_evidence(self):
        service = make_service()
        seed(service)
        report = service.diagnose("BLDG-101")
        suggestion = report.suggestions[0]
        info = service.explain_suggestion(suggestion.suggestion_id)
        self.assertEqual(info["rule_version"], "rules-2026.1")
        self.assertTrue(any(e.startswith("规则 R-") for e in info["evidence"]))
        self.assertEqual(info["event"]["event_id"], suggestion.event_id)
        self.assertIn("conclusion", info["event"])


if __name__ == "__main__":
    unittest.main()
