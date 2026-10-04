import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from heating_diagnosis import HeatNode, HeatingDiagnosisService, RuleSet
from test_ingestion import make_service, seed


class VersionTests(unittest.TestCase):
    def test_initial_version_exists(self):
        service = HeatingDiagnosisService()
        self.assertEqual(service.store.versions[-1].version_id, "V1")

    def test_topology_revision_creates_new_version(self):
        service = make_service()  # 建拓扑后已是 V2
        self.assertEqual(service.store.versions[-1].version_id, "V2")
        version = service.revise_topology([HeatNode("BLDG-103", "building", "HX-2")])
        self.assertEqual(version.version_id, "V3")
        self.assertEqual(version.reason, "拓扑修订")
        report = service.diagnose("BLDG-103", "2026-01-05T22:00:00", "2026-01-06T06:00:00")
        self.assertEqual(report.version_id, "V3")

    def test_unchanged_topology_creates_no_version(self):
        service = make_service()
        before = len(service.store.versions)
        result = service.revise_topology([HeatNode("BLDG-101", "building", "HX-2")])
        self.assertIsNone(result)
        self.assertEqual(len(service.store.versions), before)

    def test_metering_revision_creates_new_version(self):
        service = make_service()
        version = service.revise_metering("更换 HX-2 流量计")
        self.assertIn("计量修订", version.reason)
        self.assertEqual(version.version_id, "V3")

    def test_rule_revision_records_rule_version(self):
        service = make_service()
        rules = RuleSet(rule_version="rules-2026.2", min_delta_t_c=10.0)
        version = service.revise_rules(rules)
        self.assertEqual(version.rule_version, "rules-2026.2")
        seed(service)
        report = service.diagnose("BLDG-101")
        sug = next(s for s in report.suggestions if s.rule_id == "R-LOW-DELTAT")
        self.assertEqual(sug.rule_version, "rules-2026.2")


if __name__ == "__main__":
    unittest.main()
