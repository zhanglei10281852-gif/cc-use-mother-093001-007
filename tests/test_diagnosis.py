import sys
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from heating_diagnosis import (DailyHeatingWindow, FixedClock, HeatNode,
                               HeatingDiagnosisService)
from test_ingestion import START, make_service, seed, series


class DiagnosisTests(unittest.TestCase):
    def test_low_supply_interval_with_explainable_evidence(self):
        service = make_service()
        seed(service)
        report = service.diagnose("BLDG-101")
        kinds = {i.kind for i in report.intervals}
        self.assertIn("low_supply", kinds)
        interval = next(i for i in report.intervals if i.kind == "low_supply")
        self.assertEqual(interval.start, START)
        self.assertEqual(interval.end, START + timedelta(hours=8))  # 跨午夜到 06:00
        self.assertTrue(any("气候补偿" in e for e in interval.evidence))
        self.assertTrue(any("室外" in e for e in interval.evidence))

    def test_impact_scope_covers_downstream(self):
        service = make_service()
        seed(service)
        report = service.diagnose("HX-2")
        self.assertEqual(set(report.impact_scope),
                         {"HX-2", "BLDG-101", "BLDG-102"})

    def test_hydraulic_imbalance_suggests_adjust(self):
        service = make_service()
        seed(service)  # BLDG-101 流量 40，同站 BLDG-102 流量 20 → 2.0 倍
        report = service.diagnose("BLDG-101")
        sug = next(s for s in report.suggestions if s.rule_id == "R-LOW-DELTAT")
        self.assertEqual(sug.action, "adjust")
        self.assertIn("水力失衡", sug.summary)
        self.assertTrue(any("2.00 倍" in e for e in sug.evidence))

    def test_station_drop_suggests_inspect(self):
        service = make_service()
        service.ingest_batch("B-s", "samples",
                             series("SOURCE-1", 62, 42, 300)
                             + series("HX-2", 50, 40, 120))  # 落差 12℃ > 阈值 6℃
        service.ingest_batch("B-o", "outdoor",
                             [{"ts": "2026-01-05T20:00:00", "outdoor_c": -5.0}])
        report = service.diagnose("HX-2")
        sug = next(s for s in report.suggestions if s.rule_id == "R-STATION-DROP")
        self.assertEqual(sug.action, "inspect")
        self.assertTrue(any("热源出口" in e for e in sug.evidence))

    def test_outage_samples_are_excluded(self):
        service = make_service()
        seed(service)
        service.ingest_batch("B-out", "outages", [
            {"outage_id": "O-1", "node_id": "BLDG-101",
             "start": "2026-01-05T21:00:00", "end": "2026-01-06T07:00:00",
             "reason": "楼前阀检修"},
        ])
        report = service.diagnose("BLDG-101")
        self.assertEqual(report.excluded_outage_samples, 9)  # 全部样本落在停运内
        self.assertEqual(report.intervals, [])
        self.assertTrue(any("O-1" in n and "剔除" in n for n in report.outage_notes))

    def test_complaint_cluster_creates_observe_suggestion(self):
        service = make_service()
        seed(service, building_supply=50.0, building_flow=20.0)  # 计量正常
        service.ingest_batch("B-c", "complaints", [
            {"complaint_id": f"C-{i}", "node_id": "BLDG-101",
             "ts": (START + timedelta(hours=2 + i)).isoformat(), "detail": "室温 15℃"}
            for i in range(3)])
        report = service.diagnose("BLDG-101")
        kinds = {i.kind for i in report.intervals}
        self.assertEqual(kinds, {"complaint_cluster"})
        sug = report.suggestions[0]
        self.assertEqual(sug.action, "observe")
        self.assertTrue(any("C-0" in e for e in sug.evidence))

    def test_rediagnose_does_not_duplicate_open_event(self):
        service = make_service()
        seed(service)
        service.diagnose("BLDG-101")
        service.diagnose("BLDG-101")
        self.assertEqual(len(service.store.events), 2)  # low_supply + low_delta_t
        self.assertEqual(len(service.store.suggestions), 2)


if __name__ == "__main__":
    unittest.main()
