import sys
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from heating_diagnosis import (DailyHeatingWindow, FixedClock, HeatNode,
                               HeatingDiagnosisService)

NOW = datetime(2026, 1, 5, 23, 30)
START = datetime(2026, 1, 5, 22, 0)


def make_service():
    service = HeatingDiagnosisService(
        clock=FixedClock(NOW), window=DailyHeatingWindow(time(22, 0), time(6, 0)))
    service.revise_topology([
        HeatNode("SOURCE-1", "heat_source"),
        HeatNode("HX-2", "exchange_station", "SOURCE-1"),
        HeatNode("BLDG-101", "building", "HX-2"),
        HeatNode("BLDG-102", "building", "HX-2"),
    ])
    return service


def series(node, supply, ret, flow, hours=9, start=START):
    return [{"node_id": node, "ts": (start + timedelta(hours=i)).isoformat(),
             "supply_c": supply, "return_c": ret, "flow_m3_h": flow}
            for i in range(hours)]


def seed(service, building_supply=38.0, building_flow=40.0):
    service.ingest_batch("B-samples", "samples",
                         series("SOURCE-1", 62, 42, 300)
                         + series("HX-2", 52, 40, 120)
                         + series("BLDG-101", building_supply, 34, building_flow)
                         + series("BLDG-102", 50, 41, 20))
    service.ingest_batch("B-outdoor", "outdoor",
                         [{"ts": "2026-01-05T20:00:00", "outdoor_c": -5.0}])


class IngestionTests(unittest.TestCase):
    def test_same_batch_is_not_counted_twice(self):
        service = make_service()
        first = service.ingest_batch("B1", "samples", series("BLDG-101", 50, 40, 20))
        again = service.ingest_batch("B1", "samples", series("BLDG-101", 50, 40, 20))
        self.assertFalse(first.skipped)
        self.assertTrue(again.skipped)
        self.assertEqual(again.accepted, 0)
        self.assertEqual(len(service.store.samples), 9)

    def test_unknown_kind_rejected(self):
        service = make_service()
        with self.assertRaises(ValueError):
            service.ingest_batch("B2", "weather", [])

    def test_temporary_outage_uses_injected_clock(self):
        service = make_service()
        service.ingest_batch("B-out", "outages", [
            {"outage_id": "O-1", "node_id": "HX-2",
             "start": "2026-01-05T21:00:00", "reason": "水泵抢修"},  # 无 end：临时停运
            {"outage_id": "O-2", "node_id": "HX-2",
             "start": "2026-01-04T10:00:00", "end": "2026-01-04T12:00:00"},
        ])
        active = {o.outage_id for o in service.active_outages()}
        self.assertEqual(active, {"O-1"})  # O-2 在注入时钟之前已结束


if __name__ == "__main__":
    unittest.main()
