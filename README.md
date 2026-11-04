# futures-execution-regimes
成果围绕期货母单的执行方式与执行时长选择，复用 PandaData 历史分钟量价，在同一构造母单和统一口径下比较 ASAP、TWAP 与按量调度的成本、完成率、残余头寸和价格风险，形成研究报告、可复用工程文件，以及已注册到 QuantStudio 的执行决策 skill。该 skill 根据订单规模、完成期限、风险预算和业务目标，返回执行方法、计划释放时长及取舍理由；依据不足时给出条件分析或具体待补信息，部分目标可判断时仍能提供计划

## 目录

| 路径 | 内容 |
|---|---|
| [`REPORT.md`](REPORT.md) | 既有结果整合报告：三层证据、边界与未决（经济结果截至 2026-11-03，接口 2026-11-04 更新） |
| [`delivery/futures-execution-decision/`](delivery/futures-execution-decision/) | 可复用技能包：`SKILL.md`、`scripts/`、`data/`、`tests/`、`examples/`、`LICENSE` |
| [`RESULTS_DETAIL.md`](RESULTS_DETAIL.md)、[`results/`](results/) | 结果明细、配对指标、成本归因与条件包络表 |
| [`decision_research/`](decision_research/)、[`exante_policy_20261022/`](exante_policy_20261022/)、[`volume_activity_20261022/`](volume_activity_20261022/) | 六配置规则评价、事前选型研究、活跃度预测与执行价值 |
| [`plan_recovery_20261104/`](plan_recovery_20261104/)、[`skill_simplification_20261103/`](skill_simplification_20261103/)、[`skill_walkthrough_20261103/`](skill_walkthrough_20261103/) | 接口行为验证、简化记录与调用走查 |
| [`architecture_20261103/`](architecture_20261103/)、[`jev_diagnostic_20261026/`](jev_diagnostic_20261026/)、[`jev_assisted_20261022/`](jev_assisted_20261022/) | 证据对照与 Jev 相关诊断结论 |
| [`scripts/`](scripts/)、[`ENGINEERING.md`](ENGINEERING.md)、[`DATA_COVERAGE.md`](DATA_COVERAGE.md) | 取数、对齐、回放脚本与数据覆盖说明 |
| [`DATA_PREPARATION.md`](DATA_PREPARATION.md) | 数据格式、本地准备要求与授权边界 |

## 研究问题与可靠负结果

**问题：** 一笔已经决定要做的母单，什么时候应该从 ASAP 改成 TWAP 或按历史成交量分批？

**成立的条件结论：** 在给定的完成门槛、风险预算与冲击情景内，拉长计划释放时长可以降低模型总成本；该取舍在多品种样本中成立，但只在所声明的情景内成立。成本差异主要来自假设冲击项，冲击系数 `a` 未标定，所以模型里的"省"不是已实现收益。

**成立的负结果：** 已测试的事前调度规则（9 桶方向×波动规则 P、滚动规则 B、状态规则 C，以及活跃度加权选型 ACT）**都没有获得相对冻结基准 A 的采用证据**：成本差异区间跨零或不足 0.5bp，或风险门槛不通过。事前活跃度能改善当日 60 分钟总量预测（归一化绝对误差 −15.9% 验证 / −19.4% 留出），但没有转化成更好的执行选择。这是规则层面的负结果，不是"市场没有优势"。

## 技能输入输出

输入一份请求 JSON：母单（`symbol`、`side`、`qty_lots`、`release_trading_date`、`release_label`）、`Dmax_trading_minutes`（业务最晚期限，`1..60`）、`history_npz`（释放日之前最近 20 个交易日的分钟历史）、`constraints`（`risk_sd_budget_bp` 必填，`min_completion_probability` 默认 0.95、`max_unfilled_lots` 默认 0），可选 `scenario`（`cap_frac` 0.10、`fee_spread_bp` 1.3、`impact_a_bp`、`phi` 1.0、`terminal_price_change_bp` 0.0）与可选 `preference`（`cost_first` / `wait_first` / 未明确）。

输出：`status`、`choice`（`decision.py` 原有的条件最优，语义不变）、`method`、`h`，以及按偏好给出的 `plan`（方法、`h`、预测成本、等待负担、完成率、风险）、`candidates`、`effective_constraints` 与中文理由。常量：`execution_action=NONE`、`production_eligibility=INSUFFICIENT`，不连接任何交易接口，不是下单许可。

`h` 是计划释放时长，**不保证在 `h` 内完成**；`completion` 是 20 条历史路径中残余不超上限的比例，不是校准过的成交概率。

## 运行入口

以下命令都在 `delivery/futures-execution-decision/` 目录内执行，全部使用相对路径。

```bash
# 主入口：带偏好路由的计划
python3 scripts/preference_plan.py --request tests/fixtures/compare_cost_first.json --out /tmp/plan.json

# 原有入口
python3 scripts/decision.py --request examples/request_compare.json --out /tmp/compare.json
python3 scripts/decision.py --request examples/request_baseline_first.json --out /tmp/baseline.json

# 单元测试
python3 -m unittest discover -s tests -v
```

`examples/` 与 `tests/fixtures/` 下的请求都指向释放前 20 个交易日的分钟历史 NPZ，**这些 NPZ 不随本仓库分发**，需要使用者按 [`DATA_PREPARATION.md`](DATA_PREPARATION.md) 用自有授权数据准备到同名路径。没有这些文件时请求返回 `INSUFFICIENT_DATA` 并说明缺项，不会静默改用别的品种或窗口；因此**未准备数据的 clone 不能声称开箱复现**，只有脚本、`data/`、`references/` 与 `SKILL.md` 的接口行为可以直接阅读。

## 数据与许可边界

- **不分发原始行情数据。** 本仓库不含 PandaData 分钟行情、NPZ/NPY 派生数组、数据库文件、账户或委托成交回执；这些都需要使用者自备数据授权，`.gitignore` 已按类型排除。
- **技能调用不取数。** 所有技能出口恒为 `execution_action=NONE`，不交易、不连接账户；技能只读调用者已提供的历史 NPZ，不会自行取数或补数。
- **仓库内含取数工具，但默认不运行。** [`scripts/fetch_min.mjs`](scripts/fetch_min.mjs) 与 [`scripts/make_minute_tasks.py`](scripts/make_minute_tasks.py) 是研究阶段使用的 PandaData 分钟取数入口。它们只在你显式执行、且你自己已具备该供应商授权与本地 headless client 配置时才会发出请求；本仓库不附带凭据、不预置配置，也不会自动调用。
- **许可证只覆盖技能包。** [`delivery/futures-execution-decision/LICENSE`](delivery/futures-execution-decision/LICENSE) 是该技能包自身原有的 GPLv3 文本，本次分发未改动。仓库根目录不另设整体许可，研究文档、结果表与脚本的使用条款由权利方另行决定。
- **Python 依赖**见 [`requirements.txt`](requirements.txt)：核心只用 NumPy。
