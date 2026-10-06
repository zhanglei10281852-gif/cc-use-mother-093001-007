"""数据批次幂等接入：相同批次不会重复计入。

两层防护：
1. 批次级：batch_id 已入账的批次直接判重返回，不重复计入；
2. 记录级：不同批次携带的完全相同记录（同节点同时刻量测、同投诉号等）跳过。
"""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import Complaint, OutdoorSnapshot, OutageRecord, SamplePoint
from .models import BatchRecord, IngestResult

BATCH_KINDS = ("samples", "outdoor", "complaints", "outages")


@dataclass(frozen=True)
class DataBatch:
    batch_id: str
    kind: str            # samples | outdoor | complaints | outages
    items: tuple

    def __post_init__(self) -> None:
        if not self.batch_id:
            raise ValueError("批次号不能为空")
        if self.kind not in BATCH_KINDS:
            raise ValueError(f"未知批次类型: {self.kind}")


class IngestionService:
    def __init__(self, store, clock) -> None:
        self._store = store
        self._clock = clock

    def ingest(self, batch: DataBatch) -> IngestResult:
        recorded = self._store.batches.get(batch.batch_id)
        if recorded is not None:
            return IngestResult(batch.batch_id, batch.kind, "duplicate", 0, len(batch.items))
        handler = {
            "samples": self._ingest_samples,
            "outdoor": self._ingest_outdoor,
            "complaints": self._ingest_complaints,
            "outages": self._ingest_outages,
        }[batch.kind]
        accepted, skipped = handler(batch.items)
        self._store.batches[batch.batch_id] = BatchRecord(
            batch.batch_id, batch.kind, accepted, self._clock.now()
        )
        return IngestResult(batch.batch_id, batch.kind, "accepted", accepted, skipped)

    def _ingest_samples(self, items) -> tuple[int, int]:
        existing = {(s.node_id, s.ts) for s in self._store.samples}
        accepted = skipped = 0
        for it in items:
            if not isinstance(it, SamplePoint):
                raise TypeError("samples 批次元素必须是 SamplePoint")
            key = (it.node_id, it.ts)
            if key in existing:
                skipped += 1
                continue
            existing.add(key)
            self._store.samples.append(it)
            accepted += 1
        return accepted, skipped

    def _ingest_outdoor(self, items) -> tuple[int, int]:
        existing = {s.ts for s in self._store.outdoor}
        accepted = skipped = 0
        for it in items:
            if not isinstance(it, OutdoorSnapshot):
                raise TypeError("outdoor 批次元素必须是 OutdoorSnapshot")
            if it.ts in existing:
                skipped += 1
                continue
            existing.add(it.ts)
            self._store.outdoor.append(it)
            accepted += 1
        return accepted, skipped

    def _ingest_complaints(self, items) -> tuple[int, int]:
        accepted = skipped = 0
        for it in items:
            if not isinstance(it, Complaint):
                raise TypeError("complaints 批次元素必须是 Complaint")
            if it.complaint_id in self._store.complaints:
                skipped += 1
                continue
            self._store.complaints[it.complaint_id] = it
            accepted += 1
        return accepted, skipped

    def _ingest_outages(self, items) -> tuple[int, int]:
        accepted = skipped = 0
        for it in items:
            if not isinstance(it, OutageRecord):
                raise TypeError("outages 批次元素必须是 OutageRecord")
            if it.outage_id in self._store.outages:
                skipped += 1
                continue
            self._store.outages[it.outage_id] = it
            accepted += 1
        return accepted, skipped
