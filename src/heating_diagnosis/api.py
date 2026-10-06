"""基于标准库的 HTTP 接口（无外部依赖）。

路由分发与传输层分离：dispatch() 可直接在测试中调用，
make_handler()/serve() 提供真实 HTTP 服务。
"""
from __future__ import annotations

import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .contracts import Complaint, HeatNode, OutdoorSnapshot, OutageRecord, SamplePoint
from .ingestion import DataBatch
from .metering import ClimateCurve, MeteringConfig
from .rules import Thresholds
from .serde import to_jsonable
from .service import DiagnosisService


def parse_batch_items(kind: str, items: list[dict]) -> list:
    """把 JSON 条目解析为对应契约对象。"""
    if kind == "samples":
        return [
            SamplePoint(i["node_id"], datetime.fromisoformat(i["ts"]),
                        float(i["supply_c"]), float(i["return_c"]), float(i["flow_m3_h"]))
            for i in items
        ]
    if kind == "outdoor":
        return [
            OutdoorSnapshot(datetime.fromisoformat(i["ts"]), float(i["outdoor_c"]),
                            float(i.get("wind_ms", 0.0)))
            for i in items
        ]
    if kind == "complaints":
        return [
            Complaint(i["complaint_id"], i["node_id"], datetime.fromisoformat(i["ts"]),
                      i.get("kind", "room_temp_low"), i.get("detail", ""))
            for i in items
        ]
    if kind == "outages":
        return [
            OutageRecord(i["outage_id"], i["node_id"], datetime.fromisoformat(i["start"]),
                         datetime.fromisoformat(i["end"]) if i.get("end") else None,
                         i.get("kind", "planned"), i.get("reason", ""))
            for i in items
        ]
    raise ValueError(f"未知批次类型: {kind}")


def configure_from_dict(service: DiagnosisService, body: dict) -> dict:
    """按配置字典注册拓扑 / 计量 / 规则（均可选），返回各版本号。"""
    result: dict = {}
    if "topology" in body:
        t = body["topology"]
        nodes = [
            HeatNode(n["node_id"], n["node_type"], n.get("parent_id"), n.get("attrs") or {})
            for n in t.get("nodes", [])
        ]
        result["topology_version"] = service.register_topology(
            nodes, t.get("note", "")
        ).version
    if "metering" in body:
        m = body["metering"]
        c = m["curve"]
        curve = ClimateCurve(c["base_supply_c"], c["ref_outdoor_c"], c["slope"])
        configs = [
            MeteringConfig(x["node_id"], float(x["design_flow_m3_h"]),
                           float(x.get("calibration", 1.0)),
                           float(x.get("expected_delta_t_c", 20.0)))
            for x in m.get("configs", [])
        ]
        result["metering_version"] = service.revise_metering(
            curve, configs, m.get("note", "")
        ).version
    if "rules" in body:
        r = body["rules"]
        thresholds = Thresholds(**r.get("thresholds", {}))
        result["rule_version"] = service.register_rules(
            thresholds, r.get("note", "")
        ).version
    result["analysis_versions"] = [v.version for v in service.analysis_versions()]
    return result


def dispatch(service: DiagnosisService, method: str, path: str,
             body: dict | None) -> tuple[int, dict]:
    """无传输层的路由分发，返回 (HTTP 状态码, 响应体)。"""
    body = body or {}
    parsed = urlparse(path)
    parts = [p for p in parsed.path.split("/") if p]
    query = parse_qs(parsed.query)
    try:
        if parts == ["health"] and method == "GET":
            return 200, {"ok": True}
        if parts == ["configure"] and method == "POST":
            return 200, to_jsonable(configure_from_dict(service, body))
        if parts == ["ingest"] and method == "POST":
            batch = DataBatch(
                body["batch_id"], body["kind"],
                tuple(parse_batch_items(body["kind"], body.get("items", []))),
            )
            return 200, to_jsonable(service.ingest(batch))
        if parts == ["diagnosis", "run"] and method == "POST":
            as_of = datetime.fromisoformat(body["as_of"]) if body.get("as_of") else None
            run = service.run_diagnosis(
                as_of=as_of, horizon_hours=int(body.get("horizon_hours", 24))
            )
            return 200, to_jsonable(run)
        if parts == ["events"] and method == "GET":
            events = service.list_events(status=query.get("status", [None])[0])
            return 200, {"events": to_jsonable(events)}
        if len(parts) == 2 and parts[0] == "events" and method == "GET":
            event = service.get_event(parts[1])
            return 200, {
                "event": to_jsonable(event),
                "chain": to_jsonable(service.event_chain(event.event_id)),
                "conclusion": to_jsonable(service.get_conclusion(event.event_id)),
            }
        if parts == ["suggestions"] and method == "GET":
            suggestions = service.list_suggestions(status=query.get("status", [None])[0])
            return 200, {"suggestions": to_jsonable(suggestions)}
        if len(parts) == 3 and parts[0] == "suggestions" and parts[2] == "explain" and method == "GET":
            return 200, to_jsonable(service.explain_suggestion(parts[1]))
        if len(parts) == 3 and parts[0] == "suggestions" and parts[2] == "confirm" and method == "POST":
            order = service.confirm_suggestion(parts[1], body.get("operator", "dispatcher"))
            return 200, to_jsonable(order)
        if len(parts) == 3 and parts[0] == "work-orders" and parts[2] == "complete" and method == "POST":
            conclusion = service.complete_work_order(
                parts[1],
                resolved=bool(body.get("resolved", True)),
                confirmed_cause=body["confirmed_cause"],
                notes=body.get("notes", ""),
            )
            return 200, to_jsonable(conclusion)
        if len(parts) == 3 and parts[0] == "work-orders" and parts[2] == "compare" and method == "GET":
            return 200, to_jsonable(service.compare_disposal(parts[1]))
        if parts == ["versions"] and method == "GET":
            return 200, {"analysis_versions": to_jsonable(service.analysis_versions())}
        return 404, {"error": f"未知路由: {method} {parsed.path}"}
    except KeyError as exc:
        return 404, {"error": str(exc)}
    except (ValueError, RuntimeError, TypeError) as exc:
        return 400, {"error": str(exc)}


def make_handler(service: DiagnosisService):
    class Handler(BaseHTTPRequestHandler):
        def _respond(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {}
            status, payload = dispatch(service, method, self.path, body)
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - 标准库约定
            self._respond("GET")

        def do_POST(self) -> None:  # noqa: N802 - 标准库约定
            self._respond("POST")

        def log_message(self, *args) -> None:
            pass

    return Handler


def make_server(service: DiagnosisService, host: str = "127.0.0.1",
                port: int = 8080) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(service))


def serve(service: DiagnosisService, host: str = "127.0.0.1", port: int = 8080) -> None:
    server = make_server(service, host, port)
    print(f"供热诊断服务已启动: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
