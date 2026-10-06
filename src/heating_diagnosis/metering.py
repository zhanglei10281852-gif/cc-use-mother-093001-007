"""计量配置（气候补偿曲线、设计流量、计量修正系数）按版本管理。

计量修订（如修正系数调整）通过再次 register 生成新版本，
由服务层联动创建新的分析版本。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True)
class ClimateCurve:
    """气候补偿曲线：室外越冷，期望供水温度越高。"""
    base_supply_c: float
    ref_outdoor_c: float
    slope: float

    def expected_supply(self, outdoor_c: float) -> float:
        return self.base_supply_c + (self.ref_outdoor_c - outdoor_c) * self.slope


@dataclass(frozen=True)
class MeteringConfig:
    """单节点计量配置。"""
    node_id: str
    design_flow_m3_h: float
    calibration: float = 1.0        # 计量修正系数（计量修订时调整）
    expected_delta_t_c: float = 20.0

    def __post_init__(self) -> None:
        if self.design_flow_m3_h <= 0:
            raise ValueError("设计流量必须为正数")
        if self.calibration <= 0:
            raise ValueError("计量修正系数必须为正数")


@dataclass(frozen=True)
class MeteringVersion:
    version: int
    curve: ClimateCurve
    configs: dict[str, MeteringConfig]
    note: str
    created_at: datetime


class MeteringRegistry:
    def __init__(self, store, clock) -> None:
        self._store = store
        self._clock = clock

    def register(
        self,
        curve: ClimateCurve,
        configs: Iterable[MeteringConfig],
        note: str = "",
    ) -> MeteringVersion:
        config_map = {c.node_id: c for c in configs}
        version = MeteringVersion(
            version=len(self._store.metering_versions) + 1,
            curve=curve,
            configs=config_map,
            note=note,
            created_at=self._clock.now(),
        )
        self._store.metering_versions.append(version)
        return version

    def current(self) -> MeteringVersion | None:
        return self._store.metering_versions[-1] if self._store.metering_versions else None

    def get(self, version: int) -> MeteringVersion:
        for v in self._store.metering_versions:
            if v.version == version:
                return v
        raise KeyError(f"计量版本不存在: {version}")

    def config_for(self, node_id: str, version: int | None = None) -> MeteringConfig | None:
        mv = self.get(version) if version is not None else self.current()
        if mv is None:
            raise RuntimeError("尚未注册计量配置")
        return mv.configs.get(node_id)
