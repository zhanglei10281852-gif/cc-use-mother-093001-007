"""处置前后指标对比：以工单完成时刻为界，对等时长窗口对照。"""
from __future__ import annotations

from datetime import timedelta

from . import models
from .diagnosis import compute_point_metrics

_COMPARE_KEYS = (
    "avg_supply_deficit_c",
    "anomalous_ratio",
    "avg_delta_t_c",
    "avg_flow_ratio",
    "complaint_count",
)


class ComparisonService:
    def __init__(self, store, clock, window, topology, metering, rules) -> None:
        self._store = store
        self._clock = clock
        self._window = window
        self._topology = topology
        self._metering = metering
        self._rules = rules

    def compare(self, order_id: str) -> models.ComparisonReport:
        order = self._store.work_orders.get(order_id)
        if order is None:
            raise KeyError(f"工单不存在: {order_id}")
        if order.status != "done" or order.completed_at is None:
            raise ValueError("工单尚未完成，无法对比处置前后指标")
        event = self._store.events[order.event_id]
        intervals = [self._store.intervals[i] for i in event.interval_ids]
        before_intervals = [iv for iv in intervals if iv.start <= order.completed_at]
        if not before_intervals:
            raise ValueError("事件缺少处置前异常区间，无法对比")

        completed = order.completed_at
        before_start = min(iv.start for iv in before_intervals)
        duration = max(completed - before_start, timedelta(hours=1))
        now = self._clock.now()
        after_end = min(completed + duration, now)

        # 指标范围：事件节点 + 最近一次处置前区间的影响节点
        scope_nodes = sorted({event.node_id, *before_intervals[-1].impact.affected_node_ids})
        before = self._window_metrics(scope_nodes, before_start, completed)
        after = self._window_metrics(scope_nodes, completed, after_end)

        deltas: dict = {}
        for key in _COMPARE_KEYS:
            b = before.metrics.get(key)
            a = after.metrics.get(key)
            deltas[key] = round(a - b, 3) if (a is not None and b is not None) else None

        notes: list[str] = [
            f"处置前窗口 {before_start.isoformat()} ~ {completed.isoformat()}",
            f"处置后窗口 {completed.isoformat()} ~ {after_end.isoformat()}",
        ]
        if after.sample_count == 0:
            notes.append("处置后窗口内暂无新量测数据")
        before_deficit = before.metrics.get("avg_supply_deficit_c")
        after_deficit = after.metrics.get("avg_supply_deficit_c")
        if before_deficit is None or after_deficit is None:
            improved = False
            notes.append("处置前后窗口缺少可评分样本，无法判定改善")
        else:
            before_ratio = before.metrics.get("anomalous_ratio")
            after_ratio = after.metrics.get("anomalous_ratio")
            improved = after_deficit < before_deficit and (
                before_ratio is None or after_ratio is None or after_ratio <= before_ratio
            )
            notes.append(
                f"平均欠供 {before_deficit} °C → {after_deficit} °C，"
                f"异常点占比 {before_ratio} → {after_ratio}"
            )
        return models.ComparisonReport(
            order_id=order_id,
            event_id=event.event_id,
            node_id=event.node_id,
            before=before,
            after=after,
            deltas=deltas,
            improved=improved,
            notes=tuple(notes),
        )

    def _window_metrics(self, node_ids, start, end) -> models.MetricWindow:
        met = self._metering.current()
        ruleset = self._rules.current()
        if met is None or ruleset is None:
            raise RuntimeError("请先完成计量与规则配置")
        node_set = set(node_ids)
        samples = [
            s for s in self._store.samples_between(start, end) if s.node_id in node_set
        ]
        if end > start:
            windows = self._window.intersect(start, end)
            samples = [
                s for s in samples if any(ws <= s.ts < we for ws, we in windows)
            ]
        scored, _skipped = compute_point_metrics(
            samples,
            self._store.outdoor_at_or_before,
            met.curve,
            met.configs,
            ruleset.thresholds.supply_deficit_c,
        )
        complaints = self._store.complaints_between(node_set, start, end)
        count = len(scored)
        anomalous = sum(1 for p in scored if p.anomalous)
        ratios = [p.flow_ratio for p in scored if p.flow_ratio is not None]
        metrics = {
            "avg_supply_deficit_c": (
                round(sum(p.deficit_c for p in scored) / count, 2) if count else None
            ),
            "anomalous_ratio": round(anomalous / count, 3) if count else None,
            "avg_delta_t_c": (
                round(sum(p.delta_t_c for p in scored) / count, 2) if count else None
            ),
            "avg_flow_ratio": (
                round(sum(ratios) / len(ratios), 3) if ratios else None
            ),
            "complaint_count": len(complaints),
        }
        return models.MetricWindow(start=start, end=end, sample_count=count, metrics=metrics)
