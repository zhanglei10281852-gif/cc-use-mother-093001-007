"""供热运行诊断与处置服务：数据接入、诊断、建议、工单与指标对比。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .analyzer import Analyzer, AnomalyInterval, nearest_outdoor, sample_violations
from .clock import SystemClock
from .contracts import HeatNode
from .models import (AnalysisVersion, Complaint, DiagnosisEvent, EquipmentOutage,
                     OutdoorSnapshot, Suggestion, TimedSample, WorkOrder, parse_ts)
from .rules import RuleEngine, RuleSet
from .store import StateStore
from .windows import DailyHeatingWindow


@dataclass
class IngestResult:
    batch_id: str
    kind: str
    accepted: int
    skipped: bool
    message: str


@dataclass
class DiagnosisReport:
    node_id: str
    version_id: str
    window_start: datetime
    window_end: datetime
    impact_scope: list[str]
    intervals: list[AnomalyInterval]
    events: list[DiagnosisEvent]
    suggestions: list[Suggestion]
    complaints: list[Complaint]
    excluded_outage_samples: int
    outage_notes: list[str]


def _sample(r) -> TimedSample:
    if isinstance(r, TimedSample):
        return r
    return TimedSample(r["node_id"], parse_ts(r["ts"]), float(r["supply_c"]),
                       float(r["return_c"]), float(r["flow_m3_h"]))


def _outdoor(r) -> OutdoorSnapshot:
    if isinstance(r, OutdoorSnapshot):
        return r
    return OutdoorSnapshot(parse_ts(r["ts"]), float(r["outdoor_c"]),
                           float(r.get("wind_m_s", 0.0)))


def _outage(r) -> EquipmentOutage:
    if isinstance(r, EquipmentOutage):
        return r
    end = r.get("end")
    return EquipmentOutage(r["outage_id"], r["node_id"], parse_ts(r["start"]),
                           parse_ts(end) if end else None, r.get("reason", ""))


def _complaint(r) -> Complaint:
    if isinstance(r, Complaint):
        return r
    return Complaint(r["complaint_id"], r["node_id"], parse_ts(r["ts"]),
                     r.get("detail", ""))


_PARSERS = {"samples": _sample, "outdoor": _outdoor,
            "outages": _outage, "complaints": _complaint}


class HeatingDiagnosisService:
    """诊断与处置门面；时钟可注入，用于跨午夜采暖窗口与临时停运判定。"""

    def __init__(self, store: StateStore | None = None, clock=None,
                 rules: RuleSet | None = None, window: DailyHeatingWindow | None = None):
        self.store = store or StateStore()
        self.clock = clock or SystemClock()
        self.rules = rules or RuleSet()
        self.window = window
        self.analyzer = Analyzer(self.rules)
        self.engine = RuleEngine(self.rules)
        if not self.store.versions:
            self.store.versions.append(self._make_version("初始版本"))

    # ---- 分析版本 ----
    def _make_version(self, reason: str) -> AnalysisVersion:
        return AnalysisVersion(f"V{len(self.store.versions) + 1}",
                               self.rules.rule_version,
                               self.store.topology.revision,
                               reason, self.clock.now())

    def revise_topology(self, nodes) -> AnalysisVersion | None:
        """拓扑修订：有实际变化才创建新分析版本。"""
        changed = False
        for n in nodes:
            node = n if isinstance(n, HeatNode) else HeatNode(**n)
            changed |= self.store.topology.add_or_revise(node)
        if not changed:
            return None
        version = self._make_version("拓扑修订")
        self.store.versions.append(version)
        return version

    def revise_metering(self, note: str) -> AnalysisVersion:
        version = self._make_version(f"计量修订：{note}")
        self.store.versions.append(version)
        return version

    def revise_rules(self, rules: RuleSet, reason: str = "规则修订") -> AnalysisVersion:
        self.rules = rules
        self.analyzer = Analyzer(rules)
        self.engine = RuleEngine(rules)
        version = self._make_version(reason)
        self.store.versions.append(version)
        return version

    # ---- 数据接入（幂等） ----
    def ingest_batch(self, batch_id: str, kind: str, records) -> IngestResult:
        """相同批次号只入账一次，重复提交直接跳过。"""
        if batch_id in self.store.applied_batches:
            return IngestResult(batch_id, kind, 0, True, "批次已入账，重复数据未计入")
        try:
            parser = _PARSERS[kind]
        except KeyError:
            raise ValueError(f"未知数据类型 {kind}") from None
        items = [parser(r) for r in records]
        {"samples": self.store.samples, "outdoor": self.store.outdoor,
         "outages": self.store.outages,
         "complaints": self.store.complaints}[kind].extend(items)
        self.store.applied_batches.add(batch_id)
        return IngestResult(batch_id, kind, len(items), False, "入账完成")

    # ---- 诊断 ----
    def diagnose(self, node_id: str, start=None, end=None) -> DiagnosisReport:
        start, end = self._resolve_window(start, end)
        version = self.store.versions[-1]
        analysis = self.analyzer.analyze(
            node_id, self.store.samples_for(node_id, start, end),
            self.store.outdoor, self.store.outages_for(node_id), start, end,
            self._source_samples(node_id, start, end))
        scope = self.store.topology.impact_scope(node_id)
        complaints = self.store.complaints_in(scope, start, end)
        intervals = list(analysis.intervals)
        if len(complaints) >= self.rules.complaint_threshold:
            intervals.append(AnomalyInterval(
                "complaint_cluster", node_id, start, end, 0,
                [f"窗口内投诉 {len(complaints)} 起，达到聚集阈值 {self.rules.complaint_threshold}"],
                {"complaints": len(complaints)}))
        events, suggestions = [], []
        for interval in intervals:
            event = self._upsert_event(node_id, interval, version)
            events.append(event)
            suggestion = self._ensure_suggestion(event, interval, complaints, start, end)
            if suggestion is not None:
                suggestions.append(suggestion)
        return DiagnosisReport(node_id, version.version_id, start, end, scope,
                               intervals, events, suggestions, complaints,
                               analysis.excluded_outage_samples, analysis.outage_notes)

    def _resolve_window(self, start, end) -> tuple[datetime, datetime]:
        if start is not None and end is not None:
            return parse_ts(start), parse_ts(end)
        if self.window is None:
            raise ValueError("未指定起止时间，且未配置每日采暖窗口")
        return self.window.current_interval(self.clock)

    def _source_samples(self, node_id: str, start: datetime, end: datetime):
        node = self.store.topology.get(node_id)
        if node.node_type != "exchange_station" or node.parent_id is None:
            return None
        return self.store.samples_for(node.parent_id, start, end)

    def _upsert_event(self, node_id: str, interval: AnomalyInterval,
                      version: AnalysisVersion) -> DiagnosisEvent:
        fingerprint = f"{node_id}:{interval.kind}"
        for event in self.store.events.values():
            if event.fingerprint == fingerprint and event.status == "open":
                event.interval_start = min(event.interval_start, interval.start)
                event.interval_end = max(event.interval_end, interval.end)
                event.evidence = list(interval.evidence)
                event.version_id = version.version_id
                return event
        prior = [e for e in self.store.events.values()
                 if e.fingerprint == fingerprint and e.status in ("closed", "resolved")]
        prior.sort(key=lambda e: e.interval_end)
        linked = prior[-1] if prior else None
        event = DiagnosisEvent(
            event_id=self.store.next_id("E"),
            fingerprint=fingerprint,
            node_id=node_id,
            anomaly_kind=interval.kind,
            status="open",
            interval_start=interval.start,
            interval_end=interval.end,
            version_id=version.version_id,
            conclusion=(f"历史事件 {linked.event_id} 关闭后复发，已关联而非覆盖"
                        if linked else "待处置"),
            evidence=([f"复发关联：{linked.event_id}（状态 {linked.status}）"] if linked else [])
                     + list(interval.evidence),
            reoccurrence_of=linked.event_id if linked else None)
        self.store.events[event.event_id] = event
        return event

    def _ensure_suggestion(self, event: DiagnosisEvent, interval: AnomalyInterval,
                           complaints, start, end) -> Suggestion | None:
        for s in self.store.suggestions.values():
            if s.event_id == event.event_id and s.status in ("proposed", "confirmed"):
                return s
        context = {"flow_share": self._flow_share(event.node_id, start, end),
                   "complaints": complaints}
        suggestion = self.engine.suggest(event, interval, context, self.store.next_id("S"))
        if suggestion is not None:
            self.store.suggestions[suggestion.suggestion_id] = suggestion
        return suggestion

    def _flow_share(self, node_id: str, start: datetime, end: datetime) -> float | None:
        """节点均流 / 同站其他楼栋均流；无法计算返回 None。"""
        node = self.store.topology.get(node_id)
        if node.parent_id is None:
            return None
        own = self.store.samples_for(node_id, start, end)
        if not own:
            return None
        siblings = [c.node_id for c in self.store.topology.children(node.parent_id)
                    if c.node_id != node_id]
        sib = [s for sid in siblings for s in self.store.samples_for(sid, start, end)]
        if not sib:
            return None
        sib_avg = sum(s.flow_m3_h for s in sib) / len(sib)
        if sib_avg <= 0:
            return None
        own_avg = sum(s.flow_m3_h for s in own) / len(own)
        return own_avg / sib_avg

    # ---- 建议确认与工单 ----
    def confirm_suggestion(self, suggestion_id: str, dispatcher: str) -> WorkOrder:
        try:
            suggestion = self.store.suggestions[suggestion_id]
        except KeyError:
            raise KeyError(f"未知建议 {suggestion_id}") from None
        if suggestion.status != "proposed":
            raise ValueError(f"建议 {suggestion_id} 已处理（{suggestion.status}），不能重复确认")
        suggestion.status = "confirmed"
        order = WorkOrder(self.store.next_id("W"), suggestion_id, suggestion.event_id,
                          suggestion.node_id, suggestion.action, dispatcher,
                          self.clock.now())
        self.store.orders[order.order_id] = order
        event = self.store.events[suggestion.event_id]
        event.conclusion = f"建议 {suggestion_id} 已确认，生成工单 {order.order_id}（{suggestion.action}）"
        return order

    def record_feedback(self, order_id: str, resolved: bool, note: str = "") -> DiagnosisEvent:
        """工单执行结果必须反馈到诊断结论。"""
        try:
            order = self.store.orders[order_id]
        except KeyError:
            raise KeyError(f"未知工单 {order_id}") from None
        if order.status == "done":
            raise ValueError(f"工单 {order_id} 已闭环，不能重复反馈")
        order.status = "done"
        order.result = {"resolved": resolved, "note": note,
                        "at": self.clock.now().isoformat()}
        event = self.store.events[order.event_id]
        event.feedback.append({"order_id": order_id, "resolved": resolved,
                               "note": note, "at": self.clock.now().isoformat()})
        if resolved:
            event.status = "resolved"
            event.conclusion = f"工单 {order_id} 处置有效：{note or '指标恢复'}"
        else:
            event.status = "open"
            event.conclusion = f"工单 {order_id} 处置未闭环：{note or '原因待查'}，需重新诊断"
        return event

    def close_event(self, event_id: str, note: str = "") -> DiagnosisEvent:
        try:
            event = self.store.events[event_id]
        except KeyError:
            raise KeyError(f"未知事件 {event_id}") from None
        event.status = "closed"
        event.conclusion = note or "人工关闭"
        return event

    def active_outages(self) -> list[EquipmentOutage]:
        return [o for o in self.store.outages if o.is_active(self.clock)]

    # ---- 指标对比与证据 ----
    def compare_metrics(self, node_id: str, before, after) -> dict:
        """比较处置前后两个窗口的运行指标。"""
        b = self._window_stats(node_id, before[0], before[1])
        a = self._window_stats(node_id, after[0], after[1])
        keys = ("avg_supply_c", "avg_delta_t_c", "avg_flow_m3_h",
                "anomaly_ratio", "complaint_count")
        delta = {k: round(a[k] - b[k], 3)
                 for k in keys if a[k] is not None and b[k] is not None}
        if b["sample_count"] and a["sample_count"]:
            summary = (f"供水均值 {b['avg_supply_c']}→{a['avg_supply_c']}℃，"
                       f"温差均值 {b['avg_delta_t_c']}→{a['avg_delta_t_c']}℃，"
                       f"异常样本占比 {b['anomaly_ratio']:.0%}→{a['anomaly_ratio']:.0%}，"
                       f"投诉 {b['complaint_count']}→{a['complaint_count']} 起")
        else:
            summary = "对比窗口样本不足，无法给出完整结论"
        return {"node_id": node_id, "before": b, "after": a,
                "delta": delta, "summary": summary}

    def _window_stats(self, node_id: str, start, end) -> dict:
        start, end = parse_ts(start), parse_ts(end)
        samples = self.store.samples_for(node_id, start, end)
        scope = self.store.topology.impact_scope(node_id)
        complaints = self.store.complaints_in(scope, start, end)
        stats = {"start": start.isoformat(), "end": end.isoformat(),
                 "sample_count": len(samples), "complaint_count": len(complaints),
                 "avg_supply_c": None, "avg_delta_t_c": None,
                 "avg_flow_m3_h": None, "anomaly_ratio": None}
        if not samples:
            return stats
        anomalies = sum(1 for s in samples
                        if sample_violations(self.rules, s,
                                             nearest_outdoor(self.store.outdoor, s.ts)))
        n = len(samples)
        stats.update(
            avg_supply_c=round(sum(s.supply_c for s in samples) / n, 2),
            avg_delta_t_c=round(sum(s.delta_t for s in samples) / n, 2),
            avg_flow_m3_h=round(sum(s.flow_m3_h for s in samples) / n, 2),
            anomaly_ratio=round(anomalies / n, 3))
        return stats

    def explain_suggestion(self, suggestion_id: str) -> dict:
        """说明一条建议的规则版本、证据与关联事件结论。"""
        try:
            suggestion = self.store.suggestions[suggestion_id]
        except KeyError:
            raise KeyError(f"未知建议 {suggestion_id}") from None
        event = self.store.events[suggestion.event_id]
        return {
            "suggestion_id": suggestion.suggestion_id,
            "action": suggestion.action,
            "summary": suggestion.summary,
            "rule_id": suggestion.rule_id,
            "rule_version": suggestion.rule_version,
            "status": suggestion.status,
            "evidence": list(suggestion.evidence),
            "event": {
                "event_id": event.event_id,
                "status": event.status,
                "anomaly_kind": event.anomaly_kind,
                "interval": [event.interval_start.isoformat(),
                             event.interval_end.isoformat()],
                "conclusion": event.conclusion,
                "reoccurrence_of": event.reoccurrence_of,
                "feedback": list(event.feedback),
            },
        }
