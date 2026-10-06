import unittest

from helpers import ingest_imbalance_scene, make_service
from heating_diagnosis import models
from heating_diagnosis.rules import Thresholds


class WorkOrderTests(unittest.TestCase):
    def setUp(self):
        self.service, self.clock = make_service()
        ingest_imbalance_scene(self.service)
        self.service.run_diagnosis()
        self.suggestion = self.service.list_suggestions(status="open")[0]

    def test_confirm_creates_order_once(self):
        first = self.service.confirm_suggestion(self.suggestion.suggestion_id, "调度员-甲")
        second = self.service.confirm_suggestion(self.suggestion.suggestion_id, "调度员-乙")
        self.assertEqual(first.order_id, second.order_id)
        self.assertEqual(len(self.service.store.work_orders), 1)
        self.assertEqual(
            self.service.store.suggestions[self.suggestion.suggestion_id].status,
            "confirmed",
        )

    def test_feedback_confirms_conclusion(self):
        order = self.service.confirm_suggestion(self.suggestion.suggestion_id, "调度员")
        conclusion = self.service.complete_work_order(
            order.order_id, resolved=True,
            confirmed_cause=models.HYDRAULIC_IMBALANCE, notes="已调节支线阀门",
        )
        self.assertEqual(conclusion.status, "confirmed")
        self.assertEqual(len(conclusion.feedback), 1)
        self.assertEqual(conclusion.feedback[0].order_id, order.order_id)
        # 解决后事件关闭
        self.assertEqual(self.service.get_event(order.event_id).status, "closed")

    def test_feedback_revises_conclusion(self):
        order = self.service.confirm_suggestion(self.suggestion.suggestion_id, "调度员")
        conclusion = self.service.complete_work_order(
            order.order_id, resolved=False,
            confirmed_cause=models.EQUIPMENT_OUTAGE, notes="现场为换热器故障",
        )
        self.assertEqual(conclusion.status, "revised")
        self.assertEqual(conclusion.revised_category, models.EQUIPMENT_OUTAGE)

    def test_feedback_marks_false_positive(self):
        order = self.service.confirm_suggestion(self.suggestion.suggestion_id, "调度员")
        conclusion = self.service.complete_work_order(
            order.order_id, resolved=True, confirmed_cause="no_issue", notes="测温正常",
        )
        self.assertEqual(conclusion.status, "false_positive")

    def test_double_complete_rejected(self):
        order = self.service.confirm_suggestion(self.suggestion.suggestion_id, "调度员")
        self.service.complete_work_order(
            order.order_id, resolved=True, confirmed_cause=models.HYDRAULIC_IMBALANCE,
        )
        with self.assertRaises(ValueError):
            self.service.complete_work_order(
                order.order_id, resolved=True, confirmed_cause=models.HYDRAULIC_IMBALANCE,
            )

    def test_confirm_superseded_suggestion_rejected(self):
        self.service.register_rules(Thresholds(supply_deficit_c=2.0), "规则修订")
        with self.assertRaises(ValueError):
            self.service.confirm_suggestion(self.suggestion.suggestion_id, "调度员")

    def test_unknown_cause_rejected(self):
        order = self.service.confirm_suggestion(self.suggestion.suggestion_id, "调度员")
        with self.assertRaises(ValueError):
            self.service.complete_work_order(
                order.order_id, resolved=True, confirmed_cause="alien_interference",
            )


if __name__ == "__main__":
    unittest.main()
