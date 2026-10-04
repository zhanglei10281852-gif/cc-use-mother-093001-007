"""规则集与建议引擎：阈值随 rule_version 演进，建议必须携带可解释证据。"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Suggestion


@dataclass(frozen=True)
class RuleSet:
    rule_version: str = "rules-2026.1"
    base_supply_c: float = 46.0       # 气候补偿曲线基准供水温度
    curve_k: float = 1.1              # 室外温度补偿系数
    supply_deficit_c: float = 4.0     # 低于补偿曲线的容忍度
    min_delta_t_c: float = 8.0        # 楼栋最小供回水温差
    station_drop_c: float = 6.0       # 热源出口与站供温差上限
    hydraulic_flow_share: float = 1.2 # 流量高于同站均值的倍数阈值
    complaint_threshold: int = 3      # 窗口内投诉聚集阈值
    interval_gap_minutes: int = 120   # 异常点归并区间的最大间隔
    source_match_minutes: int = 30    # 站供与热源样本的时间匹配容差

    def expected_supply(self, outdoor_c: float) -> float:
        """气候补偿期望供水温度：室外越冷，期望供水越高。"""
        return self.base_supply_c - self.curve_k * outdoor_c


RULE_IDS = {
    "low_supply": "R-LOW-SUPPLY",
    "low_delta_t": "R-LOW-DELTAT",
    "station_drop": "R-STATION-DROP",
    "complaint_cluster": "R-COMPLAINT-CLUSTER",
}


class RuleEngine:
    """按规则版本把异常区间翻译成调节/巡检/观察建议。"""

    def __init__(self, rules: RuleSet):
        self.rules = rules

    def suggest(self, event, interval, context: dict, suggestion_id: str) -> Suggestion | None:
        kind = interval.kind
        rule_id = RULE_IDS.get(kind)
        if rule_id is None:
            return None
        evidence = [f"规则 {rule_id}（版本 {self.rules.rule_version}）", *interval.evidence]
        if kind == "low_supply":
            action = "adjust"
            summary = "提高对应换热站二次供水温度设定，并核查楼前阀门开度"
        elif kind == "low_delta_t":
            share = context.get("flow_share")
            if share is not None:
                evidence.append(
                    f"楼栋平均流量为同站其他楼栋均值 {share:.2f} 倍"
                    f"（水力失衡阈值 {self.rules.hydraulic_flow_share}）")
            if share is not None and share >= self.rules.hydraulic_flow_share:
                action = "adjust"
                summary = "流量偏高而温差不足，疑似管网水力失衡，调节楼前平衡阀或分支阀门"
            else:
                action = "observe"
                summary = "温差偏低但流量证据不足，继续观察一个采暖窗口后再评估"
        elif kind == "station_drop":
            action = "inspect"
            summary = "站供与热源出口温差过大，巡检换热器结垢、水泵与阀门状态"
        elif kind == "complaint_cluster":
            action = "observe"
            summary = "投诉集中但计量未越限，安排入户测温并持续观察"
        else:  # pragma: no cover
            return None
        for c in context.get("complaints", []):
            evidence.append(f"投诉 {c.complaint_id}（{c.node_id} {c.ts:%m-%d %H:%M}）：{c.detail or '室温不足'}")
        return Suggestion(suggestion_id, event.event_id, event.node_id, action,
                          rule_id, self.rules.rule_version, summary, evidence)
