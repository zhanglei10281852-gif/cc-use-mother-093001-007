"""供热拓扑和运行样本的基础契约。

所有时间戳均为本地朴素 datetime（naive），代表供热企业本地时间；
“当前时刻”一律通过 clock 模块注入，不在契约层取系统时间。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

#: 允许的拓扑节点类型：热源 / 换热站 / 楼栋
NODE_TYPES = ("source", "exchange_station", "building")


@dataclass(frozen=True)
class HeatNode:
    """供热拓扑节点，parent_id 指向上级节点（热源为根）。

    attrs 存放附加属性（如 households 户数），不参与相等性比较。
    """
    node_id: str
    node_type: str
    parent_id: str | None = None
    attrs: dict = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if self.node_type not in NODE_TYPES:
            raise ValueError(f"未知节点类型: {self.node_type}")
        if self.parent_id == self.node_id:
            raise ValueError("节点不能以自身为父节点")


@dataclass(frozen=True)
class RuntimeSample:
    """单节点运行量测（无时间戳的瞬时契约，供外部系统对接）。"""
    node_id: str
    supply_c: float
    return_c: float
    flow_m3_h: float

    def __post_init__(self) -> None:
        if self.return_c > self.supply_c:
            raise ValueError("回水温度不能高于供水温度")
        if self.flow_m3_h < 0:
            raise ValueError("流量不能为负数")


@dataclass(frozen=True)
class SamplePoint:
    """带时间戳的运行量测点，诊断计算的基本输入。"""
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


@dataclass(frozen=True)
class OutdoorSnapshot:
    """室外条件快照。"""
    ts: datetime
    outdoor_c: float
    wind_ms: float = 0.0


@dataclass(frozen=True)
class Complaint:
    """居民投诉（默认室温不足）。"""
    complaint_id: str
    node_id: str
    ts: datetime
    kind: str = "room_temp_low"
    detail: str = ""


@dataclass(frozen=True)
class OutageRecord:
    """设备停运记录；end 为 None 表示仍在停运中。

    是否为“临时停运”由 kind 区分（planned=计划检修 / unplanned=临时停运），
    进行中停运的有效结束时间由调用方注入的时钟解释。
    """
    outage_id: str
    node_id: str
    start: datetime
    end: datetime | None = None
    kind: str = "planned"
    reason: str = ""

    def effective_end(self, now: datetime) -> datetime:
        """仍在停运时以注入的当前时刻作为有效结束时间。"""
        return self.end if self.end is not None else now

    def is_active_at(self, ts: datetime, now: datetime) -> bool:
        return self.start <= ts <= self.effective_end(now)
