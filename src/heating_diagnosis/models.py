"""诊断领域模型：带时标样本、室外快照、停运、投诉、事件、建议、工单与版本。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


def parse_ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class TimedSample:
    node_id: str
    ts: datetime
    supply_c: float
    return_c: float
    flow_m3_h: float

    def __post_init__(self) -> None:
        if self.return_c > self.supply_c:
            raise ValueError("回水温度不能高于供水温度")
        if self.flow_m3_h < 0:
            raise ValueError("流量不能为负数")

    @property
    def delta_t(self) -> float:
        return self.supply_c - self.return_c


@dataclass(frozen=True)
class OutdoorSnapshot:
    ts: datetime
    outdoor_c: float
    wind_m_s: float = 0.0


@dataclass(frozen=True)
class EquipmentOutage:
    """设备停运；end 为 None 表示临时停运进行中（由注入时钟判定）。"""

    outage_id: str
    node_id: str
    start: datetime
    end: datetime | None = None
    reason: str = ""

    def covers(self, ts: datetime) -> bool:
        return self.start <= ts and (self.end is None or ts <= self.end)

    def is_active(self, clock) -> bool:
        now = clock.now()
        return self.start <= now and (self.end is None or self.end >= now)


@dataclass(frozen=True)
class Complaint:
    complaint_id: str
    node_id: str
    ts: datetime
    detail: str = ""


@dataclass
class DiagnosisEvent:
    """诊断事件；已关闭事件复发时新建事件并通过 reoccurrence_of 关联，不覆盖。"""

    event_id: str
    fingerprint: str  # f"{node_id}:{anomaly_kind}"
    node_id: str
    anomaly_kind: str
    status: str  # open | closed | resolved
    interval_start: datetime
    interval_end: datetime
    version_id: str
    conclusion: str = ""
    evidence: list[str] = field(default_factory=list)
    feedback: list[dict] = field(default_factory=list)
    reoccurrence_of: str | None = None


@dataclass
class Suggestion:
    suggestion_id: str
    event_id: str
    node_id: str
    action: str  # adjust | inspect | observe
    rule_id: str
    rule_version: str
    summary: str
    evidence: list[str]
    status: str = "proposed"  # proposed | confirmed | dismissed


@dataclass
class WorkOrder:
    order_id: str
    suggestion_id: str
    event_id: str
    node_id: str
    action: str
    created_by: str
    created_at: datetime
    status: str = "issued"  # issued | done
    result: dict | None = None


@dataclass(frozen=True)
class AnalysisVersion:
    """分析版本：拓扑或计量修订都会创建新版本，诊断结论按版本归属。"""

    version_id: str
    rule_version: str
    topology_revision: int
    reason: str
    created_at: datetime
