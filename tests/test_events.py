import unittest
from datetime import datetime

from helpers import batch, ingest_imbalance_scene, make_service, outdoor, sample


class EventLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.service, self.clock = make_service()

    def test_reappearing_closed_event_is_linked_not_overwritten(self):
        # 第一次出现 → 事件打开
        ingest_imbalance_scene(self.service, "P1")
        self.service.run_diagnosis()
        first = self.service.list_events(status="open")[0]
        first_snapshot = dict(
            status=first.status, opened_at=first.opened_at,
            interval_ids=list(first.interval_ids),
        )

        # 数据恢复正常 → 事件关闭
        self.clock.set(datetime(2026, 1, 7, 1, 30))
        self.service.ingest(batch("P2-OUT", "outdoor",
                                  [outdoor(0, day=7), outdoor(1, day=7)]))
        normal = []
        for h, m in ((0, 0), (0, 30), (1, 0)):
            for n in ("HX-1", "B-1", "B-2", "B-3"):
                normal.append(sample(n, h, m, 50.0, day=7))
        self.service.ingest(batch("P2-S", "samples", normal))
        self.service.run_diagnosis()
        self.assertEqual(self.service.get_event(first.event_id).status, "closed")

        # 同类异常再次出现 → 新事件关联旧事件，旧事件不被覆盖
        self.clock.set(datetime(2026, 1, 7, 3, 30))
        self.service.ingest(batch("P3-OUT", "outdoor",
                                  [outdoor(2, day=7), outdoor(3, day=7)]))
        relapsed = []
        for h, m in ((2, 0), (2, 30), (3, 0)):
            relapsed.append(sample("HX-1", h, m, 50.0, day=7))
            relapsed.append(sample("B-1", h, m, 43.0, day=7))
            relapsed.append(sample("B-2", h, m, 43.0, day=7))
        self.service.ingest(batch("P3-S", "samples", relapsed))
        self.service.run_diagnosis()

        reopened = self.service.list_events(status="open")
        self.assertEqual(len(reopened), 1)
        second = reopened[0]
        self.assertNotEqual(second.event_id, first.event_id)
        self.assertEqual(second.previous_event_id, first.event_id)

        old = self.service.get_event(first.event_id)
        self.assertEqual(old.status, "closed")
        self.assertEqual(old.opened_at, first_snapshot["opened_at"])
        self.assertEqual(old.interval_ids, first_snapshot["interval_ids"])

        chain = self.service.event_chain(second.event_id)
        self.assertEqual([e.event_id for e in chain], [first.event_id, second.event_id])

    def test_conclusion_created_with_event(self):
        ingest_imbalance_scene(self.service)
        self.service.run_diagnosis()
        event = self.service.list_events()[0]
        conclusion = self.service.get_conclusion(event.event_id)
        self.assertEqual(conclusion.status, "pending")
        self.assertEqual(conclusion.category, event.category)


if __name__ == "__main__":
    unittest.main()
