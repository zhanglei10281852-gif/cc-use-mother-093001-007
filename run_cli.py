"""命令行冒烟：跨午夜采暖窗口内完成 诊断→派单→反馈→对比 闭环。

无参数：运行内存中的端到端演示并打印 JSON 摘要。
带参数：转发给完整命令行（状态落盘 heating_state.json），如
    python run_cli.py diagnose --node BLDG-101 --window 22:00-06:00
"""
import json
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

if len(sys.argv) > 1:
    from heating_diagnosis.cli import main
    raise SystemExit(main(sys.argv[1:]))

from heating_diagnosis import (DailyHeatingWindow, FixedClock, HeatNode,
                               HeatingDiagnosisService)

clock = FixedClock(datetime(2026, 1, 5, 23, 30))  # 注入时钟：处于跨午夜窗口内
service = HeatingDiagnosisService(clock=clock,
                                  window=DailyHeatingWindow(time(22, 0), time(6, 0)))
service.revise_topology([
    HeatNode("SOURCE-1", "heat_source"),
    HeatNode("HX-2", "exchange_station", "SOURCE-1"),
    HeatNode("BLDG-101", "building", "HX-2"),
    HeatNode("BLDG-102", "building", "HX-2"),
])

start = datetime(2026, 1, 5, 22, 0)


def series(node, supply, ret, flow):
    return [{"node_id": node, "ts": (start + timedelta(hours=i)).isoformat(),
             "supply_c": supply, "return_c": ret, "flow_m3_h": flow}
            for i in range(9)]  # 22:00 → 次日 06:00，跨午夜


service.ingest_batch("B-samples", "samples",
                     series("SOURCE-1", 62, 42, 300) + series("HX-2", 52, 40, 120)
                     + series("BLDG-101", 38, 34, 40) + series("BLDG-102", 50, 41, 20))
again = service.ingest_batch("B-samples", "samples", [])  # 幂等：同批次不重复计入
service.ingest_batch("B-outdoor", "outdoor",
                     [{"ts": "2026-01-05T20:00:00", "outdoor_c": -5.0}])
service.ingest_batch("B-complaints", "complaints", [
    {"complaint_id": f"C-{i}", "node_id": "BLDG-101",
     "ts": (start + timedelta(hours=2 + i)).isoformat(), "detail": "卧室 15℃"}
    for i in range(3)])

report = service.diagnose("BLDG-101")  # 未给起止时间 → 按注入时钟取当前跨午夜窗口
suggestion = report.suggestions[0]
order = service.confirm_suggestion(suggestion.suggestion_id, "调度员甲")
clock.set(datetime(2026, 1, 6, 12, 0))
event = service.record_feedback(order.order_id, True, "已调平衡阀并提高二供设定")

after = datetime(2026, 1, 6, 22, 0)
service.ingest_batch("B-after", "samples", [
    {"node_id": "BLDG-101", "ts": (after + timedelta(hours=i)).isoformat(),
     "supply_c": 50.0, "return_c": 41.0, "flow_m3_h": 22.0} for i in range(8)])
comparison = service.compare_metrics(
    "BLDG-101",
    ("2026-01-05T22:00:00", "2026-01-06T06:00:00"),
    ("2026-01-06T22:00:00", "2026-01-07T06:00:00"))

print(json.dumps({
    "采暖窗口": [report.window_start.isoformat(), report.window_end.isoformat()],
    "重复批次已跳过": again.skipped,
    "影响范围": report.impact_scope,
    "异常区间": [{"kind": i.kind, "start": i.start.isoformat(),
                "end": i.end.isoformat(), "样本数": i.sample_count}
               for i in report.intervals],
    "建议": {"id": suggestion.suggestion_id, "action": suggestion.action,
            "规则版本": suggestion.rule_version, "证据": suggestion.evidence},
    "工单": order.order_id,
    "诊断结论": event.conclusion,
    "处置前后对比": comparison["summary"],
}, ensure_ascii=False, indent=2, default=str))
