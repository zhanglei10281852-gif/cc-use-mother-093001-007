import unittest
from datetime import datetime

from helpers import (
    batch,
    complaint,
    ingest_imbalance_scene,
    make_service,
    outdoor,
    outage,
    sample,
)
from heating_diagnosis import models


class DiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.service, self.clock = make_service()

    def _intervals(self, run):
        return [self.service.store.intervals[i] for i in run.interval_ids]

    def test_hydraulic_imbalance_attributed_to_station(self):
        ingest_imbalance_scene(self.service)
        run = self.service.run_diagnosis()
        intervals = self._intervals(run)
        self.assertEqual(len(intervals), 1)
        iv = intervals[0]
        self.assertEqual(iv.node_id, "HX-1")
        self.assertEqual(iv.category, models.HYDRAULIC_IMBALANCE)
        # 影响范围：欠供的两栋楼 + 户数合计
        self.assertEqual(set(iv.impact.affected_node_ids), {"B-1", "B-2"})
        self.assertEqual(iv.impact.households, 220)
        # 可解释证据：阈值、欠供指标、室外温度、拓扑影响
        kinds = {e.kind for e in iv.evidence}
        self.assertIn("threshold", kinds)
        self.assertIn("metric", kinds)
        self.assertIn("outdoor", kinds)
        self.assertIn("topology", kinds)
        # 不重复派单：B-1/B-2 不再产生用户侧区间
        self.assertFalse(any(i.category == models.USER_SIDE for i in intervals))

    def test_planned_outage_excludes_samples_and_yields_observe(self):
        self.service.ingest(batch("OUT", "outdoor", [outdoor(h) for h in (20, 21, 22, 23)]))
        self.service.ingest(batch("OG", "outages", [
            outage("OG-1", "HX-2", 22, 0, 23, 0, kind="planned", reason="更换阀门"),
        ]))
        items = []
        for h, m in ((21, 0), (22, 0), (22, 30), (23, 0)):
            # 停运期间 HX-2 与下游 B-4 参数异常，但应被停运窗口剔除
            supply = 50.0 if (h, m) == (21, 0) else 30.0
            items.append(sample("HX-2", h, m, supply, flow=0.0 if supply < 40 else 100.0))
            items.append(sample("B-4", h, m, supply, flow=0.0 if supply < 40 else 100.0))
        self.service.ingest(batch("S", "samples", items))
        run = self.service.run_diagnosis()
        intervals = self._intervals(run)
        self.assertEqual(len(intervals), 1)
        iv = intervals[0]
        self.assertEqual(iv.node_id, "HX-2")
        self.assertEqual(iv.category, models.EQUIPMENT_OUTAGE)
        self.assertTrue(any(e.kind == "outage" for e in iv.evidence))
        # 停运期间下游楼栋不被误判为用户侧异常
        self.assertFalse(any(i.node_id == "B-4" for i in intervals))
        # 计划停运 → 观察建议
        suggestion = self.service.list_suggestions()[0]
        self.assertEqual(suggestion.action_type, models.ACTION_OBSERVE)

    def test_unplanned_outage_yields_inspect(self):
        self.service.ingest(batch("OUT", "outdoor", [outdoor(h) for h in (20, 21, 22)]))
        self.service.ingest(batch("OG", "outages", [
            outage("OG-1", "HX-2", 21, 0, 22, 0, kind="unplanned", reason="泵跳闸"),
        ]))
        run = self.service.run_diagnosis()
        intervals = self._intervals(run)
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0].category, models.EQUIPMENT_OUTAGE)
        suggestion = self.service.list_suggestions()[0]
        self.assertEqual(suggestion.action_type, models.ACTION_INSPECT)

    def test_ongoing_outage_uses_injected_clock(self):
        # 未结束的临时停运：有效结束时间取注入时钟的 now
        self.service.ingest(batch("OG", "outages", [
            outage("OG-9", "HX-2", 22, 0, kind="unplanned", reason="临时停运"),
        ]))
        run = self.service.run_diagnosis()
        iv = self._intervals(run)[0]
        self.assertEqual(iv.end, self.clock.now())

    def test_user_side_single_building_with_complaint(self):
        self.service.ingest(batch("OUT", "outdoor", [outdoor(h) for h in (20, 21, 22, 23)]))
        items = []
        for h, m in ((21, 0), (21, 30), (22, 0)):
            items.append(sample("HX-1", h, m, 50.0))
            items.append(sample("B-1", h, m, 50.0))
            items.append(sample("B-2", h, m, 50.0))
            items.append(sample("B-3", h, m, 43.5))  # 仅 B-3 欠供
        self.service.ingest(batch("S", "samples", items))
        self.service.ingest(batch("C", "complaints", [
            complaint("C-1", "B-3", 21, 10), complaint("C-2", "B-3", 21, 40),
        ]))
        run = self.service.run_diagnosis()
        intervals = self._intervals(run)
        self.assertEqual(len(intervals), 1)
        iv = intervals[0]
        self.assertEqual(iv.node_id, "B-3")
        self.assertEqual(iv.category, models.USER_SIDE)
        self.assertEqual(set(iv.impact.complaint_ids), {"C-1", "C-2"})
        self.assertTrue(any(e.kind == "complaint" for e in iv.evidence))
        suggestion = self.service.list_suggestions()[0]
        self.assertEqual(suggestion.action_type, models.ACTION_INSPECT)

    def test_cross_midnight_interval_merges(self):
        # 23:00 与次日 00:30 的异常点合并为同一跨午夜区间
        self.service.ingest(batch("OUT", "outdoor",
                                  [outdoor(22), outdoor(23), outdoor(0, day=7)]))
        items = []
        for day, h, m in ((6, 23, 0), (6, 23, 30), (7, 0, 0), (7, 0, 30)):
            items.append(sample("HX-1", h, m, 50.0, day=day))
            items.append(sample("B-1", h, m, 43.0, day=day))
            items.append(sample("B-2", h, m, 43.0, day=day))
        self.service.ingest(batch("S", "samples", items))
        run = self.service.run_diagnosis(as_of=datetime(2026, 1, 7, 1, 0))
        intervals = self._intervals(run)
        self.assertEqual(len(intervals), 1)
        iv = intervals[0]
        self.assertEqual(iv.start, datetime(2026, 1, 6, 23, 0))
        self.assertEqual(iv.end, datetime(2026, 1, 7, 0, 30))

    def test_samples_outside_heating_window_ignored(self):
        # 中午（窗口外）欠供不计异常
        self.service.ingest(batch("OUT", "outdoor", [outdoor(11), outdoor(12)]))
        items = [sample("B-1", 12, 0, 30.0), sample("B-2", 12, 0, 30.0)]
        self.service.ingest(batch("S", "samples", items))
        run = self.service.run_diagnosis()
        self.assertEqual(self._intervals(run), [])

    def test_low_flow_station_suppresses_building_alarms(self):
        # 换热站流量接近零 → 设备侧事件；下游楼栋不重复报警
        self.service.ingest(batch("OUT", "outdoor", [outdoor(h) for h in (20, 21, 22, 23)]))
        items = []
        for h, m in ((21, 0), (21, 30), (22, 0)):
            items.append(sample("HX-1", h, m, 38.0, flow=5.0))
            items.append(sample("B-1", h, m, 36.0, flow=4.0))
            items.append(sample("B-2", h, m, 36.0, flow=4.0))
        self.service.ingest(batch("S", "samples", items))
        run = self.service.run_diagnosis()
        intervals = self._intervals(run)
        self.assertEqual(len(intervals), 1)
        self.assertEqual(intervals[0].node_id, "HX-1")
        self.assertEqual(intervals[0].category, models.EQUIPMENT_OUTAGE)

    def test_missing_outdoor_snapshot_skips_scoring(self):
        self.service.ingest(batch("S", "samples", [sample("B-1", 21, 0, 30.0)]))
        run = self.service.run_diagnosis()
        self.assertEqual(self._intervals(run), [])

    def test_second_run_is_incremental_and_idempotent(self):
        ingest_imbalance_scene(self.service)
        run1 = self.service.run_diagnosis()
        count_after_first = len(self.service.store.intervals)
        # 同一时刻重复运行：没有新数据，不产生新区间
        run2 = self.service.run_diagnosis()
        self.assertEqual(run2.interval_ids, ())
        self.assertEqual(len(self.service.store.intervals), count_after_first)
        self.assertNotEqual(run1.run_id, run2.run_id)


if __name__ == "__main__":
    unittest.main()
