# 供热低效诊断与处置

本项目定义供热拓扑节点、运行样本和处置建议的基础契约，用于关联热源、换热站与楼栋的运行信息。

## 功能

- **拓扑关联诊断**：供回水温度、流量、室外快照、设备停运与投诉按供热拓扑（热源→换热站→楼栋）关联，计算可解释的异常区间与影响范围。
- **规则版本化建议**：按 `RuleSet.rule_version` 生成调节（adjust）/巡检（inspect）/观察（observe）建议，每条建议携带证据链。
- **工单闭环**：调度员确认建议生成工单，工单执行结果反馈到诊断结论；未闭环事件保持打开。
- **分析版本**：拓扑或计量修订创建新分析版本，诊断结论按版本归属。
- **幂等接入**：相同批次号的数据不重复计入。
- **可注入时钟**：跨午夜采暖窗口与临时停运（无结束时间）统一由注入时钟判定。
- **事件关联**：已关闭事件复发时新建事件并通过 `reoccurrence_of` 关联，不覆盖历史。
- **指标对比**：接口与命令行均可比较处置前后指标，并能说明每条建议的证据。

## 模块

| 模块 | 职责 |
| --- | --- |
| `contracts.py` | 基础契约：拓扑节点、运行样本 |
| `models.py` | 带时标样本、室外快照、停运、投诉、事件、建议、工单、版本 |
| `topology.py` | 拓扑关系与影响范围 |
| `windows.py` | 跨午夜每日采暖窗口 |
| `analyzer.py` | 异常区间计算（停运样本剔除、区间归并） |
| `rules.py` | 规则集与建议引擎（阈值随规则版本演进） |
| `service.py` | 诊断与处置门面（接入、诊断、确认、反馈、对比、解释） |
| `store.py` | 内存状态与 JSON 持久化 |
| `cli.py` | 命令行入口 |

## 命令行示例

```bash
python run_cli.py topology --node HX-2 --type exchange_station --parent SOURCE-1
python run_cli.py ingest --batch B1 --kind samples --file samples.json   # 重复执行不重复计入
python run_cli.py diagnose --node BLDG-101 --window 22:00-06:00          # 跨午夜窗口
python run_cli.py confirm --suggestion S-0001 --by 调度员甲               # 生成工单
python run_cli.py feedback --order W-0001 --resolved yes --note 已调节    # 反馈诊断结论
python run_cli.py compare --node BLDG-101 --before-start ... --after-start ...
python run_cli.py explain --suggestion S-0001                             # 建议证据
```

运行测试：`python -m unittest discover -s tests -v`

编译检查：`python -m compileall -q src tests run_cli.py`

命令行冒烟：`python run_cli.py`
