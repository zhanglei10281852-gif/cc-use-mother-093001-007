"""供热运行诊断与处置服务门面（编程接口）。"""
from __future__ import annotations

import dataclasses
from datetime import datetime

from . import models
from .clock import Clock, HeatingWindow, SystemClock
from .compare import ComparisonService
from .contracts import HeatNode
from .diagnosis import DiagnosisEngine
from .ingestion import DataBatch, IngestionService
from .metering import ClimateCurve, MeteringConfig, MeteringRegistry, MeteringVersion
from .models import AnalysisVersion
from .rules import RuleRegistry, RuleSet, Thresholds
from .store import Store
from .topology import TopologyRegistry, TopologyVersion
from .workorders import WorkOrderService


class DiagnosisService:
    """把拓扑、计量、规则、接入、诊断、工单与对比编排为一个服务。"""

    def __init__(
        self,
        store: Store | None = None,
        clock: Clock | None = None,
        window: HeatingWindow | None = None,
    ) -> None:
        self.store = store or Store()
        self.clock = clock or SystemClock()
        self.window = window or HeatingWindow()
        self.topology = TopologyRegistry(self.store, self.clock)
        self.metering = MeteringRegistry(self.store, self.clock)
        self.rules = RuleRegistry(self.store, self.clock)
        self.ingestion = IngestionService(self.store, self.clock)
        self.work_orders = WorkOrderService(self.store, self.clock)

    # ---------------------------------------------------------------- 配置与版本
    def register_topology(self, nodes: list[HeatNode], note: str = "") -> TopologyVersion:
        version = self.topology.register(nodes, note)
        self._ensure_analysis_version("拓扑修订")
        return version

    def revise_metering(
        self,
        curve: ClimateCurve,
        configs: list[MeteringConfig],
        note: str = "",
    ) -> MeteringVersion:
        version = self.metering.register(curve, configs, note)
        self._ensure_analysis_version("计量修订")
        return version

    def register_rules(self, thresholds: Thresholds | None = None, note: str = "") -> RuleSet:
        ruleset = self.rules.register(thresholds, note)
        # 旧规则版本的未确认建议作废，等待按新规则重新生成
        for sid, suggestion in list(self.store.suggestions.items()):
            if suggestion.status == "open" and suggestion.rule_version != ruleset.version:
                self.store.suggestions[sid] = dataclasses.replace(
                    suggestion, status="superseded"
                )
        self._ensure_analysis_version("规则修订")
        return ruleset

    def _ensure_analysis_version(self, note: str) -> AnalysisVersion | None:
        """拓扑/计量/规则任一修订后，组合变化即创建新的分析版本。"""
        topo = self.topology.current()
        met = self.metering.current()
        rules = self.rules.current()
        if topo is None or met is None or rules is None:
            return None
        triple = (topo.version, met.version, rules.version)
        existing = self.store.analysis_versions
        if existing:
            last = existing[-1]
            if (last.topology_version, last.metering_version, last.rule_version) == triple:
                return last
        version = AnalysisVersion(
            version=len(existing) + 1,
            topology_version=topo.version,
            metering_version=met.version,
            rule_version=rules.version,
            created_at=self.clock.now(),
            note=note,
        )
        self.store.analysis_versions.append(version)
        return version

    def analysis_versions(self) -> list[AnalysisVersion]:
        return list(self.store.analysis_versions)

    # ---------------------------------------------------------------- 数据接入
    def ingest(self, batch: DataBatch) -> models.IngestResult:
        return self.ingestion.ingest(batch)

    # ---------------------------------------------------------------- 诊断
    def run_diagnosis(self, as_of: datetime | None = None,
                      horizon_hours: int = 24) -> models.DiagnosisRun:
        version = self._ensure_analysis_version("诊断运行")
        if version is None:
            raise RuntimeError("请先完成拓扑、计量与规则配置")
        engine = DiagnosisEngine(
            self.store, self.clock, self.window,
            self.topology, self.metering, self.rules,
        )
        return engine.run(version.version, as_of=as_of, horizon_hours=horizon_hours)

    # ---------------------------------------------------------------- 事件
    def list_events(self, status: str | None = None) -> list[models.AnomalyEvent]:
        events = sorted(self.store.events.values(), key=lambda e: e.opened_at)
        return [e for e in events if status is None or e.status == status]

    def get_event(self, event_id: str) -> models.AnomalyEvent:
        event = self.store.events.get(event_id)
        if event is None:
            raise KeyError(f"事件不存在: {event_id}")
        return event

    def event_chain(self, event_id: str) -> list[models.AnomalyEvent]:
        """复发链：从最早的前序事件到当前事件。"""
        event = self.get_event(event_id)
        chain = [event]
        while event.previous_event_id is not None:
            event = self.store.events[event.previous_event_id]
            chain.append(event)
        chain.reverse()
        return chain

    def get_conclusion(self, event_id: str) -> models.DiagnosisConclusion:
        conclusion = self.store.conclusions.get(event_id)
        if conclusion is None:
            raise KeyError(f"诊断结论不存在: {event_id}")
        return conclusion

    # ---------------------------------------------------------------- 建议
    def list_suggestions(self, status: str | None = None) -> list[models.Suggestion]:
        suggestions = sorted(self.store.suggestions.values(), key=lambda s: s.created_at)
        return [s for s in suggestions if status is None or s.status == status]

    def explain_suggestion(self, suggestion_id: str) -> dict:
        """返回建议的完整证据链与人读说明。"""
        suggestion = self.store.suggestions.get(suggestion_id)
        if suggestion is None:
            raise KeyError(f"建议不存在: {suggestion_id}")
        lines = []
        for e in suggestion.evidence:
            line = f"[{e.kind}] {e.detail}"
            if e.value is not None:
                line += f"（数值 {e.value}）"
            lines.append(line)
        return {
            "suggestion": suggestion,
            "event": self.store.events.get(suggestion.event_id),
            "rule_version": suggestion.rule_version,
            "analysis_version": suggestion.analysis_version,
            "evidence": list(suggestion.evidence),
            "lines": lines,
        }

    # ---------------------------------------------------------------- 工单
    def confirm_suggestion(self, suggestion_id: str, operator: str) -> models.WorkOrder:
        return self.work_orders.confirm(suggestion_id, operator)

    def complete_work_order(
        self,
        order_id: str,
        resolved: bool,
        confirmed_cause: str,
        notes: str = "",
    ) -> models.DiagnosisConclusion:
        if confirmed_cause != "no_issue" and confirmed_cause not in models.CATEGORIES:
            raise ValueError(f"未知原因类别: {confirmed_cause}")
        result = models.WorkOrderResult(
            resolved=resolved, confirmed_cause=confirmed_cause, notes=notes
        )
        return self.work_orders.complete(order_id, result)

    # ---------------------------------------------------------------- 对比
    def compare_disposal(self, order_id: str) -> models.ComparisonReport:
        comparison = ComparisonService(
            self.store, self.clock, self.window,
            self.topology, self.metering, self.rules,
        )
        return comparison.compare(order_id)

    # ---------------------------------------------------------------- 持久化
    def save(self, path) -> None:
        self.store.save(path)

    @classmethod
    def load(cls, path, clock: Clock | None = None,
             window: HeatingWindow | None = None) -> "DiagnosisService":
        return cls(Store.load(path), clock=clock, window=window)
