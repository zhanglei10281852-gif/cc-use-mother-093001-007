"""状态存储：内存结构与 JSON 持久化，支撑命令行跨调用共享状态。"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .contracts import HeatNode
from .models import (AnalysisVersion, Complaint, DiagnosisEvent, EquipmentOutage,
                     OutdoorSnapshot, Suggestion, TimedSample, WorkOrder)
from .topology import Topology

_DT_FIELDS = {
    "TimedSample": ("ts",),
    "OutdoorSnapshot": ("ts",),
    "EquipmentOutage": ("start", "end"),
    "Complaint": ("ts",),
    "DiagnosisEvent": ("interval_start", "interval_end"),
    "WorkOrder": ("created_at",),
    "AnalysisVersion": ("created_at",),
}


def _to_dict(obj) -> dict:
    d = asdict(obj)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def _from_dict(cls, d: dict):
    for name in _DT_FIELDS.get(cls.__name__, ()):
        if d.get(name) is not None:
            d[name] = datetime.fromisoformat(d[name])
    return cls(**d)


class StateStore:
    def __init__(self) -> None:
        self.topology = Topology()
        self.samples: list[TimedSample] = []
        self.outdoor: list[OutdoorSnapshot] = []
        self.outages: list[EquipmentOutage] = []
        self.complaints: list[Complaint] = []
        self.events: dict[str, DiagnosisEvent] = {}
        self.suggestions: dict[str, Suggestion] = {}
        self.orders: dict[str, WorkOrder] = {}
        self.versions: list[AnalysisVersion] = []
        self.applied_batches: set[str] = set()
        self.seqs: dict[str, int] = {}

    def next_id(self, prefix: str) -> str:
        self.seqs[prefix] = self.seqs.get(prefix, 0) + 1
        return f"{prefix}-{self.seqs[prefix]:04d}"

    # ---- 查询 ----
    def samples_for(self, node_id: str, start: datetime, end: datetime) -> list[TimedSample]:
        return sorted((s for s in self.samples
                       if s.node_id == node_id and start <= s.ts <= end),
                      key=lambda s: s.ts)

    def complaints_in(self, node_ids, start: datetime, end: datetime) -> list[Complaint]:
        ids = set(node_ids)
        return sorted((c for c in self.complaints
                       if c.node_id in ids and start <= c.ts <= end),
                      key=lambda c: c.ts)

    def outages_for(self, node_id: str) -> list[EquipmentOutage]:
        return [o for o in self.outages if o.node_id == node_id]

    # ---- 持久化 ----
    def save(self, path) -> None:
        data = {
            "topology": {"revision": self.topology.revision,
                         "nodes": [asdict(n) for n in self.topology.nodes()]},
            "samples": [_to_dict(s) for s in self.samples],
            "outdoor": [_to_dict(o) for o in self.outdoor],
            "outages": [_to_dict(o) for o in self.outages],
            "complaints": [_to_dict(c) for c in self.complaints],
            "events": [_to_dict(e) for e in self.events.values()],
            "suggestions": [_to_dict(s) for s in self.suggestions.values()],
            "orders": [_to_dict(o) for o in self.orders.values()],
            "versions": [_to_dict(v) for v in self.versions],
            "applied_batches": sorted(self.applied_batches),
            "seqs": self.seqs,
        }
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path) -> "StateStore":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        store = cls()
        topo = data["topology"]
        store.topology = Topology([HeatNode(**n) for n in topo["nodes"]])
        store.topology.revision = topo["revision"]
        store.samples = [_from_dict(TimedSample, d) for d in data["samples"]]
        store.outdoor = [_from_dict(OutdoorSnapshot, d) for d in data["outdoor"]]
        store.outages = [_from_dict(EquipmentOutage, d) for d in data["outages"]]
        store.complaints = [_from_dict(Complaint, d) for d in data["complaints"]]
        store.events = {d["event_id"]: _from_dict(DiagnosisEvent, d) for d in data["events"]}
        store.suggestions = {d["suggestion_id"]: _from_dict(Suggestion, d) for d in data["suggestions"]}
        store.orders = {d["order_id"]: _from_dict(WorkOrder, d) for d in data["orders"]}
        store.versions = [_from_dict(AnalysisVersion, d) for d in data["versions"]]
        store.applied_batches = set(data["applied_batches"])
        store.seqs = dict(data.get("seqs", {}))
        return store
