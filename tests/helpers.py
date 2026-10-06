"""测试公共构造器。"""
import sys
from datetime import datetime, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from heating_diagnosis.clock import FixedClock, HeatingWindow
from heating_diagnosis.contracts import (
    Complaint,
    HeatNode,
    OutdoorSnapshot,
    OutageRecord,
    SamplePoint,
)
from heating_diagnosis.ingestion import DataBatch
from heating_diagnosis.metering import ClimateCurve, MeteringConfig
from heating_diagnosis.rules import Thresholds
from heating_diagnosis.service import DiagnosisService

#: 期望供水温度：40 + (5 - (-5)) * 1.0 = 50°C
BASE_NOW = datetime(2026, 1, 6, 23, 30)


def make_service(now=BASE_NOW, window=None, thresholds=None):
    """配置好拓扑/计量/规则的服务与固定时钟。

    拓扑：SRC-1 → HX-1 → B-1/B-2/B-3；SRC-1 → HX-2 → B-4。
    """
    clock = FixedClock(now)
    service = DiagnosisService(
        clock=clock, window=window or HeatingWindow(time(20, 0), time(8, 0))
    )
    nodes = [
        HeatNode("SRC-1", "source"),
        HeatNode("HX-1", "exchange_station", "SRC-1"),
        HeatNode("HX-2", "exchange_station", "SRC-1"),
        HeatNode("B-1", "building", "HX-1", {"households": 100}),
        HeatNode("B-2", "building", "HX-1", {"households": 120}),
        HeatNode("B-3", "building", "HX-1", {"households": 80}),
        HeatNode("B-4", "building", "HX-2", {"households": 90}),
    ]
    service.register_topology(nodes, "初始拓扑")
    service.revise_metering(
        ClimateCurve(base_supply_c=40.0, ref_outdoor_c=5.0, slope=1.0),
        [MeteringConfig(n, 100.0)
         for n in ("SRC-1", "HX-1", "HX-2", "B-1", "B-2", "B-3", "B-4")],
        "初始计量",
    )
    service.register_rules(thresholds or Thresholds(), "初始规则")
    return service, clock


def make_bare_service(now=BASE_NOW, window=None):
    """未配置拓扑/计量/规则的空服务（用于测试配置接口本身）。"""
    clock = FixedClock(now)
    service = DiagnosisService(
        clock=clock, window=window or HeatingWindow(time(20, 0), time(8, 0))
    )
    return service, clock


def sample(node, h, m, supply, flow=100.0, day=6, ret=None):
    ret = supply - 18.0 if ret is None else ret
    return SamplePoint(node, datetime(2026, 1, day, h, m), supply, ret, flow)


def outdoor(h, temp=-5.0, day=6, m=0):
    return OutdoorSnapshot(datetime(2026, 1, day, h, m), temp)


def complaint(cid, node, h, m, day=6, detail="室温 15°C"):
    return Complaint(cid, node, datetime(2026, 1, day, h, m), detail=detail)


def outage(oid, node, sh, sm, eh=None, em=None, kind="planned", day=6, reason=""):
    end = datetime(2026, 1, day, eh, em) if eh is not None else None
    return OutageRecord(oid, node, datetime(2026, 1, day, sh, sm), end, kind, reason)


def batch(bid, kind, items):
    return DataBatch(bid, kind, tuple(items))


def ingest_imbalance_scene(service, bid="B1"):
    """HX-1 站侧正常，B-1/B-2 欠供，B-3 正常 → 水力失衡场景。"""
    service.ingest(batch(f"{bid}-OUT", "outdoor",
                         [outdoor(h) for h in (20, 21, 22, 23)]))
    items = []
    for h, m in ((21, 0), (21, 30), (22, 0), (22, 30), (23, 0)):
        items.append(sample("HX-1", h, m, 50.0))
        items.append(sample("B-1", h, m, 43.0))
        items.append(sample("B-2", h, m, 43.5))
        items.append(sample("B-3", h, m, 50.0))
    service.ingest(batch(f"{bid}-S", "samples", items))
