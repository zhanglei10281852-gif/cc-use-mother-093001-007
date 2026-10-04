import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from test_ingestion import make_service, seed


class WorkOrderTests(unittest.TestCase):
    def _confirmed_order(self, service):
        report = service.diagnose("BLDG-101")
        suggestion = report.suggestions[0]
        order = service.confirm_suggestion(suggestion.suggestion_id, "调度员甲")
        return suggestion, order

    def test_confirm_generates_work_order(self):
        service = make_service()
        seed(service)
        suggestion, order = self._confirmed_order(service)
        self.assertEqual(order.status, "issued")
        self.assertEqual(order.action, suggestion.action)
        self.assertEqual(service.store.suggestions[suggestion.suggestion_id].status,
                         "confirmed")
        event = service.store.events[order.event_id]
        self.assertIn(order.order_id, event.conclusion)

    def test_double_confirm_rejected(self):
        service = make_service()
        seed(service)
        suggestion, _ = self._confirmed_order(service)
        with self.assertRaises(ValueError):
            service.confirm_suggestion(suggestion.suggestion_id, "调度员乙")

    def test_resolved_feedback_updates_conclusion(self):
        service = make_service()
        seed(service)
        _, order = self._confirmed_order(service)
        event = service.record_feedback(order.order_id, True, "已调平衡阀")
        self.assertEqual(event.status, "resolved")
        self.assertIn("处置有效", event.conclusion)
        self.assertIn("已调平衡阀", event.conclusion)
        self.assertEqual(event.feedback[0]["order_id"], order.order_id)
        self.assertTrue(event.feedback[0]["resolved"])

    def test_unresolved_feedback_keeps_event_open(self):
        service = make_service()
        seed(service)
        _, order = self._confirmed_order(service)
        event = service.record_feedback(order.order_id, False, "调节后仍低温")
        self.assertEqual(event.status, "open")
        self.assertIn("未闭环", event.conclusion)
        self.assertEqual(len(event.feedback), 1)

    def test_double_feedback_rejected(self):
        service = make_service()
        seed(service)
        _, order = self._confirmed_order(service)
        service.record_feedback(order.order_id, True)
        with self.assertRaises(ValueError):
            service.record_feedback(order.order_id, True)


if __name__ == "__main__":
    unittest.main()
