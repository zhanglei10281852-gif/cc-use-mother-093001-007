import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
CLI = ROOT / "heating_cli.py"

CONFIG = {
    "heating_window": {"start": "20:00", "end": "08:00"},
    "topology": {"note": "初始拓扑", "nodes": [
        {"node_id": "SRC-1", "node_type": "source"},
        {"node_id": "HX-1", "node_type": "exchange_station", "parent_id": "SRC-1"},
        {"node_id": "B-1", "node_type": "building", "parent_id": "HX-1",
         "attrs": {"households": 100}},
        {"node_id": "B-2", "node_type": "building", "parent_id": "HX-1",
         "attrs": {"households": 120}},
        {"node_id": "B-3", "node_type": "building", "parent_id": "HX-1"},
    ]},
    "metering": {
        "note": "初始计量",
        "curve": {"base_supply_c": 40.0, "ref_outdoor_c": 5.0, "slope": 1.0},
        "configs": [{"node_id": n, "design_flow_m3_h": 100.0}
                    for n in ("SRC-1", "HX-1", "B-1", "B-2", "B-3")],
    },
    "rules": {"note": "初始规则", "thresholds": {"supply_deficit_c": 3.0}},
}


def _sample(node, h, m, supply, day=6):
    return {"node_id": node, "ts": f"2026-01-{day:02d}T{h:02d}:{m:02d}:00",
            "supply_c": supply, "return_c": supply - 18.0, "flow_m3_h": 100.0}


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "state.json"
        (self.dir / "config.json").write_text(
            json.dumps(CONFIG, ensure_ascii=False), encoding="utf-8")
        (self.dir / "outdoor.json").write_text(json.dumps({
            "batch_id": "O-1", "kind": "outdoor",
            "items": [{"ts": f"2026-01-06T{h:02d}:00:00", "outdoor_c": -5.0}
                      for h in (20, 21, 22, 23)],
        }), encoding="utf-8")
        items = []
        for h, m in ((21, 0), (21, 30), (22, 0), (22, 30), (23, 0)):
            items.append(_sample("HX-1", h, m, 50.0))
            items.append(_sample("B-1", h, m, 43.0))
            items.append(_sample("B-2", h, m, 43.5))
            items.append(_sample("B-3", h, m, 50.0))
        (self.dir / "samples.json").write_text(json.dumps({
            "batch_id": "S-1", "kind": "samples", "items": items,
        }), encoding="utf-8")
        recovered = []
        for h, m in ((0, 0), (0, 30), (1, 0)):
            for n, supply in (("HX-1", 50.0), ("B-1", 49.5), ("B-2", 49.6), ("B-3", 50.0)):
                recovered.append(_sample(n, h, m, supply, day=7))
        (self.dir / "recovered.json").write_text(json.dumps({
            "batch_id": "S-2", "kind": "samples", "items": recovered,
        }), encoding="utf-8")
        (self.dir / "outdoor2.json").write_text(json.dumps({
            "batch_id": "O-2", "kind": "outdoor",
            "items": [{"ts": f"2026-01-07T{h:02d}:00:00", "outdoor_c": -5.0}
                      for h in (0, 1)],
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv, now="2026-01-06T23:30:00"):
        cmd = [sys.executable, str(CLI), "--db", str(self.db)]
        if now:
            cmd += ["--now", now]
        cmd += list(argv)
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(proc.returncode, 0, msg=f"stdout={proc.stdout}\nstderr={proc.stderr}")
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def test_full_flow_via_cli(self):
        cfg = self.run_cli("configure", "--file", str(self.dir / "config.json"))
        self.assertEqual(cfg["analysis_versions"], [1])

        result = self.run_cli("ingest", "--file", str(self.dir / "outdoor.json"))
        self.assertEqual(result["status"], "accepted")
        # 相同批次重复接入 → duplicate，不重复计入
        dup = self.run_cli("ingest", "--file", str(self.dir / "outdoor.json"))
        self.assertEqual(dup["status"], "duplicate")
        self.run_cli("ingest", "--file", str(self.dir / "samples.json"))

        run = self.run_cli("diagnose")
        self.assertEqual(run["analysis_version"], 1)
        self.assertEqual(len(run["interval_ids"]), 1)

        suggestions = self.run_cli("suggestions", "--status", "open")["suggestions"]
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["action_type"], "adjust")
        sug_id = suggestions[0]["suggestion_id"]

        explained = self.run_cli("explain", sug_id)
        self.assertTrue(explained["lines"])
        self.assertTrue(any("阈值" in line for line in explained["lines"]))

        order = self.run_cli("confirm", sug_id, "--by", "调度员-甲")
        self.assertEqual(order["status"], "open")
        # 重复确认不重复派单
        again = self.run_cli("confirm", sug_id, "--by", "调度员-乙")
        self.assertEqual(again["order_id"], order["order_id"])

        conclusion = self.run_cli(
            "complete", order["order_id"],
            "--cause", "hydraulic_imbalance", "--notes", "已调节支线阀门",
        )
        self.assertEqual(conclusion["status"], "confirmed")

        # 跨午夜处置后数据 → 对比改善
        self.run_cli("ingest", "--file", str(self.dir / "outdoor2.json"),
                     now="2026-01-07T01:30:00")
        self.run_cli("ingest", "--file", str(self.dir / "recovered.json"),
                     now="2026-01-07T01:30:00")
        report = self.run_cli("compare", order["order_id"], now="2026-01-07T01:30:00")
        self.assertTrue(report["improved"])
        self.assertLess(report["after"]["metrics"]["avg_supply_deficit_c"],
                        report["before"]["metrics"]["avg_supply_deficit_c"])

        versions = self.run_cli("versions")
        self.assertEqual(len(versions["analysis_versions"]), 1)

        events = self.run_cli("events", "--status", "closed")["events"]
        self.assertEqual(len(events), 1)
        detail = self.run_cli("event", events[0]["event_id"])
        self.assertEqual(detail["conclusion"]["status"], "confirmed")

    def test_demo_command(self):
        proc = subprocess.run(
            [sys.executable, str(CLI), "--db", str(self.dir / "demo.json"), "demo"],
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        payload = json.loads(proc.stdout)
        steps = [s["step"] for s in payload["demo"]]
        self.assertEqual(
            steps,
            ["configure", "ingest", "diagnose", "suggestions",
             "confirm", "feedback", "compare", "rediagnose"],
        )
        compare_step = payload["demo"][6]["report"]
        self.assertTrue(compare_step["improved"])


if __name__ == "__main__":
    unittest.main()
