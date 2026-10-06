"""命令行入口：配置、接入、诊断、建议、工单与处置前后对比。

用法：
    python heating_cli.py [--db STATE.json] [--now ISO时间] <子命令> ...

--now 注入固定时钟（用于回放/演示跨午夜窗口与临时停运），缺省用系统时钟。
状态持久化在 --db 指定的 JSON 文件中，多次调用可延续。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time
from pathlib import Path

from .api import configure_from_dict, parse_batch_items, serve as serve_http
from .clock import FixedClock, HeatingWindow, SystemClock
from .contracts import Complaint, HeatNode, OutdoorSnapshot, SamplePoint
from .ingestion import DataBatch
from .metering import ClimateCurve, MeteringConfig
from .rules import Thresholds
from .serde import to_jsonable
from .service import DiagnosisService


# ---------------------------------------------------------------------- 服务装载
def _load_service(args) -> DiagnosisService:
    clock = FixedClock(datetime.fromisoformat(args.now)) if args.now else SystemClock()
    db = Path(args.db)
    service = DiagnosisService.load(db, clock=clock) if db.exists() else DiagnosisService(clock=clock)
    start = service.store.settings.get("window_start")
    end = service.store.settings.get("window_end")
    if start and end:
        service.window = HeatingWindow(time.fromisoformat(start), time.fromisoformat(end))
    return service


def _save(service: DiagnosisService, args) -> None:
    service.save(args.db)


# ---------------------------------------------------------------------- 子命令
def cmd_configure(service, args):
    body = json.loads(Path(args.file).read_text(encoding="utf-8"))
    window = body.get("heating_window")
    if window:
        service.store.settings["window_start"] = window["start"]
        service.store.settings["window_end"] = window["end"]
        service.window = HeatingWindow(
            time.fromisoformat(window["start"]), time.fromisoformat(window["end"])
        )
    return configure_from_dict(service, body)


def cmd_ingest(service, args):
    body = json.loads(Path(args.file).read_text(encoding="utf-8"))
    batch = DataBatch(
        body["batch_id"], body["kind"],
        tuple(parse_batch_items(body["kind"], body.get("items", []))),
    )
    return to_jsonable(service.ingest(batch))


def cmd_diagnose(service, args):
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
    run = service.run_diagnosis(as_of=as_of, horizon_hours=args.horizon_hours)
    result = to_jsonable(run)
    result["events"] = to_jsonable([service.store.events[e] for e in run.event_ids])
    return result


def cmd_events(service, args):
    return {"events": to_jsonable(service.list_events(status=args.status))}


def cmd_event(service, args):
    event = service.get_event(args.event_id)
    return {
        "event": to_jsonable(event),
        "chain": to_jsonable(service.event_chain(event.event_id)),
        "conclusion": to_jsonable(service.get_conclusion(event.event_id)),
        "intervals": to_jsonable([service.store.intervals[i] for i in event.interval_ids]),
    }


def cmd_suggestions(service, args):
    return {"suggestions": to_jsonable(service.list_suggestions(status=args.status))}


def cmd_explain(service, args):
    return to_jsonable(service.explain_suggestion(args.suggestion_id))


def cmd_confirm(service, args):
    return to_jsonable(service.confirm_suggestion(args.suggestion_id, args.by))


def cmd_complete(service, args):
    return to_jsonable(service.complete_work_order(
        args.order_id,
        resolved=args.resolved,
        confirmed_cause=args.cause,
        notes=args.notes,
    ))


def cmd_compare(service, args):
    return to_jsonable(service.compare_disposal(args.order_id))


def cmd_versions(service, args):
    return {"analysis_versions": to_jsonable(service.analysis_versions())}


def cmd_serve(service, args):
    serve_http(service, host=args.host, port=args.port)
    return None


# ---------------------------------------------------------------------- 演示
def cmd_demo(service, args):
    """端到端演示：跨午夜采暖窗口内的水力失衡诊断 → 建议 → 工单 → 反馈 → 对比。"""
    demo_clock = FixedClock(datetime(2026, 1, 6, 23, 30))
    svc = DiagnosisService(clock=demo_clock, window=HeatingWindow(time(20, 0), time(8, 0)))
    steps: list[dict] = []

    nodes = [
        HeatNode("SRC-1", "source"),
        HeatNode("HX-1", "exchange_station", "SRC-1"),
        HeatNode("B-1", "building", "HX-1", {"households": 110}),
        HeatNode("B-2", "building", "HX-1", {"households": 96}),
        HeatNode("B-3", "building", "HX-1", {"households": 104}),
    ]
    svc.register_topology(nodes, "演示拓扑")
    svc.revise_metering(
        ClimateCurve(base_supply_c=40.0, ref_outdoor_c=5.0, slope=1.0),
        [MeteringConfig(n, 100.0) for n in
         ["SRC-1", "HX-1", "B-1", "B-2", "B-3"]],
        "演示计量",
    )
    svc.register_rules(Thresholds(), "演示规则")
    steps.append({"step": "configure", "analysis_versions":
                  [v.version for v in svc.analysis_versions()]})

    svc.ingest(DataBatch("D-OUT", "outdoor", tuple(
        OutdoorSnapshot(datetime(2026, 1, 6, h, 0), -5.0) for h in (20, 21, 22, 23)
    )))

    def sample(node, h, m, supply, day=6):
        return SamplePoint(node, datetime(2026, 1, day, h, m), supply, supply - 18.0, 100.0)

    items = []
    for h, m in ((21, 0), (21, 30), (22, 0), (22, 30), (23, 0)):
        items.append(sample("HX-1", h, m, 50.0))   # 站侧总供正常（期望 50°C）
        items.append(sample("B-1", h, m, 43.0))    # B-1 欠供 7°C
        items.append(sample("B-2", h, m, 43.5))    # B-2 欠供 6.5°C
        items.append(sample("B-3", h, m, 50.0))    # B-3 正常
    svc.ingest(DataBatch("D-S1", "samples", tuple(items)))
    svc.ingest(DataBatch("D-C1", "complaints", tuple([
        Complaint("C-1", "B-1", datetime(2026, 1, 6, 21, 40), detail="卧室 15°C"),
    ])))
    steps.append({"step": "ingest", "batches": ["D-OUT", "D-S1", "D-C1"]})

    run = svc.run_diagnosis()
    steps.append({"step": "diagnose", "run": to_jsonable(run)})

    suggestions = svc.list_suggestions(status="open")
    steps.append({"step": "suggestions",
                  "items": to_jsonable(suggestions),
                  "explain": to_jsonable(svc.explain_suggestion(suggestions[0].suggestion_id))})

    order = svc.confirm_suggestion(suggestions[0].suggestion_id, "调度员-王")
    steps.append({"step": "confirm", "order": to_jsonable(order)})

    conclusion = svc.complete_work_order(
        order.order_id, resolved=True,
        confirmed_cause="hydraulic_imbalance", notes="已调节 B-1/B-2 支线阀门",
    )
    steps.append({"step": "feedback", "conclusion": to_jsonable(conclusion)})

    # 跨午夜：处置后新批次（次日 00:00-01:00），欠供消除
    demo_clock.set(datetime(2026, 1, 7, 1, 30))
    svc.ingest(DataBatch("D-OUT2", "outdoor", tuple(
        OutdoorSnapshot(datetime(2026, 1, 7, h, 0), -5.0) for h in (0, 1)
    )))
    recovered = []
    for h, m in ((0, 0), (0, 30), (1, 0)):
        recovered.append(sample("HX-1", h, m, 50.0, day=7))
        recovered.append(sample("B-1", h, m, 49.5, day=7))
        recovered.append(sample("B-2", h, m, 49.6, day=7))
        recovered.append(sample("B-3", h, m, 50.0, day=7))
    svc.ingest(DataBatch("D-S2", "samples", tuple(recovered)))
    steps.append({"step": "compare",
                  "report": to_jsonable(svc.compare_disposal(order.order_id))})

    run2 = svc.run_diagnosis()
    steps.append({"step": "rediagnose",
                  "open_events": [e.event_id for e in svc.list_events(status="open")],
                  "run": to_jsonable(run2)})
    return {"demo": steps}


# ---------------------------------------------------------------------- 入口
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="heating_cli", description="供热运行诊断与处置命令行")
    parser.add_argument("--db", default="heating_state.json", help="状态文件路径")
    parser.add_argument("--now", default=None, help="注入固定时钟，如 2026-01-06T23:30:00")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("configure", help="从 JSON 文件注册拓扑/计量/规则/采暖窗口")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_configure)

    p = sub.add_parser("ingest", help="幂等接入数据批次（JSON 文件）")
    p.add_argument("--file", required=True)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("diagnose", help="运行诊断")
    p.add_argument("--horizon-hours", type=int, default=24)
    p.add_argument("--as-of", default=None, help="诊断基准时刻（ISO），缺省取注入时钟")
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser("events", help="列出异常事件")
    p.add_argument("--status", default=None, choices=["open", "closed"])
    p.set_defaults(func=cmd_events)

    p = sub.add_parser("event", help="查看事件详情与复发链")
    p.add_argument("event_id")
    p.set_defaults(func=cmd_event)

    p = sub.add_parser("suggestions", help="列出处置建议")
    p.add_argument("--status", default=None, choices=["open", "confirmed", "superseded"])
    p.set_defaults(func=cmd_suggestions)

    p = sub.add_parser("explain", help="说明建议的证据链")
    p.add_argument("suggestion_id")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("confirm", help="确认建议并生成工单")
    p.add_argument("suggestion_id")
    p.add_argument("--by", default="dispatcher", help="调度员标识")
    p.set_defaults(func=cmd_confirm)

    p = sub.add_parser("complete", help="回填工单执行结果并反馈诊断结论")
    p.add_argument("order_id")
    p.add_argument("--cause", required=True,
                   choices=["equipment_outage", "hydraulic_imbalance", "user_side", "no_issue"])
    p.add_argument("--resolved", action="store_true", default=True)
    p.add_argument("--unresolved", dest="resolved", action="store_false")
    p.add_argument("--notes", default="")
    p.set_defaults(func=cmd_complete)

    p = sub.add_parser("compare", help="对比工单处置前后指标")
    p.add_argument("order_id")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("versions", help="列出分析版本")
    p.set_defaults(func=cmd_versions)

    p = sub.add_parser("serve", help="启动 HTTP 接口服务")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=cmd_serve, persist=False)

    p = sub.add_parser("demo", help="运行端到端演示（内存态，不写状态文件）")
    p.set_defaults(func=cmd_demo, persist=False)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    service = _load_service(args)
    try:
        result = args.func(service, args)
    except (KeyError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    if getattr(args, "persist", True):
        _save(service, args)
    if result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
