import unittest
from datetime import datetime

from helpers import batch, ingest_imbalance_scene, make_service, outdoor, sample
from heating_diagnosis import models


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.service, self.clock = make_service()
        ingest_imbalance_scene(self.service)
        self.service.run_diagnosis()
        suggestion = self.service.list_suggestions(status="open")[0]
        self.order = self.service.confirm_suggestion(suggestion.suggestion_id, "调度员")

    def _recover(self):
        # 处置后（跨午夜）新批次：欠供消除
        self.clock.set(datetime(2026, 1, 7, 1, 30))
        self.service.ingest(batch("R-OUT", "outdoor",
                                  [outdoor(0, day=7), outdoor(1, day=7)]))
        items = []
        for h, m in ((0, 0), (0, 30), (1, 0)):
            items.append(sample("HX-1", h, m, 50.0, day=7))
            items.append(sample("B-1", h, m, 49.5, day=7))
            items.append(sample("B-2", h, m, 49.6, day=7))
            items.append(sample("B-3", h, m, 50.0, day=7))
        self.service.ingest(batch("R-S", "samples", items))

    def test_compare_requires_completed_order(self):
        with self.assertRaises(ValueError):
            self.service.compare_disposal(self.order.order_id)

    def test_before_after_metrics_show_improvement(self):
        self.service.complete_work_order(
            self.order.order_id, resolved=True,
            confirmed_cause=models.HYDRAULIC_IMBALANCE,
        )
        self._recover()
        report = self.service.compare_disposal(self.order.order_id)
        before = report.before.metrics
        after = report.after.metrics
        self.assertGreater(before["avg_supply_deficit_c"], 3.0)
        self.assertLess(after["avg_supply_deficit_c"], 1.0)
        self.assertEqual(after["anomalous_ratio"], 0.0)
        self.assertLess(after["avg_supply_deficit_c"], before["avg_supply_deficit_c"])
        self.assertTrue(report.improved)
        self.assertEqual(report.deltas["avg_supply_deficit_c"],
                         round(after["avg_supply_deficit_c"] - before["avg_supply_deficit_c"], 3))
        # 报告包含窗口边界与样本量，便于核对
        self.assertEqual(report.before.end, report.after.start)
        self.assertGreater(report.before.sample_count, 0)
        self.assertGreater(report.after.sample_count, 0)
        self.assertTrue(any("处置前窗口" in n for n in report.notes))

    def test_compare_without_after_data_is_explained(self):
        self.service.complete_work_order(
            self.order.order_id, resolved=True,
            confirmed_cause=models.HYDRAULIC_IMBALANCE,
        )
        # 时钟不前进、无新数据
        report = self.service.compare_disposal(self.order.order_id)
        self.assertEqual(report.after.sample_count, 0)
        self.assertFalse(report.improved)
        self.assertTrue(any("暂无新量测" in n or "缺少可评分样本" in n for n in report.notes))


if __name__ == "__main__":
    unittest.main()
