import json
import threading
import unittest
import urllib.request
from datetime import datetime

from helpers import make_bare_service, make_service
from heating_diagnosis.api import dispatch, make_server

CONFIG = {
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


class ApiDispatchTests(unittest.TestCase):
    def setUp(self):
        self.service, self.clock = make_bare_service()

    def test_full_disposal_flow_over_api(self):
        status, cfg = dispatch(self.service, "POST", "/configure", CONFIG)
        self.assertEqual(status, 200)
        self.assertEqual(cfg["analysis_versions"], [1])

        # 幂等接入：同一批次重复提交不重复计入
        outdoor_batch = {"batch_id": "O-1", "kind": "outdoor", "items": [
            {"ts": f"2026-01-06T{h:02d}:00:00", "outdoor_c": -5.0} for h in (20, 21, 22, 23)
        ]}
        self.assertEqual(dispatch(self.service, "POST", "/ingest", outdoor_batch)[1]["status"], "accepted")
        self.assertEqual(dispatch(self.service, "POST", "/ingest", outdoor_batch)[1]["status"], "duplicate")

        items = []
        for h, m in ((21, 0), (21, 30), (22, 0), (22, 30), (23, 0)):
            items.append(_sample("HX-1", h, m, 50.0))
            items.append(_sample("B-1", h, m, 43.0))
            items.append(_sample("B-2", h, m, 43.5))
            items.append(_sample("B-3", h, m, 50.0))
        status, result = dispatch(self.service, "POST", "/ingest",
                                  {"batch_id": "S-1", "kind": "samples", "items": items})
        self.assertEqual(result["status"], "accepted")

        status, run = dispatch(self.service, "POST", "/diagnosis/run", {})
        self.assertEqual(status, 200)
        self.assertEqual(run["analysis_version"], 1)

        status, body = dispatch(self.service, "GET", "/suggestions?status=open", None)
        suggestions = body["suggestions"]
        self.assertEqual(len(suggestions), 1)
        suggestion_id = suggestions[0]["suggestion_id"]
        self.assertEqual(suggestions[0]["action_type"], "adjust")

        # 每条建议的证据说明
        status, explained = dispatch(
            self.service, "GET", f"/suggestions/{suggestion_id}/explain", None)
        self.assertEqual(status, 200)
        self.assertTrue(explained["lines"])
        self.assertTrue(any("阈值" in line for line in explained["lines"]))

        status, order = dispatch(self.service, "POST",
                                 f"/suggestions/{suggestion_id}/confirm",
                                 {"operator": "调度员-甲"})
        self.assertEqual(status, 200)
        order_id = order["order_id"]

        status, conclusion = dispatch(self.service, "POST",
                                      f"/work-orders/{order_id}/complete",
                                      {"resolved": True,
                                       "confirmed_cause": "hydraulic_imbalance",
                                       "notes": "已调节支线阀门"})
        self.assertEqual(status, 200)
        self.assertEqual(conclusion["status"], "confirmed")

        # 处置后数据（跨午夜）→ 对比改善
        self.clock.set(datetime(2026, 1, 7, 1, 30))
        dispatch(self.service, "POST", "/ingest", {
            "batch_id": "O-2", "kind": "outdoor",
            "items": [{"ts": f"2026-01-07T{h:02d}:00:00", "outdoor_c": -5.0}
                      for h in (0, 1)],
        })
        recovered = []
        for h, m in ((0, 0), (0, 30), (1, 0)):
            for n, supply in (("HX-1", 50.0), ("B-1", 49.5), ("B-2", 49.6), ("B-3", 50.0)):
                recovered.append(_sample(n, h, m, supply, day=7))
        dispatch(self.service, "POST", "/ingest",
                 {"batch_id": "S-2", "kind": "samples", "items": recovered})

        status, report = dispatch(self.service, "GET",
                                  f"/work-orders/{order_id}/compare", None)
        self.assertEqual(status, 200)
        self.assertTrue(report["improved"])
        self.assertLess(report["after"]["metrics"]["avg_supply_deficit_c"],
                        report["before"]["metrics"]["avg_supply_deficit_c"])

        status, events = dispatch(self.service, "GET", "/events", None)
        self.assertEqual(status, 200)
        self.assertEqual(events["events"][0]["status"], "closed")

        status, versions = dispatch(self.service, "GET", "/versions", None)
        self.assertEqual(len(versions["analysis_versions"]), 1)

    def test_unknown_route_404(self):
        status, body = dispatch(self.service, "GET", "/nope", None)
        self.assertEqual(status, 404)

    def test_missing_resource_404(self):
        status, body = dispatch(self.service, "GET", "/suggestions/SUG-99/explain", None)
        self.assertEqual(status, 404)

    def test_bad_batch_400(self):
        status, body = dispatch(self.service, "POST", "/ingest",
                                {"batch_id": "X", "kind": "telemetry", "items": []})
        self.assertEqual(status, 400)


class ApiHttpTests(unittest.TestCase):
    def test_real_http_server_smoke(self):
        service, _clock = make_service()
        server = make_server(service, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health") as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(json.loads(resp.read()), {"ok": True})
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/diagnosis/run",
                data=b"{}", headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
