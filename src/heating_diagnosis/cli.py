"""命令行入口：数据接入、诊断、建议确认、工单反馈与指标对比。"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import time
from pathlib import Path

from .contracts import HeatNode
from .service import HeatingDiagnosisService
from .store import StateStore
from .windows import DailyHeatingWindow


def _hhmm(text: str) -> time:
    hh, mm = text.split(":")
    return time(int(hh), int(mm))


def _window(text: str | None) -> DailyHeatingWindow | None:
    if not text:
        return None
    start, end = text.split("-")
    return DailyHeatingWindow(_hhmm(start), _hhmm(end))


def _dump(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="heating", description="供热运行诊断与处置服务")
    p.add_argument("--state", default="heating_state.json", help="状态文件路径")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("topology", help="新增或修订拓扑节点（有变化时创建新分析版本）")
    t.add_argument("--node", required=True)
    t.add_argument("--type", required=True, dest="node_type")
    t.add_argument("--parent", default=None)

    m = sub.add_parser("metering", help="登记计量修订（创建新分析版本）")
    m.add_argument("--note", required=True)

    i = sub.add_parser("ingest", help="幂等接入数据批次（相同批次号不重复计入）")
    i.add_argument("--batch", required=True)
    i.add_argument("--kind", required=True,
                   choices=["samples", "outdoor", "outages", "complaints"])
    i.add_argument("--file", required=True, help="JSON 数组文件")

    d = sub.add_parser("diagnose", help="诊断节点异常区间并生成建议")
    d.add_argument("--node", required=True)
    d.add_argument("--start", help="ISO 时间；与 --end 一起省略时按采暖窗口取当前窗口")
    d.add_argument("--end", help="ISO 时间")
    d.add_argument("--window", help="每日采暖窗口，如 22:00-06:00（支持跨午夜）")

    c = sub.add_parser("confirm", help="调度员确认建议并生成工单")
    c.add_argument("--suggestion", required=True)
    c.add_argument("--by", required=True, help="调度员")

    f = sub.add_parser("feedback", help="录入工单执行结果并反馈诊断结论")
    f.add_argument("--order", required=True)
    f.add_argument("--resolved", choices=["yes", "no"], required=True)
    f.add_argument("--note", default="")

    cl = sub.add_parser("close", help="关闭事件（之后复发将关联而非覆盖）")
    cl.add_argument("--event", required=True)
    cl.add_argument("--note", default="")

    cm = sub.add_parser("compare", help="比较处置前后指标")
    cm.add_argument("--node", required=True)
    cm.add_argument("--before-start", required=True)
    cm.add_argument("--before-end", required=True)
    cm.add_argument("--after-start", required=True)
    cm.add_argument("--after-end", required=True)

    e = sub.add_parser("explain", help="说明一条建议的证据与规则版本")
    e.add_argument("--suggestion", required=True)

    ev = sub.add_parser("events", help="列出诊断事件")
    ev.add_argument("--node")

    sub.add_parser("outages", help="列出进行中的停运（含临时停运）")
    return p


def main(argv=None, service: HeatingDiagnosisService | None = None) -> int:
    args = build_parser().parse_args(argv)
    own = service is None
    if own:
        store = StateStore.load(args.state) if Path(args.state).exists() else StateStore()
        service = HeatingDiagnosisService(
            store=store, window=_window(getattr(args, "window", None)))
    cmd = args.cmd
    if cmd == "topology":
        version = service.revise_topology(
            [HeatNode(args.node, args.node_type, args.parent)])
        _dump({"version": asdict(version) if version else None,
               "message": "已创建新分析版本" if version else "拓扑无变化"})
    elif cmd == "metering":
        _dump({"version": asdict(service.revise_metering(args.note))})
    elif cmd == "ingest":
        records = json.loads(Path(args.file).read_text(encoding="utf-8"))
        _dump(asdict(service.ingest_batch(args.batch, args.kind, records)))
    elif cmd == "diagnose":
        _dump(asdict(service.diagnose(args.node, args.start, args.end)))
    elif cmd == "confirm":
        _dump(asdict(service.confirm_suggestion(args.suggestion, args.by)))
    elif cmd == "feedback":
        _dump(asdict(service.record_feedback(args.order, args.resolved == "yes", args.note)))
    elif cmd == "close":
        _dump(asdict(service.close_event(args.event, args.note)))
    elif cmd == "compare":
        _dump(service.compare_metrics(
            args.node, (args.before_start, args.before_end),
            (args.after_start, args.after_end)))
    elif cmd == "explain":
        _dump(service.explain_suggestion(args.suggestion))
    elif cmd == "events":
        _dump([asdict(e) for e in service.store.events.values()
               if not args.node or e.node_id == args.node])
    elif cmd == "outages":
        _dump([asdict(o) for o in service.active_outages()])
    if own:
        service.store.save(args.state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
