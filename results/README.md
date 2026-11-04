# results 目录：当前有效结果（2026-10-21 第三轮）

| 文件 | 内容 |
|---|---|
| `selector_decisions_val.csv` | 逐母单决策 213,192 行（7 个预声明配置），含不可行原因、三条基线结果、路径改变标记、Q 复核 |
| `fixed_h_train.csv` | 训练期每个 h 的聚合成本/完成/价格差标准差/预算/是否选中/不可行原因 |
| `paired_selector_v3.csv` | 选择器与三基线的配对差，连续 5 日块 bootstrap 95% 区间，按 side/a/φ/偏好/规模/D 分组 |
| `deadline_distribution.csv` | 逐母单四个期限的情景完成率与最短可行 D（train/val/warmup 分开） |
| `state_within.csv` | 每个（品种, 窗口）内部状态桶的成本/完成/风险 |
| `sensitivity.csv` | 费率、价差、冲击、容量的敏感性 |
| `proxy_compare.csv` | close 与有效 VWAP 代理的同案例比较（TA 无有效均价，未纳入） |
| `pareto_t60.csv` | 成本/完成/风险/用时的非支配组合（T=60 结算） |
| `expectation_model_v2.json` | 三个因果特征 + 固定 λ 岭回归的方向与误差 |
| `focus_checks.json`、`verification_v4.json` | 期限/状态/扰动/Q 一致/冲击线性/路径改变等核验 |
| `replay_train_val_t60.npz` | 主回放 + 共同 T=60 残余项 |
| `scenarios_train_val.npz` | matched scenario 矩阵 |
| `replay_train_val_vwaq_t60.npz` | VWAP 代理敏感性回放（11 品种） |

**已失效**：`frontier_summary.csv`、`pairs_key.csv`、`state_summary.csv`、`expectation_summary.csv`、`flip_thresholds.csv`、`pareto.csv`、`selector_eval.csv`、`selector_summary.json`、`analysis_coverage.json`（旧选择器/旧状态/旧均价口径）。


## 留出期（2026-10-21 第四轮）

| 文件 | 内容 |
|---|---|
| `selector_decisions_test.csv` | 留出期逐母单决策（407,232 行，冻结训练参数） |
| `frozen_params_used_test.csv` | 留出期实际使用的 h/预算/原因（与训练表逐组一致） |
| `replay_test_t60.npz`、`scenarios_test.npz` | 留出期回放与情景矩阵 |
| `pareto_final.csv` | 逐 (split,品种,窗口,规模,方向,D,方法,h) 的非支配点 |
| `sensitivity_final.csv` | 费用/价差/冲击对同一母单配对差的影响（容量固定 10%） |
| `state_full.csv` | 全 D、三档规模、全部合法 h 的分组表 |
| `fourway_metrics.csv` | 留出期四方同子集指标（成本/完成/R/风险/用时/markout） |

`fixed_h_train_frozen.csv` 与早前未修 bug 时的留出决策表已作废。

## 本轮新增（2026-10-21 第五轮）

| 文件 | 内容 |
|---|---|
| `fourway_metrics.csv` | val+test 同子集四方指标，含 subset、覆盖、n_dates、n_blocks、bootstrap 区间 |
| `pareto_wait.csv` | 逐 D 非支配点，时间用等待负担 `D·L_exec + 60·L_res`（交易分钟） |
| `expectation_table.csv` | 共同 b（T60 预期价格变化）下的成本/完成/风险 |
| `proxy_flip.csv` | close 与有效 VWAP 的同案例配对差与排序是否翻转（3,436 对，未翻转） |
| `state_cases.csv` | 每条母单的逐日事前状态与分桶 |
| `case_examples.json` | **历史**（旧 4% 规模案例，字段 risk 为单笔价格差）；已由 `case_methods.csv` 取代，保留不删 |

## 逐品种状态与多目标（2026-10-21 第六轮）

| 文件 | 内容 |
|---|---|
| `state_by_product.csv` | 逐品种状态表（多列键重建，含 note 列标注描述性格），保留品种/时段，覆盖三规模×四期限×全部合法 h，含流动性/波动/活跃度/首段方向四维状态、独立日期数、成本、完成、残余比、风险、等待负担 |
| `multiobjective.csv` | 同子集多目标：成本差与风险差（逐块重算两个样本 SD 后相减，5 日块 2,000 次区间）、完成率、完成用时 |
| `expectation_full.csv` | 全部合法 arm × 三规模 × 四期限 × 三个预期情形的成本/完成/风险 |
| `ab_flip.csv` | 相对尽快执行的冲击/预期翻转阈值；只含 buy、φ=1、D=30/60，基准差已含 1.3×费用载荷差 |
| `proxy_case_flip.csv` | 价格项符号的逐案例代理翻转比例，仅 10% 容量、1% 规模、D=60；2.2%–3.5%，池化均值不翻转 |
| `state_full.csv` | 旧池化表，仅作对照；逐品种结论一律以 `state_by_product.csv` 为准 |
| `state_verify.json` | 10 个随机分组的直接重算校验 |

已知局限：状态格内独立日期数不足 30 的，只描述不结论。

## 全候选比较（2026-10-21 最终）

| 文件 | 内容 |
|---|---|
| `all_candidates_center.csv` | 12 行：同一 2,424 笔留出期母单（买、1%、D=60）上 ASAP、TWAP 5/10/20/30/60、SHAPE 5/10/20/30/60 与逐日自适应（h 留空，每日不同）；列成本、价格项标准差、完成率、完成用时、R/Q 95 分位与均值、等待负担 |
| `all_candidates_vs_dynamic.csv` | 自适应相对各既定候选的配对成本差与标准差差，5 日块 2,000 次 95% 区间 |

`paired_metrics_complete.csv` 与 `case_methods.csv` 由 `scripts/build_delivery_tables.py` 生成；本轮不新增实验。
