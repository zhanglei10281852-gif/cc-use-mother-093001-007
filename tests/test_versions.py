import unittest

from helpers import ingest_imbalance_scene, make_service
from heating_diagnosis.contracts import HeatNode
from heating_diagnosis.metering import ClimateCurve, MeteringConfig
from heating_diagnosis.rules import Thresholds


class AnalysisVersionTests(unittest.TestCase):
    def setUp(self):
        self.service, self.clock = make_service()

    def test_initial_configuration_creates_first_version(self):
        versions = self.service.analysis_versions()
        self.assertEqual(len(versions), 1)
        self.assertEqual(
            (versions[0].topology_version, versions[0].metering_version,
             versions[0].rule_version),
            (1, 1, 1),
        )

    def test_metering_revision_creates_new_analysis_version(self):
        self.service.revise_metering(
            ClimateCurve(40.0, 5.0, 1.0),
            [MeteringConfig("HX-1", 100.0, calibration=1.05)],
            "修正 HX-1 流量系数",
        )
        versions = self.service.analysis_versions()
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[-1].metering_version, 2)
        self.assertEqual(versions[-1].note, "计量修订")

    def test_topology_revision_creates_new_analysis_version(self):
        self.service.register_topology(
            [HeatNode("SRC-1", "source"),
             HeatNode("HX-1", "exchange_station", "SRC-1")],
            "拓扑精简",
        )
        versions = self.service.analysis_versions()
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[-1].topology_version, 2)

    def test_same_combination_does_not_duplicate_version(self):
        self.service.run_diagnosis()
        self.service.run_diagnosis()
        self.assertEqual(len(self.service.analysis_versions()), 1)

    def test_diagnosis_run_tagged_with_current_version(self):
        ingest_imbalance_scene(self.service)
        run1 = self.service.run_diagnosis()
        self.assertEqual(run1.analysis_version, 1)
        self.service.revise_metering(
            ClimateCurve(40.0, 5.0, 1.0),
            [MeteringConfig("HX-1", 100.0)],
            "计量修订",
        )
        run2 = self.service.run_diagnosis()
        self.assertEqual(run2.analysis_version, 2)
        # 历史运行保留在旧版本下
        self.assertEqual(
            self.service.store.runs[run1.run_id].analysis_version, 1
        )

    def test_rule_revision_creates_new_analysis_version(self):
        self.service.register_rules(Thresholds(supply_deficit_c=2.5), "收紧阈值")
        versions = self.service.analysis_versions()
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[-1].rule_version, 2)


if __name__ == "__main__":
    unittest.main()
