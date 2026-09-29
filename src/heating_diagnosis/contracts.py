"""供热拓扑和运行样本的基础契约。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class HeatNode:
    node_id: str
    node_type: str
    parent_id: str | None = None


@dataclass(frozen=True)
class RuntimeSample:
    node_id: str
    supply_c: float
    return_c: float
    flow_m3_h: float

    def __post_init__(self) -> None:
        if self.return_c > self.supply_c:
            raise ValueError("回水温度不能高于供水温度")
        if self.flow_m3_h < 0:
            raise ValueError("流量不能为负数")
