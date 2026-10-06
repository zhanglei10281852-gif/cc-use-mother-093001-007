"""内存仓储：保存全部领域状态，并支持 JSON 持久化（供 CLI 跨调用延续）。"""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import Complaint, OutdoorSnapshot, OutageRecord, SamplePoint
from .metering import MeteringVersion
from .models import (
    AnalysisVersion,
    AnomalyEvent,
    AnomalyInterval,
    BatchRecord,
    DiagnosisConclusion,
    DiagnosisRun,
    Suggestion,
    WorkOrder,
)
from .rules import RuleSet
from .serde import from_jsonable, to_jsonable
from .topology import TopologyVersion


class Store:
    def __init__(self) -> None:
        self.batches: dict[str, BatchRecord] = {}
        self.samples: list[SamplePoint] = []
        self.outdoor: list[OutdoorSnapshot] = []
        self.complaints: dict[str, Complaint] = {}
        self.outages: dict[str, OutageRecord] = {}
        self.topology_versions: list[TopologyVersion] = []
        self.metering_versions: list[MeteringVersion] = []
        self.rule_versions: list[RuleSet] = []
        self.analysis_versions: list[AnalysisVersion] = []
        self.intervals: dict[str, AnomalyInterval] = {}
        self.events: dict[str, AnomalyEvent] = {}
        self.suggestions: dict[str, Suggestion] = {}
        self.work_orders: dict[str, WorkOrder] = {}
        self.conclusions: dict[str, DiagnosisConclusion] = {}
        self.runs: dict[str, DiagnosisRun] = {}
        self.counters: dict[str, int] = {}
        self.settings: dict[str, str] = {}

    # ---- id 生成（计数器随状态持久化） ----
    def next_id(self, prefix: str) -> str:
        self.counters[prefix] = self.counters.get(prefix, 0) + 1
        return f"{prefix}-{self.counters[prefix]}"

    # ---- 查询 ----
    def samples_between(self, start, end) -> list[SamplePoint]:
        return sorted((s for s in self.samples if start <= s.ts <= end), key=lambda s: s.ts)

    def outdoor_at_or_before(self, ts, max_age_minutes: int = 90) -> OutdoorSnapshot | None:
        best: OutdoorSnapshot | None = None
        for snap in self.outdoor:
            if snap.ts <= ts and (ts - snap.ts).total_seconds() <= max_age_minutes * 60:
                if best is None or snap.ts > best.ts:
                    best = snap
        return best

    def complaints_between(self, node_ids, start, end) -> list[Complaint]:
        ids = set(node_ids)
        return sorted(
            (c for c in self.complaints.values() if c.node_id in ids and start <= c.ts <= end),
            key=lambda c: c.ts,
        )

    def outages_for(self, node_id: str) -> list[OutageRecord]:
        return sorted((o for o in self.outages.values() if o.node_id == node_id), key=lambda o: o.start)

    def open_events(self) -> list[AnomalyEvent]:
        return [e for e in self.events.values() if e.status == "open"]

    def open_suggestion_for(self, event_id: str) -> Suggestion | None:
        for s in self.suggestions.values():
            if s.event_id == event_id and s.status == "open":
                return s
        return None

    def order_for_suggestion(self, suggestion_id: str) -> WorkOrder | None:
        for o in self.work_orders.values():
            if o.suggestion_id == suggestion_id:
                return o
        return None

    # ---- 持久化 ----
    def to_dict(self) -> dict:
        return {
            "batches": {k: to_jsonable(v) for k, v in self.batches.items()},
            "samples": [to_jsonable(s) for s in self.samples],
            "outdoor": [to_jsonable(s) for s in self.outdoor],
            "complaints": {k: to_jsonable(v) for k, v in self.complaints.items()},
            "outages": {k: to_jsonable(v) for k, v in self.outages.items()},
            "topology_versions": [to_jsonable(v) for v in self.topology_versions],
            "metering_versions": [to_jsonable(v) for v in self.metering_versions],
            "rule_versions": [to_jsonable(v) for v in self.rule_versions],
            "analysis_versions": [to_jsonable(v) for v in self.analysis_versions],
            "intervals": {k: to_jsonable(v) for k, v in self.intervals.items()},
            "events": {k: to_jsonable(v) for k, v in self.events.items()},
            "suggestions": {k: to_jsonable(v) for k, v in self.suggestions.items()},
            "work_orders": {k: to_jsonable(v) for k, v in self.work_orders.items()},
            "conclusions": {k: to_jsonable(v) for k, v in self.conclusions.items()},
            "runs": {k: to_jsonable(v) for k, v in self.runs.items()},
            "counters": dict(self.counters),
            "settings": dict(self.settings),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Store":
        store = cls()
        store.batches = {k: from_jsonable(BatchRecord, v) for k, v in data.get("batches", {}).items()}
        store.samples = [from_jsonable(SamplePoint, v) for v in data.get("samples", [])]
        store.outdoor = [from_jsonable(OutdoorSnapshot, v) for v in data.get("outdoor", [])]
        store.complaints = {k: from_jsonable(Complaint, v) for k, v in data.get("complaints", {}).items()}
        store.outages = {k: from_jsonable(OutageRecord, v) for k, v in data.get("outages", {}).items()}
        store.topology_versions = [from_jsonable(TopologyVersion, v) for v in data.get("topology_versions", [])]
        store.metering_versions = [from_jsonable(MeteringVersion, v) for v in data.get("metering_versions", [])]
        store.rule_versions = [from_jsonable(RuleSet, v) for v in data.get("rule_versions", [])]
        store.analysis_versions = [from_jsonable(AnalysisVersion, v) for v in data.get("analysis_versions", [])]
        store.intervals = {k: from_jsonable(AnomalyInterval, v) for k, v in data.get("intervals", {}).items()}
        store.events = {k: from_jsonable(AnomalyEvent, v) for k, v in data.get("events", {}).items()}
        store.suggestions = {k: from_jsonable(Suggestion, v) for k, v in data.get("suggestions", {}).items()}
        store.work_orders = {k: from_jsonable(WorkOrder, v) for k, v in data.get("work_orders", {}).items()}
        store.conclusions = {k: from_jsonable(DiagnosisConclusion, v) for k, v in data.get("conclusions", {}).items()}
        store.runs = {k: from_jsonable(DiagnosisRun, v) for k, v in data.get("runs", {}).items()}
        store.counters = dict(data.get("counters", {}))
        store.settings = dict(data.get("settings", {}))
        return store

    def save(self, path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path) -> "Store":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
