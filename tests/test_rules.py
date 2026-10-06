import unittest
from datetime import datetime

from helpers import batch, ingest_imbalance_scene, make_service, outdoor, sample
from heating_diagnosis import models
from heating_diagnosis.rules import Thresholds


class RuleSuggestionTests(unittest.TestCase):
    def setUp(self):
        self.service, self.clock = make_service()

    def test_imbalance_maps_to_adjust(self):
        ingest_imbalance_scene(self.service)
        self.service.run_diagnosis()
        suggestion = self.service.list_suggestions()[0]
        self.assertEqual(suggestion.action_type, models.ACTION_ADJUST)
        self.assertEqual(suggestion.rule_version, 1)
        self.assertIn("水力失衡", suggestion.reason)

    def test_mild_user_side_maps_to_observe(self):
        # 欠供 3.4°C（刚超阈值）且无投诉 → 严重度低 → 观察
        self.service.ingest(batch("OUT", "outdoor", [outdoor(h) for h in (20, 21, 22, 23)]))
        items = []
        for h, m in ((21, 0), (21, 30), (22, 0)):
            items.append(sample("HX-1", h, m, 50.0))
            items.append(sample("B-1", h, m, 50.0))
            items.append(sample("B-2", h, m, 50.0))
            items.append(sample("B-3", h, m, 46.6))
        self.service.ingest(batch("S", "samples", items))
        self.service.run_diagnosis()
        suggestion = self.service.list_suggestions()[0]
        self.assertEqual(suggestion.action_type, models.ACTION_OBSERVE)

    def test_new_rule_version_supersedes_open_suggestions(self):
        ingest_imbalance_scene(self.service)
        self.service.run_diagnosis()
        old = self.service.list_suggestions(status="open")[0]

        self.service.register_rules(Thresholds(supply_deficit_c=2.5), "收紧欠供阈值")
        self.assertEqual(
            self.service.store.suggestions[old.suggestion_id].status,
            "superseded",
        )

        # 新数据触发复诊 → 按新规则版本重新生成建议
        self.clock.set(datetime(2026, 1, 7, 0, 30))
        self.service.ingest(batch("P2-OUT", "outdoor", [outdoor(0, day=7)]))
        items = []
        for h, m in ((23, 30), (0, 0)):
            day = 6 if h == 23 else 7
            items.append(sample("HX-1", h, m, 50.0, day=day))
            items.append(sample("B-1", h, m, 43.0, day=day))
            items.append(sample("B-2", h, m, 43.0, day=day))
        self.service.ingest(batch("P2-S", "samples", items))
        self.service.run_diagnosis()
        new_open = self.service.list_suggestions(status="open")
        self.assertEqual(len(new_open), 1)
        self.assertEqual(new_open[0].rule_version, 2)
        self.assertNotEqual(new_open[0].suggestion_id, old.suggestion_id)

    def test_suggestion_carries_evidence(self):
        ingest_imbalance_scene(self.service)
        self.service.run_diagnosis()
        suggestion = self.service.list_suggestions()[0]
        explained = self.service.explain_suggestion(suggestion.suggestion_id)
        self.assertTrue(explained["lines"])
        kinds = {e.kind for e in explained["evidence"]}
        self.assertIn("threshold", kinds)
        self.assertIn("metric", kinds)
        # 证据中包含具体阈值数值，便于调度员核对
        threshold_values = [e.value for e in explained["evidence"] if e.kind == "threshold"]
        self.assertIn(3.0, threshold_values)


if __name__ == "__main__":
    unittest.main()
