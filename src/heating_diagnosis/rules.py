"""规则版本与处置建议生成（调节 / 巡检 / 观察）。

规则以版本管理：阈值调整生成新的 RuleSet 版本，
服务层会把旧规则版本的未确认建议标记为 superseded。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import models


@dataclass(frozen=True)
class Thresholds:
    supply_deficit_c: float = 3.0        # 供水欠供阈值：期望供水 - 实际供水 超过该值记为异常
    flow_ratio_low: float = 0.15         # 实际流量低于设计流量该比例 → 疑似停运
    imbalance_spread_c: float = 4.0      # 同站建筑欠供离散度（证据指标）
    min_imbalance_buildings: int = 2     # 判定水力失衡的最少同时异常建筑数
    max_gap_minutes: int = 45            # 相邻异常点合并为同一区间的最大间隔
    complaint_burst: int = 2             # 区间内投诉集中阈值
    observe_severity: float = 0.4        # 用户侧低于该严重度 → 建议观察而非巡检


@dataclass(frozen=True)
class RuleSet:
    version: int
    thresholds: Thresholds
    note: str
    created_at: datetime


class RuleRegistry:
    def __init__(self, store, clock) -> None:
        self._store = store
        self._clock = clock

    def register(self, thresholds: Thresholds | None = None, note: str = "") -> RuleSet:
        ruleset = RuleSet(
            version=len(self._store.rule_versions) + 1,
            thresholds=thresholds or Thresholds(),
            note=note,
            created_at=self._clock.now(),
        )
        self._store.rule_versions.append(ruleset)
        return ruleset

    def current(self) -> RuleSet | None:
        return self._store.rule_versions[-1] if self._store.rule_versions else None

    def get(self, version: int) -> RuleSet:
        for v in self._store.rule_versions:
            if v.version == version:
                return v
        raise KeyError(f"规则版本不存在: {version}")


def build_suggestion(
    suggestion_id: str,
    event: models.AnomalyEvent,
    interval: models.AnomalyInterval,
    ruleset: RuleSet,
    now: datetime,
) -> models.Suggestion:
    """依据规则版本把异常事件映射为处置建议，证据随建议一起保存。"""
    th = ruleset.thresholds
    evidence = list(interval.evidence)
    evidence.append(models.Evidence(
        "threshold", "supply_deficit_c",
        f"欠供判定阈值 {th.supply_deficit_c} °C（规则版本 v{ruleset.version}）",
        th.supply_deficit_c,
    ))
    complaints = interval.metrics.get("complaint_count") or 0
    planned = any(e.kind == "outage" and "计划" in e.detail for e in interval.evidence)

    if event.category == models.EQUIPMENT_OUTAGE:
        if planned:
            action = models.ACTION_OBSERVE
            reason = f"{event.node_id} 处于计划停运窗口，建议观察恢复后的供回水参数"
        else:
            action = models.ACTION_INSPECT
            reason = f"{event.node_id} 疑似设备停运/故障，建议安排现场巡检核实"
    elif event.category == models.HYDRAULIC_IMBALANCE:
        action = models.ACTION_ADJUST
        buildings = "、".join(interval.impact.affected_node_ids)
        reason = (
            f"{event.node_id} 下属 {len(interval.impact.affected_node_ids)} 栋建筑欠供"
            f"而站侧总供正常，判定管网水力失衡，建议对 {buildings} 支线阀门做平衡调节"
        )
    else:  # USER_SIDE
        if complaints >= th.complaint_burst or interval.severity >= th.observe_severity:
            action = models.ACTION_INSPECT
            reason = f"{event.node_id} 欠供且同站其他建筑正常，判定用户侧异常，建议入户巡检"
        else:
            action = models.ACTION_OBSERVE
            reason = f"{event.node_id} 轻度欠供，建议继续观察一个采暖窗口"

    return models.Suggestion(
        suggestion_id=suggestion_id,
        event_id=event.event_id,
        action_type=action,
        target_node_id=event.node_id,
        reason=reason,
        evidence=tuple(evidence),
        rule_version=ruleset.version,
        analysis_version=interval.analysis_version,
        status="open",
        created_at=now,
    )
