# 供热低效诊断与处置

把供回水温度、流量、室外条件快照、设备停运和投诉按供热拓扑（热源 → 换热站 → 楼栋）关联起来，
计算**可解释的异常区间**与**影响范围**，依据**规则版本**提出调节 / 巡检 / 观察建议；
调度员确认建议后生成工单，工单执行结果反馈到诊断结论，形成处置闭环。

纯 Python 标准库实现，无外部依赖。

## 运行

```bash
python3 -m unittest discover -s tests -v      # 测试
python3 -m compileall -q src tests run_cli.py heating_cli.py   # 编译检查
python3 run_cli.py                            # 契约冒烟
python3 heating_cli.py demo                   # 端到端演示（内存态）
```

## 命令行

```bash
python3 heating_cli.py --db state.json --now 2026-01-06T23:30:00 <子命令>
```

- `--db`：状态文件（JSON），多次调用延续同一状态；`--now`：注入固定时钟（回放/演示用），缺省为系统时钟。
- `configure --file cfg.json`：注册拓扑 / 计量 / 规则 / 采暖窗口（任一修订都创建新的分析版本）
- `ingest --file batch.json`：幂等接入数据批次（samples / outdoor / complaints / outages）
- `diagnose [--horizon-hours 24]`：运行诊断，产出异常区间、事件与建议
- `events / event <id>`：事件列表与详情（含复发链、诊断结论、区间证据）
- `suggestions / explain <id>`：建议列表与**每条建议的证据链说明**
- `confirm <建议id> --by <调度员>`：确认建议生成工单（同一建议不重复派单）
- `complete <工单id> --cause <类别|no_issue> [--unresolved] [--notes ...]`：回填执行结果并反馈诊断结论
- `compare <工单id>`：**处置前后指标对比**（等时长窗口，含窗口边界与样本量）
- `versions`：分析版本历史
- `serve --port 8080`：启动 HTTP 接口

## HTTP 接口

`POST /configure`、`POST /ingest`、`POST /diagnosis/run`、`GET /events`、`GET /events/{id}`、
`GET /suggestions`、`GET /suggestions/{id}/explain`、`POST /suggestions/{id}/confirm`、
`POST /work-orders/{id}/complete`、`GET /work-orders/{id}/compare`、`GET /versions`、`GET /health`。

编程接口为 `heating_diagnosis.service.DiagnosisService`，HTTP 与 CLI 均为其上的薄层。

## 数据格式

配置（configure）：

```json
{
  "heating_window": {"start": "20:00", "end": "08:00"},
  "topology": {"note": "初始", "nodes": [
    {"node_id": "SRC-1", "node_type": "source"},
    {"node_id": "HX-1", "node_type": "exchange_station", "parent_id": "SRC-1"},
    {"node_id": "B-1", "node_type": "building", "parent_id": "HX-1", "attrs": {"households": 120}}
  ]},
  "metering": {"curve": {"base_supply_c": 40, "ref_outdoor_c": 5, "slope": 1.0},
               "configs": [{"node_id": "HX-1", "design_flow_m3_h": 100, "calibration": 1.0}]},
  "rules": {"thresholds": {"supply_deficit_c": 3.0, "min_imbalance_buildings": 2}}
}
```

批次（ingest）：`{"batch_id": "B1", "kind": "samples", "items": [...]}`，
items 按 kind 分别为量测点 / 室外快照 / 投诉 / 停运记录（见 `contracts.py`）。

## 关键设计

- **可解释异常区间**：按气候补偿曲线计算期望供水温度，欠供超阈值记为异常点，
  相邻点合并为区间；区间携带阈值、欠供指标、室外温度、投诉、停运、拓扑影响等证据。
- **根因归集，避免重复派单**：停运（含上级停运）覆盖时段的量测剔除出评分；
  换热站/热源自身欠供归集为设备侧事件并抑制下游楼栋报警；同站多栋欠供而站侧正常
  判定管网水力失衡（挂在换热站）；单栋欠供判定用户侧异常。
- **影响范围**：受影响下游节点 + 区间内关联投诉 + 户数合计。
- **幂等**：批次级（batch_id 入账判重）+ 记录级（同节点同时刻等完全重复跳过）；
  诊断按增量运行（每次只处理上次运行之后的新数据），相同数据不重复计入。
- **可注入时钟**：所有“当前时刻”经 `Clock` 注入；采暖窗口支持跨午夜（半开区间），
  未结束的临时停运以注入时钟确定有效结束时间。
- **事件生命周期**：事件随异常消失而关闭；已关闭事件重新出现时创建新事件并以
  `previous_event_id` 关联成复发链，不覆盖历史。
- **版本化**：拓扑版本 × 计量版本 × 规则版本组合成分析版本；任一修订创建新版本，
  历史运行与结论保留在旧版本下；规则修订使旧的未确认建议作废（superseded）。
- **处置闭环**：确认建议 → 工单（幂等）→ 执行结果反馈诊断结论
  （confirmed / revised / false_positive），解决即关闭事件；
  `compare` 以工单完成时刻为界做等时长前后对照并给出是否改善的判定与说明。
