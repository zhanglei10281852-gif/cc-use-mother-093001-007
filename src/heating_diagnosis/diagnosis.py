"""异常区间计算、影响范围分析与事件生命周期管理。

诊断语义：
- 只评估采暖窗口（支持跨午夜）内的量测；窗口外数据不计异常；
- 设备停运（含上级节点停运）覆盖时段的量测剔除出评分，避免把计划/临时
  停运误判为用户侧或水力问题；
- 每次运行只处理自上次运行以来的新数据（首次按 horizon 回溯），
  相同数据不会被重复计入诊断；
- 异常按“根因节点”归集：换热站/热源自身欠供记为设备侧事件并抑制下游
  楼栋的重复报警，同站多栋欠供而站侧正常记为水力失衡，单栋欠供记为用户侧，
  以此避免重复派单；
- 事件关闭后同类异常重新出现时，创建新事件并通过 previous_event_id 关联，
  不覆盖已关闭事件。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from . import models
from .clock import HeatingWindow
from .contracts import OutageRecord
from .models import Evidence
from .rules import build_suggestion
from .topology import TopologyVersion


@dataclass
class PointMetric:
    """单个量测点的诊断指标。"""
    node_id: str
    ts: datetime
    supply_c: float
    return_c: float
    flow_m3_h: float
    outdoor_c: float
    expected_supply_c: float
    deficit_c: float
    delta_t_c: float
    flow_ratio: float | None
    anomalous: bool


def compute_point_metrics(samples, outdoor_lookup, curve, configs, deficit_threshold):
    """对量测点评分，返回 (评分点列表, 因缺少室外快照而跳过的样本列表)。"""
    scored: list[PointMetric] = []
    skipped: list = []
    for s in samples:
        snap = outdoor_lookup(s.ts)
        if snap is None:
            skipped.append(s)
            continue
        cfg = configs.get(s.node_id)
        expected = curve.expected_supply(snap.outdoor_c)
        flow_ratio = None
        if cfg is not None:
            flow_ratio = round(s.flow_m3_h * cfg.calibration / cfg.design_flow_m3_h, 4)
        deficit = round(expected - s.supply_c, 3)
        scored.append(PointMetric(
            node_id=s.node_id,
            ts=s.ts,
            supply_c=s.supply_c,
            return_c=s.return_c,
            flow_m3_h=s.flow_m3_h,
            outdoor_c=snap.outdoor_c,
            expected_supply_c=round(expected, 3),
            deficit_c=deficit,
            delta_t_c=round(s.supply_c - s.return_c, 3),
            flow_ratio=flow_ratio,
            anomalous=deficit > deficit_threshold,
        ))
    return scored, skipped


def merge_points(timestamps, max_gap_minutes: int) -> list[tuple[datetime, datetime]]:
    """把相邻间隔不超过 max_gap 的时间点合并为区间。"""
    if not timestamps:
        return []
    ordered = sorted(timestamps)
    gap = timedelta(minutes=max_gap_minutes)
    segments: list[tuple[datetime, datetime]] = []
    seg_start = prev = ordered[0]
    for t in ordered[1:]:
        if t - prev <= gap:
            prev = t
        else:
            segments.append((seg_start, prev))
            seg_start = prev = t
    segments.append((seg_start, prev))
    return segments


def _covered(spans, ts) -> bool:
    return any(s <= ts <= e for s, e in spans)


@dataclass
class _Ctx:
    """单次诊断运行的上下文。"""
    as_of: datetime
    analysis_version: int
    topo: TopologyVersion
    thresholds: object
    by_node: dict
    anomalous_by_node: dict


class DiagnosisEngine:
    def __init__(self, store, clock, window: HeatingWindow, topology, metering, rules) -> None:
        self._store = store
        self._clock = clock
        self._window = window
        self._topology = topology
        self._metering = metering
        self._rules = rules

    # ------------------------------------------------------------------ 主流程
    def run(self, analysis_version: int, as_of: datetime | None = None,
            horizon_hours: int = 24) -> models.DiagnosisRun:
        store = self._store
        as_of = as_of or self._clock.now()
        topo_v = self._topology.current()
        met_v = self._metering.current()
        ruleset = self._rules.current()
        if topo_v is None or met_v is None or ruleset is None:
            raise RuntimeError("请先完成拓扑、计量与规则配置")
        th = ruleset.thresholds

        # 增量语义：只处理上次运行之后到达的数据，避免重复计入
        last_as_of = max((r.as_of for r in store.runs.values()), default=None)
        start = as_of - timedelta(hours=horizon_hours)
        if last_as_of is not None and last_as_of > start:
            start = last_as_of

        windows = self._window.intersect(start, as_of) if start < as_of else []
        in_window = [
            s for s in store.samples_between(start, as_of)
            if any(ws <= s.ts < we for ws, we in windows)
        ]

        # 停运（含上级停运）覆盖时段的量测剔除出评分
        outage_map = self._outage_map(topo_v, start, as_of)
        excluded: list = []
        kept: list = []
        for s in in_window:
            outages = outage_map.get(s.node_id, [])
            if any(o.is_active_at(s.ts, as_of) for o in outages):
                excluded.append(s)
            else:
                kept.append(s)

        scored, _no_outdoor = compute_point_metrics(
            kept, store.outdoor_at_or_before, met_v.curve, met_v.configs, th.supply_deficit_c
        )
        by_node: dict[str, list[PointMetric]] = {}
        for pm in scored:
            by_node.setdefault(pm.node_id, []).append(pm)
        anomalous_by_node = {
            nid: [p for p in pts if p.anomalous] for nid, pts in by_node.items()
        }
        ctx = _Ctx(as_of, analysis_version, topo_v, th, by_node, anomalous_by_node)

        intervals: list[models.AnomalyInterval] = []
        equipment_spans: dict[str, list[tuple[datetime, datetime]]] = {}

        # 1) 设备停运区间（停运记录驱动）
        for iv in self._outage_intervals(ctx, outage_map, excluded, start):
            intervals.append(iv)
            equipment_spans.setdefault(iv.node_id, []).append((iv.start, iv.end))

        # 2) 持续低流量 → 疑似设备停运
        for iv in self._low_flow_intervals(ctx, equipment_spans):
            intervals.append(iv)
            equipment_spans.setdefault(iv.node_id, []).append((iv.start, iv.end))

        # 3) 热源/换热站自身欠供 → 设备侧（按拓扑深度自顶向下，祖先优先归集）
        for iv in self._own_deficit_intervals(ctx, equipment_spans):
            intervals.append(iv)
            equipment_spans.setdefault(iv.node_id, []).append((iv.start, iv.end))

        # 4) 楼栋层归集：同站多栋欠供 → 站侧水力失衡；单栋欠供 → 用户侧
        intervals += self._classify_network(ctx, equipment_spans)

        # 5) 事件生命周期：开新事件 / 延续打开事件 / 关闭未再出现的事件
        touched: list[str] = []
        for iv in intervals:
            event = self._match_open_event(iv)
            if event is None:
                event = self._open_event(iv, as_of)
            event.interval_ids.append(iv.interval_id)
            event.updated_at = as_of
            touched.append(event.event_id)
        for event in store.open_events():
            if event.event_id not in touched:
                event.status = "closed"
                event.closed_at = as_of

        # 6) 依据当前规则版本为没有未确认建议的事件生成建议
        suggestion_ids: list[str] = []
        for event_id in touched:
            if store.open_suggestion_for(event_id) is not None:
                continue
            event = store.events[event_id]
            latest = store.intervals[event.interval_ids[-1]]
            suggestion = build_suggestion(
                store.next_id("SUG"), event, latest, ruleset, as_of
            )
            store.suggestions[suggestion.suggestion_id] = suggestion
            suggestion_ids.append(suggestion.suggestion_id)

        run = models.DiagnosisRun(
            run_id=store.next_id("RUN"),
            analysis_version=analysis_version,
            as_of=as_of,
            horizon_hours=horizon_hours,
            interval_ids=tuple(iv.interval_id for iv in intervals),
            event_ids=tuple(touched),
            suggestion_ids=tuple(suggestion_ids),
        )
        store.runs[run.run_id] = run
        return run

    # ------------------------------------------------------------------ 停运
    def _outage_map(self, topo_v: TopologyVersion, start: datetime, as_of: datetime):
        """node_id -> 该节点（含祖先节点）在 [start, as_of] 内生效的停运记录。"""
        result: dict[str, list[OutageRecord]] = {}
        for o in self._store.outages.values():
            if o.effective_end(as_of) < start or o.start > as_of:
                continue
            affected = [o.node_id, *self._topology.descendants_of(o.node_id)]
            for nid in affected:
                result.setdefault(nid, []).append(o)
        return result

    def _outage_intervals(self, ctx: _Ctx, outage_map, excluded, start):
        intervals = []
        seen: set[str] = set()
        for node_id, outages in outage_map.items():
            for o in outages:
                if o.outage_id in seen:
                    continue
                seen.add(o.outage_id)
                s = max(o.start, start)
                e = min(o.effective_end(ctx.as_of), ctx.as_of)
                if e <= s:
                    continue
                node_excluded = [
                    x for x in excluded if x.node_id == node_id and s <= x.ts <= e
                ]
                kind_cn = "计划停运" if o.kind == "planned" else "临时停运"
                extra = [Evidence(
                    "outage", o.outage_id,
                    f"{kind_cn} {o.start.isoformat()} ~ "
                    f"{o.effective_end(ctx.as_of).isoformat()} {o.reason}".strip(),
                )]
                flows = [x.flow_m3_h for x in node_excluded]
                metrics = {
                    "sample_count": len(node_excluded),
                    "avg_flow_m3_h": round(sum(flows) / len(flows), 2) if flows else None,
                }
                severity = 0.5 if o.kind == "planned" else 0.9
                intervals.append(self._build(
                    ctx, o.node_id, models.EQUIPMENT_OUTAGE, (s, e), [],
                    extra_evidence=extra, severity=severity, metrics_override=metrics,
                ))
        return intervals

    # ------------------------------------------------------------------ 低流量
    def _low_flow_intervals(self, ctx: _Ctx, equipment_spans):
        intervals = []
        th = ctx.thresholds
        # 自顶向下处理，父站低流量区间先行登记，下游楼栋不再重复报警
        node_ids = sorted(ctx.by_node, key=lambda nid: self._depth(ctx.topo, nid))
        for node_id in node_ids:
            pts = ctx.by_node[node_id]
            low = [
                p for p in pts
                if p.flow_ratio is not None and p.flow_ratio < th.flow_ratio_low
                and not self._covered_with_ancestors(equipment_spans, ctx.topo, node_id, p.ts)
            ]
            for seg in merge_points([p.ts for p in low], th.max_gap_minutes):
                seg_pts = [p for p in low if seg[0] <= p.ts <= seg[1]]
                ratios = [p.flow_ratio for p in seg_pts if p.flow_ratio is not None]
                avg_ratio = sum(ratios) / len(ratios) if ratios else 0.0
                extra = [Evidence(
                    "metric", "avg_flow_ratio",
                    f"区间平均流量仅为设计值 {avg_ratio:.0%}"
                    f"（阈值 {th.flow_ratio_low:.0%}）",
                    round(avg_ratio, 3),
                )]
                interval = self._build(
                    ctx, node_id, models.EQUIPMENT_OUTAGE, seg, seg_pts, extra_evidence=extra
                )
                intervals.append(interval)
                equipment_spans.setdefault(node_id, []).append(
                    (interval.start, interval.end)
                )
        return intervals

    # ------------------------------------------------------------------ 自身欠供
    def _own_deficit_intervals(self, ctx: _Ctx, equipment_spans):
        intervals = []
        nodes = sorted(
            (n for n in ctx.topo.nodes.values() if n.node_type != "building"),
            key=lambda n: self._depth(ctx.topo, n.node_id),
        )
        for node in nodes:
            pts = [
                p for p in ctx.anomalous_by_node.get(node.node_id, [])
                if not self._covered_with_ancestors(
                    equipment_spans, ctx.topo, node.node_id, p.ts
                )
            ]
            for seg in merge_points([p.ts for p in pts], ctx.thresholds.max_gap_minutes):
                seg_pts = [p for p in pts if seg[0] <= p.ts <= seg[1]]
                intervals.append(self._build(
                    ctx, node.node_id, models.EQUIPMENT_OUTAGE, seg, seg_pts,
                    extra_evidence=[Evidence(
                        "metric", "own_supply_deficit",
                        f"{node.node_id} 自身供水持续欠供，归集为设备侧事件",
                    )],
                ))
                equipment_spans.setdefault(node.node_id, []).append(seg)
        return intervals

    # ------------------------------------------------------------------ 楼栋归集
    def _classify_network(self, ctx: _Ctx, equipment_spans):
        intervals = []
        th = ctx.thresholds
        for station in self._topology.stations():
            st_id = station.node_id
            unexplained: dict[str, list[PointMetric]] = {}
            for child in self._topology.children_of(st_id):
                pts = [
                    p for p in ctx.anomalous_by_node.get(child.node_id, [])
                    if not self._covered_with_ancestors(
                        equipment_spans, ctx.topo, child.node_id, p.ts
                    )
                ]
                if pts:
                    unexplained[child.node_id] = pts
            if not unexplained:
                continue
            all_ts = [p.ts for pts in unexplained.values() for p in pts]
            for seg in merge_points(all_ts, th.max_gap_minutes):
                involved = {
                    cid: [p for p in pts if seg[0] <= p.ts <= seg[1]]
                    for cid, pts in unexplained.items()
                }
                involved = {cid: ps for cid, ps in involved.items() if ps}
                if len(involved) >= th.min_imbalance_buildings:
                    all_pts = [p for ps in involved.values() for p in ps]
                    spread = self._deficit_spread(involved)
                    extra = [
                        Evidence(
                            "metric", "deficit_spread_c",
                            f"建筑间欠供离散度 {spread:.1f} °C",
                            round(spread, 2),
                        ),
                        Evidence(
                            "topology", st_id,
                            f"同站 {len(involved)} 栋建筑同时欠供: "
                            f"{'、'.join(sorted(involved))}",
                        ),
                    ]
                    intervals.append(self._build(
                        ctx, st_id, models.HYDRAULIC_IMBALANCE, seg, all_pts,
                        extra_evidence=extra, affected=tuple(sorted(involved)),
                    ))
                else:
                    for cid, ps in involved.items():
                        for bseg in merge_points([p.ts for p in ps], th.max_gap_minutes):
                            bpts = [p for p in ps if bseg[0] <= p.ts <= bseg[1]]
                            intervals.append(self._build(
                                ctx, cid, models.USER_SIDE, bseg, bpts
                            ))
        return intervals

    # ------------------------------------------------------------------ 区间工厂
    def _build(self, ctx: _Ctx, node_id, category, seg, points,
               extra_evidence=(), affected=None, severity=None, metrics_override=None):
        start, end = seg
        if end == start:
            end = start + timedelta(minutes=30)  # 单点区间给最小宽度
        impact = self._impact(ctx, node_id, category, start, end, affected)
        complaint_count = len(impact.complaint_ids)
        metrics = dict(metrics_override) if metrics_override else self._summarize(points)
        metrics["complaint_count"] = complaint_count
        evidence = self._evidence(ctx, node_id, points, impact, extra_evidence)
        sev = severity if severity is not None else self._severity(
            points, complaint_count, ctx.thresholds
        )
        interval = models.AnomalyInterval(
            interval_id=self._store.next_id("IVL"),
            node_id=node_id,
            category=category,
            start=start,
            end=end,
            severity=sev,
            metrics=metrics,
            evidence=evidence,
            impact=impact,
            analysis_version=ctx.analysis_version,
        )
        self._store.intervals[interval.interval_id] = interval
        return interval

    def _impact(self, ctx: _Ctx, node_id, category, start, end, forced_affected):
        if forced_affected is not None:
            affected = list(forced_affected)
        elif category == models.USER_SIDE:
            affected = [node_id]
        else:
            descendants = self._topology.descendants_of(node_id)
            affected = [
                d for d in descendants
                if any(start <= p.ts <= end for p in ctx.anomalous_by_node.get(d, []))
            ]
            if not affected:
                # 下游无量测（如停运期间）时按拓扑全部计入影响范围
                affected = descendants
        complaints = self._store.complaints_between(set(affected) | {node_id}, start, end)
        households = 0
        seen_households = False
        for nid in affected:
            node = ctx.topo.nodes.get(nid)
            h = node.attrs.get("households") if node else None
            if isinstance(h, int):
                households += h
                seen_households = True
        return models.ImpactScope(
            node_id=node_id,
            affected_node_ids=tuple(affected),
            complaint_ids=tuple(c.complaint_id for c in complaints),
            households=households if seen_households else None,
        )

    def _evidence(self, ctx: _Ctx, node_id, points, impact, extra) -> tuple[Evidence, ...]:
        th = ctx.thresholds
        evidence = list(extra)
        evidence.append(Evidence(
            "threshold", "supply_deficit_c",
            f"供水欠供阈值 {th.supply_deficit_c} °C", th.supply_deficit_c,
        ))
        if points:
            max_deficit = max(p.deficit_c for p in points)
            avg_deficit = sum(p.deficit_c for p in points) / len(points)
            avg_outdoor = sum(p.outdoor_c for p in points) / len(points)
            evidence.append(Evidence(
                "metric", "max_supply_deficit_c",
                f"区间最大欠供 {max_deficit:.1f} °C", round(max_deficit, 2),
            ))
            evidence.append(Evidence(
                "metric", "avg_supply_deficit_c",
                f"区间平均欠供 {avg_deficit:.1f} °C", round(avg_deficit, 2),
            ))
            evidence.append(Evidence(
                "outdoor", "outdoor_avg_c",
                f"区间平均室外温度 {avg_outdoor:.1f} °C", round(avg_outdoor, 2),
            ))
        for cid in impact.complaint_ids:
            c = self._store.complaints[cid]
            evidence.append(Evidence(
                "complaint", cid,
                f"投诉[{c.node_id}] {c.kind} {c.detail}".strip(),
            ))
        if len(impact.affected_node_ids) > 1:
            evidence.append(Evidence(
                "topology", node_id,
                f"影响范围 {len(impact.affected_node_ids)} 个节点: "
                f"{'、'.join(impact.affected_node_ids)}",
            ))
        return tuple(evidence)

    @staticmethod
    def _summarize(points) -> dict:
        if not points:
            return {"sample_count": 0}
        ratios = [p.flow_ratio for p in points if p.flow_ratio is not None]
        return {
            "sample_count": len(points),
            "avg_supply_deficit_c": round(sum(p.deficit_c for p in points) / len(points), 2),
            "max_supply_deficit_c": round(max(p.deficit_c for p in points), 2),
            "avg_delta_t_c": round(sum(p.delta_t_c for p in points) / len(points), 2),
            "avg_flow_ratio": round(sum(ratios) / len(ratios), 3) if ratios else None,
            "outdoor_avg_c": round(sum(p.outdoor_c for p in points) / len(points), 2),
        }

    @staticmethod
    def _severity(points, complaint_count, th) -> float:
        if points:
            max_deficit = max(p.deficit_c for p in points)
            base = min(1.0, max(0.0, max_deficit / (3 * th.supply_deficit_c)))
        else:
            base = 0.5
        return round(min(1.0, base + 0.1 * min(complaint_count, 3)), 2)

    @staticmethod
    def _deficit_spread(involved) -> float:
        means = [
            sum(p.deficit_c for p in pts) / len(pts) for pts in involved.values()
        ]
        return max(means) - min(means) if means else 0.0

    # ------------------------------------------------------------------ 事件
    def _match_open_event(self, interval):
        for event in self._store.open_events():
            if event.node_id == interval.node_id and event.category == interval.category:
                return event
        return None

    def _open_event(self, interval, as_of):
        previous = None
        for event in self._store.events.values():
            if (
                event.node_id == interval.node_id
                and event.category == interval.category
                and event.status == "closed"
                and (previous is None or (event.closed_at or as_of) > (previous.closed_at or as_of))
            ):
                previous = event
        event = models.AnomalyEvent(
            event_id=self._store.next_id("EVT"),
            node_id=interval.node_id,
            category=interval.category,
            status="open",
            opened_at=as_of,
            updated_at=as_of,
            previous_event_id=previous.event_id if previous else None,
            analysis_version=interval.analysis_version,
        )
        self._store.events[event.event_id] = event
        self._store.conclusions[event.event_id] = models.DiagnosisConclusion(
            event_id=event.event_id,
            category=interval.category,
            status="pending",
            updated_at=as_of,
        )
        return event

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _depth(topo_v: TopologyVersion, node_id: str) -> int:
        depth = 0
        cur = topo_v.nodes.get(node_id)
        while cur is not None and cur.parent_id is not None:
            depth += 1
            cur = topo_v.nodes.get(cur.parent_id)
        return depth

    @staticmethod
    def _covered_with_ancestors(equipment_spans, topo_v: TopologyVersion, node_id, ts) -> bool:
        cur: str | None = node_id
        while cur is not None:
            if _covered(equipment_spans.get(cur, []), ts):
                return True
            node = topo_v.nodes.get(cur)
            cur = node.parent_id if node else None
        return False
