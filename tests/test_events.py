import sys
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from heating_diagnosis import (DailyHeatingWindow, FixedClock, HeatNode,
                               HeatingDiagnosisService)
from test_ingestion import make_service, seed, series


class EventLifecycleTests(unittest.TestCase):
    def test_reappearing_closed_event_is_linked_not_overwritten(self):
        service = make_service()
        seed(service)
        first = service.diagnose("BLDG-101")
        old = next(e for e in first.events if e.anomaly_kind == "low_supply")
        service.close_event(old.event_id, "气温回升，暂关闭")

        # 下一采暖窗口异常复发
        service.clock.set(datetime(2026, 1, 6, 23, 30))
        later = datetime(2026, 1, 6, 22, 0)
        service.ingest_batch("B-night2", "samples",
                             series("BLDG-101", 37, 33, 40, hours=9, start=later))
        second = service.diagnose("BLDG-101")
        new = next(e for e in second.events if e.anomaly_kind == "low_supply")

        self.assertEqual(new.reoccurrence_of, old.event_id)  # 关联
        self.assertNotEqual(new.event_id, old.event_id)
        kept = service.store.events[old.event_id]
        self.assertEqual(kept.status, "closed")              # 旧事件未被覆盖
        self.assertEqual(kept.conclusion, "气温回升，暂关闭")
        self.assertIn("复发", new.conclusion)
        self.assertTrue(any("复发关联" in e for e in new.evidence))

    def test_open_event_is_extended_not_duplicated(self):
        service = make_service()
        seed(service)
        service.diagnose("BLDG-101")
        report = service.diagnose("BLDG-101")
        ids = [e.event_id for e in report.events]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
