"""供热拓扑注册与版本管理：每次修订生成新的拓扑版本，历史版本保留。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .contracts import HeatNode


@dataclass(frozen=True)
class TopologyVersion:
    version: int
    nodes: dict[str, HeatNode]
    note: str
    created_at: datetime


class TopologyRegistry:
    def __init__(self, store, clock) -> None:
        self._store = store
        self._clock = clock

    def register(self, nodes: Iterable[HeatNode], note: str = "") -> TopologyVersion:
        node_map = {n.node_id: n for n in nodes}
        if not node_map:
            raise ValueError("拓扑不能为空")
        for n in node_map.values():
            if n.parent_id is not None and n.parent_id not in node_map:
                raise ValueError(f"节点 {n.node_id} 的父节点 {n.parent_id} 不存在")
        self._check_acyclic(node_map)
        version = TopologyVersion(
            version=len(self._store.topology_versions) + 1,
            nodes=node_map,
            note=note,
            created_at=self._clock.now(),
        )
        self._store.topology_versions.append(version)
        return version

    @staticmethod
    def _check_acyclic(node_map: dict) -> None:
        for nid in node_map:
            seen = {nid}
            cur = node_map[nid]
            while cur.parent_id is not None:
                if cur.parent_id in seen:
                    raise ValueError(f"拓扑存在环: {nid}")
                seen.add(cur.parent_id)
                cur = node_map[cur.parent_id]

    def current(self) -> TopologyVersion | None:
        return self._store.topology_versions[-1] if self._store.topology_versions else None

    def get(self, version: int) -> TopologyVersion:
        for v in self._store.topology_versions:
            if v.version == version:
                return v
        raise KeyError(f"拓扑版本不存在: {version}")

    def _resolve(self, version: int | None) -> TopologyVersion:
        tv = self.get(version) if version is not None else self.current()
        if tv is None:
            raise RuntimeError("尚未注册拓扑")
        return tv

    def children_of(self, node_id: str, version: int | None = None) -> list[HeatNode]:
        tv = self._resolve(version)
        return [n for n in tv.nodes.values() if n.parent_id == node_id]

    def descendants_of(self, node_id: str, version: int | None = None) -> list[str]:
        tv = self._resolve(version)
        result: list[str] = []
        stack = [node_id]
        while stack:
            cur = stack.pop()
            for n in tv.nodes.values():
                if n.parent_id == cur:
                    result.append(n.node_id)
                    stack.append(n.node_id)
        return result

    def parent_of(self, node_id: str, version: int | None = None) -> HeatNode | None:
        tv = self._resolve(version)
        node = tv.nodes.get(node_id)
        if node is None or node.parent_id is None:
            return None
        return tv.nodes.get(node.parent_id)

    def stations(self, version: int | None = None) -> list[HeatNode]:
        tv = self._resolve(version)
        return [n for n in tv.nodes.values() if n.node_type == "exchange_station"]

    def buildings(self, version: int | None = None) -> list[HeatNode]:
        tv = self._resolve(version)
        return [n for n in tv.nodes.values() if n.node_type == "building"]

    def sources(self, version: int | None = None) -> list[HeatNode]:
        tv = self._resolve(version)
        return [n for n in tv.nodes.values() if n.node_type == "source"]
