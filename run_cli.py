import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))
from heating_diagnosis.contracts import HeatNode, RuntimeSample

node = HeatNode("HX-2", "exchange_station", "SOURCE-1")
sample = RuntimeSample(node.node_id, 78.0, 51.0, 33.5)
print(json.dumps({"node": node.node_id, "delta": sample.supply_c - sample.return_c}, ensure_ascii=False))
