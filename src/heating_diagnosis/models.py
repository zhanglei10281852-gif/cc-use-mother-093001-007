"""诊断与处置领域模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# ---- 异常类别 ----
EQUIPMENT_OUTAGE = "equipment_outage"          # 设备停运/检修（含热源、换热站欠供）
HYDRAULIC_IMBALANCE = "hydraulic_imbalance"    # 管网水力失衡
USER_SIDE = "user_side"                        # 用户侧异常
CATEGORIES = (EQUIPMENT_OUTAGE, HYDRAULIC_IMBALANCE, USER_SIDE)

CATEGORY_NAMES = {
    EQUIPMENT_OUTAGE: "设备停运/检修",
    HYDRAULIC_IMBALANCE: "管网水力失衡",
    USER_SIDE: "用户侧异常",
}

# ---- 建议动作 ----
ACTION_ADJUST = "adjust"      # 调节
ACTION_INSPECT = "inspect"    # 巡检
ACTION_OBSERVE = "observe"    # 观察
ACTIONS = (ACTION_ADJUST, ACTION_INSPECT, ACTION_OBSERVE)

ACTION_NAMES = {
    ACTION_ADJUST: "调节",
    ACTION_INSPECT: "巡检",
    ACTION_OBSERVE: "观察",
}


@dataclass(frozen=True)
class Evidence:
    """一条可解释证据：kind 指明来源，ref 指向原始记录，detail 为人读说明。"""
    kind: str        # sample | outdoor | complaint | outage | threshold | topology | metric
    ref: str
    detail: str
    value: float | None = None


@dataclass(frozen=True)
class ImpactScope:
    """影响范围：受影响的下游节点、关联投诉与户数。"""
    node_id: str
    affected_node_ids: tuple[str, ...]
    complaint_ids: tuple[str, ...]
    households: int | None


@dataclass(frozen=True)
class AnomalyInterval:
    """可解释的异常区间。"""
    interval_id: str
    node_id: str
    category: str
    start: datetime
    end: datetime
    severity: float
    metrics: dict
    evidence: tuple[Evidence, ...]
    impact: ImpactScope
    analysis_version: int


@dataclass
class AnomalyEvent:
    """异常事件：同一 (node, category) 的持续表现聚合为一个事件。

    已关闭事件重新出现时创建新事件，并通过 previous_event_id 关联，
    而不是覆盖旧事件。
    """
    event_id: str
    node_id: str
    category: str
    status: str            # open | closed
    opened_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    interval_ids: list[str] = field(default_factory=list)
    previous_event_id: str | None = None
    analysis_version: int = 0


@dataclass(frozen=True)
class Suggestion:
    """处置建议（调节/巡检/观察），携带规则版本与完整证据链。"""
    suggestion_id: str
    event_id: str
    action_type: str
    target_node_id: str
    reason: str
    evidence: tuple[Evidence, ...]
    rule_version: int
    analysis_version: int
    status: str            # open | confirmed | superseded
    created_at: datetime


@dataclass(frozen=True)
class WorkOrderResult:
    """工单执行结果：resolved 表示问题是否解决；confirmed_cause 为现场核实原因
    （与异常类别一致 / no_issue / 其他类别），用于反馈诊断结论。"""
    resolved: bool
    confirmed_cause: str
    notes: str = ""


@dataclass
class WorkOrder:
    order_id: str
    suggestion_id: str
    event_id: str
    node_id: str
    action_type: str
    status: str            # open | done
    created_by: str
    created_at: datetime
    completed_at: datetime | None = None
    result: WorkOrderResult | None = None


@dataclass(frozen=True)
class FeedbackRecord:
    order_id: str
    result: WorkOrderResult
    recorded_at: datetime


@dataclass
class DiagnosisConclusion:
    """诊断结论：随工单反馈更新。

    pending → confirmed（原因吻合）/ revised（原因修正）/ false_positive（误报）。
    """
    event_id: str
    category: str
    status: str
    revised_category: str | None = None
    feedback: list[FeedbackRecord] = field(default_factory=list)
    updated_at: datetime | None = None


@dataclass(frozen=True)
class AnalysisVersion:
    """分析版本：拓扑版本 × 计量版本 × 规则版本 的组合。

    拓扑或计量（或规则）修订时创建新版本，历史运行与结论保留在旧版本下。
    """
    version: int
    topology_version: int
    metering_version: int
    rule_version: int
    created_at: datetime
    note: str = ""


@dataclass(frozen=True)
class DiagnosisRun:
    run_id: str
    analysis_version: int
    as_of: datetime
    horizon_hours: int
    interval_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    suggestion_ids: tuple[str, ...]


@dataclass(frozen=True)
class MetricWindow:
    """一段时间窗口内聚合出的指标。"""
    start: datetime
    end: datetime
    sample_count: int
    metrics: dict


@dataclass(frozen=True)
class ComparisonReport:
    """处置前后指标对比报告。"""
    order_id: str
    event_id: str
    node_id: str
    before: MetricWindow
    after: MetricWindow
    deltas: dict
    improved: bool
    notes: tuple[str, ...]


@dataclass(frozen=True)
class BatchRecord:
    """已计入的数据批次台账（幂等依据）。"""
    batch_id: str
    kind: str
    accepted_count: int
    ingested_at: datetime


@dataclass(frozen=True)
class IngestResult:
    batch_id: str
    kind: str
    status: str            # accepted | duplicate
    accepted_count: int
    skipped_count: int
