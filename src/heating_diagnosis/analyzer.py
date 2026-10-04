"""异常区间计算：把样本、室外快照与停运关联成可解释的异常区间。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import EquipmentOutage, OutdoorSnapshot, TimedSample
from .rules import RuleSet


@dataclass
class AnomalyInterval:
    kind: str  # low_supply | low_delta_t | station_drop | complaint_cluster
    node_id: str
    start: datetime
    end: datetime
    sample_count: int
    evidence: list[str]
    metrics: dict


@dataclass
class NodeAnalysis:
    node_id: str
    intervals: list[AnomalyInterval]
    kept_samples: int
    excluded_outage_samples: int
    outage_notes: list[str]


def nearest_outdoor(outdoor: list[OutdoorSnapshot], ts: datetime) -> OutdoorSnapshot | None:
    """取不晚于 ts 的最近室外快照；没有则取最早一条。"""
    if not outdoor:
        return None
    before = [o for o in outdoor if o.ts <= ts]
    if before:
        return max(before, key=lambda o: o.ts)
    return min(outdoor, key=lambda o: o.ts)


def sample_violations(rules: RuleSet, sample: TimedSample,
                      snap: OutdoorSnapshot | None) -> list[str]:
    kinds = []
    if snap is not None and rules.expected_supply(snap.outdoor_c) - sample.supply_c > rules.supply_deficit_c:
        kinds.append("low_supply")
    if sample.delta_t < rules.min_delta_t_c:
        kinds.append("low_delta_t")
    return kinds


def _nearest_sample(samples: list[TimedSample], ts: datetime,
                    tolerance_minutes: int) -> TimedSample | None:
    best, best_gap = None, None
    for s in samples:
        gap = abs((s.ts - ts).total_seconds())
        if best is None or gap < best_gap:
            best, best_gap = s, gap
    if best is not None and best_gap <= tolerance_minutes * 60:
        return best
    return None


def _make_interval(kind: str, samples: list[TimedSample], evidence: list[str]) -> AnomalyInterval:
    return AnomalyInterval(
        kind=kind,
        node_id=samples[0].node_id,
        start=samples[0].ts,
        end=samples[-1].ts,
        sample_count=len(samples),
        evidence=list(evidence),
        metrics={
            "min_supply_c": round(min(s.supply_c for s in samples), 2),
            "avg_delta_t_c": round(sum(s.delta_t for s in samples) / len(samples), 2),
            "avg_flow_m3_h": round(sum(s.flow_m3_h for s in samples) / len(samples), 2),
        })


def _merge(points: list[tuple[str, TimedSample, str]], gap: timedelta) -> list[AnomalyInterval]:
    """同类异常点按时间归并：间隔不超过 gap 的并入同一区间。"""
    by_kind: dict[str, list[tuple[TimedSample, str]]] = {}
    for kind, sample, evidence in points:
        by_kind.setdefault(kind, []).append((sample, evidence))
    intervals = []
    for kind, items in by_kind.items():
        items.sort(key=lambda x: x[0].ts)
        cur_samples: list[TimedSample] = []
        cur_evidence: list[str] = []
        for sample, evidence in items:
            if cur_samples and sample.ts - cur_samples[-1].ts > gap:
                intervals.append(_make_interval(kind, cur_samples, cur_evidence))
                cur_samples, cur_evidence = [], []
            cur_samples.append(sample)
            cur_evidence.append(evidence)
        if cur_samples:
            intervals.append(_make_interval(kind, cur_samples, cur_evidence))
    intervals.sort(key=lambda i: i.start)
    return intervals


class Analyzer:
    def __init__(self, rules: RuleSet):
        self.rules = rules

    def analyze(self, node_id: str, samples: list[TimedSample],
                outdoor: list[OutdoorSnapshot], outages: list[EquipmentOutage],
                start: datetime, end: datetime,
                source_samples: list[TimedSample] | None = None) -> NodeAnalysis:
        kept, excluded = [], []
        for s in sorted(samples, key=lambda x: x.ts):
            if not (start <= s.ts <= end):
                continue
            outage = next((o for o in outages if o.covers(s.ts)), None)
            if outage is None:
                kept.append(s)
            else:
                excluded.append((s, outage))
        points: list[tuple[str, TimedSample, str]] = []
        for s in kept:
            snap = nearest_outdoor(outdoor, s.ts)
            if snap is not None:
                expected = self.rules.expected_supply(snap.outdoor_c)
                deficit = expected - s.supply_c
                if deficit > self.rules.supply_deficit_c:
                    points.append(("low_supply", s,
                        f"{s.ts:%Y-%m-%d %H:%M} 供水 {s.supply_c:.1f}℃ 低于气候补偿期望 "
                        f"{expected:.1f}℃（室外 {snap.outdoor_c:.1f}℃）偏差 {deficit:.1f}℃ "
                        f"超阈值 {self.rules.supply_deficit_c:.1f}℃"))
            if s.delta_t < self.rules.min_delta_t_c:
                points.append(("low_delta_t", s,
                    f"{s.ts:%Y-%m-%d %H:%M} 供回水温差 {s.delta_t:.1f}℃ 低于阈值 "
                    f"{self.rules.min_delta_t_c:.1f}℃（流量 {s.flow_m3_h:.1f}m³/h）"))
            if source_samples:
                src = _nearest_sample(source_samples, s.ts, self.rules.source_match_minutes)
                if src is not None:
                    drop = src.supply_c - s.supply_c
                    if drop > self.rules.station_drop_c:
                        points.append(("station_drop", s,
                            f"{s.ts:%Y-%m-%d %H:%M} 站供 {s.supply_c:.1f}℃ 较热源出口 "
                            f"{src.supply_c:.1f}℃ 低 {drop:.1f}℃ 超阈值 {self.rules.station_drop_c:.1f}℃"))
        intervals = _merge(points, timedelta(minutes=self.rules.interval_gap_minutes))
        notes = []
        for o in outages:
            n = sum(1 for _, hit in excluded if hit is o)
            if n:
                end_txt = f"{o.end:%m-%d %H:%M}" if o.end else "进行中"
                notes.append(f"停运 {o.outage_id}（{o.reason or '未说明'}，"
                             f"{o.start:%m-%d %H:%M}~{end_txt}）剔除 {n} 条样本")
        return NodeAnalysis(node_id, intervals, len(kept), len(excluded), notes)
