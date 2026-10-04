"""供热拓扑：节点父子关系、影响范围计算与修订计数。"""
from __future__ import annotations

from .contracts import HeatNode


class Topology:
    def __init__(self, nodes=()):
        self._nodes: dict[str, HeatNode] = {}
        self.revision = 0
        for n in nodes:
            self._nodes[n.node_id] = n

    def add_or_revise(self, node: HeatNode) -> bool:
        """新增或修订节点；内容无变化返回 False。"""
        if self._nodes.get(node.node_id) == node:
            return False
        self._nodes[node.node_id] = node
        self.revision += 1
        return True

    def get(self, node_id: str) -> HeatNode:
        try:
            return self._nodes[node_id]
        except KeyError:
            raise KeyError(f"未知节点 {node_id}") from None

    def nodes(self) -> list[HeatNode]:
        return list(self._nodes.values())

    def children(self, node_id: str) -> list[HeatNode]:
        return [n for n in self._nodes.values() if n.parent_id == node_id]

    def impact_scope(self, node_id: str) -> list[str]:
        """影响范围：节点自身及全部下游（热源→换热站→楼栋）。"""
        self.get(node_id)
        scope, stack = [], [node_id]
        while stack:
            cur = stack.pop()
            scope.append(cur)
            stack.extend(c.node_id for c in self.children(cur))
        return scope

    def ancestors(self, node_id: str) -> list[str]:
        out, cur = [], self.get(node_id).parent_id
        while cur is not None:
            out.append(cur)
            cur = self.get(cur).parent_id
        return out
