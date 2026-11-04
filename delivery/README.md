# 交付入口（公开副本）

本目录是期货执行节奏研究的对外交付入口。当前状态：**研究级、只读结论**，不连接交易接口。

## 先看这几份

| 内容 | 路径 |
|---|---|
| 仓库导航 | [`../README.md`](../README.md) |
| 既有结果整合报告（经济结果截至 2026-11-03，接口 2026-11-04 更新） | [`../REPORT.md`](../REPORT.md) |
| 数据格式、本地准备与授权边界 | [`../DATA_PREPARATION.md`](../DATA_PREPARATION.md) |
| 结果明细（方法、覆盖、区间） | [`../RESULTS_DETAIL.md`](../RESULTS_DETAIL.md) |

## 技能包

| 内容 | 路径 |
|---|---|
| 技能说明与边界 | [`futures-execution-decision/SKILL.md`](futures-execution-decision/SKILL.md) |
| 输入输出契约 | [`futures-execution-decision/references/contract.md`](futures-execution-decision/references/contract.md) |
| 证据与已知失败 | [`futures-execution-decision/references/evidence.md`](futures-execution-decision/references/evidence.md)、[`volume-evidence.md`](futures-execution-decision/references/volume-evidence.md) |
| 数值内核 | [`futures-execution-decision/scripts/decision.py`](futures-execution-decision/scripts/decision.py)、[`exante_core.py`](futures-execution-decision/scripts/exante_core.py)、[`preference_plan.py`](futures-execution-decision/scripts/preference_plan.py) |
| 冻结基准 A | [`futures-execution-decision/data/frozen_A_params.csv`](futures-execution-decision/data/frozen_A_params.csv) |
| 测试与验证摘要 | [`futures-execution-decision/tests/`](futures-execution-decision/tests/)、[`VALIDATION.md`](futures-execution-decision/VALIDATION.md) |
| 许可 | [`futures-execution-decision/LICENSE`](futures-execution-decision/LICENSE)（该包自身原有的 GPLv3 文本，未改动） |

## 命令（在 `delivery/futures-execution-decision/` 内执行）

```bash
# 偏好入口：方法与计划时长
python3 scripts/preference_plan.py --request tests/fixtures/compare_cost_first.json --out /tmp/plan.json

# 原有入口
python3 scripts/decision.py --request examples/request_compare.json --out /tmp/compare.json
python3 scripts/decision.py --request examples/request_baseline_first.json --out /tmp/baseline.json

# 单元测试：111 项（决策 17 + 事前 36 + 预测 9 + 契约 14 + 偏好入口 35）
python3 -m unittest discover -s tests -v
```

**这 111 项需要授权数据。** `examples/` 与 `tests/fixtures/` 指向 release 前 20 个交易日的分钟历史 NPZ，本仓库不分发。缺文件时请求返回 `INSUFFICIENT_DATA` 并说明缺项，不会换品种或换窗口顶替。准备方法见 [`../DATA_PREPARATION.md`](../DATA_PREPARATION.md)。

## 读输出的三条规则

1. 先看 `status`。`choice` 是 `decision.py` 原有的条件最优，语义不变；按偏好给出的方法与计划时长在 `plan` 里，两者不一致时一起读，不能把 `plan` 当成改了原推荐。
2. `h` 是计划释放时长，**不保证在 `h` 内完成**；`completion` 是 20 条历史路径中残余不超上限的比例，不是校准过的成交概率。
3. `compare` 与 `baseline_first` 是给定假设下的条件选择，不是收益证明；`a` 未标定时成本只有条件集合，`predicted_cost_bp` 为 `null`。

所有出口恒为 `execution_action=NONE`、`production_eligibility=INSUFFICIENT`。

## 案例与验证记录

| 内容 | 路径 |
|---|---|
| 接口行为验证（10 条固定请求回放、111 项测试、旧新版本对照） | [`../plan_recovery_20261104/RESULTS.md`](../plan_recovery_20261104/RESULTS.md) |
| 调用走查 | [`../skill_walkthrough_20261103/WALKTHROUGH.md`](../skill_walkthrough_20261103/WALKTHROUGH.md) |
| 简化记录（含偏好入口与 Jev 边界） | [`../skill_simplification_20261103/RESULTS.md`](../skill_simplification_20261103/RESULTS.md) |
| 证据对照 | [`../architecture_20261103/EVIDENCE_MAP.md`](../architecture_20261103/EVIDENCE_MAP.md) |

## 研究侧

| 内容 | 路径 |
|---|---|
| 成本归因与条件边界 | [`../decision_research/COST_ATTRIBUTION.md`](../decision_research/COST_ATTRIBUTION.md)、[`../decision_research/attribution_by_product.csv`](../decision_research/attribution_by_product.csv) |
| 六配置规则评价 | [`../decision_research/POLICY_FINDINGS.md`](../decision_research/POLICY_FINDINGS.md)、[`../decision_research/v2/evaluate_pairs.json`](../decision_research/v2/evaluate_pairs.json) |
| 全范围条件包络 | [`../decision_research/envelope_all.csv`](../decision_research/envelope_all.csv)、[`../decision_research/envelope_all_nofeasible.csv`](../decision_research/envelope_all_nofeasible.csv) |
| 事前选型（研究候选，未启用） | [`../exante_policy_20261022/EVALUATION.md`](../exante_policy_20261022/EVALUATION.md)、[`futures-execution-decision/data/exante_params.json`](futures-execution-decision/data/exante_params.json) |

重建命令：`python3 ../decision_research/build_envelope_all.py`（需本地中间数据）；六配置配对见 `../decision_research/v2/evaluate.py`（先进入该目录）。
