# 输入输出契约

请求 JSON：

| 字段 | 含义 | 缺失时 |
|---|---|---|
| parent.symbol / side / qty_lots | 品种、方向、整数手 | INPUT_REQUIRED |
| parent.release_trading_date / release_label | 释放日与释放分钟标签（数据网格内） | INPUT_REQUIRED |
| parent.Dmax_trading_minutes | 业务最晚期限（交易分钟） | INPUT_REQUIRED |
| history_npz | 相对请求文件或绝对路径的历史 NPZ（dates/labels/close/volume/mask） | INSUFFICIENT_DATA |
| constraints.min_completion_probability | 完成门槛，默认 0.95（研究设定） | 用默认 |
| constraints.max_unfilled_lots | 允许残余手数，默认 0 | 用默认 |
| constraints.risk_sd_budget_bp | 价格项标准差预算 | **INPUT_REQUIRED** |
| scenario.cap_frac / fee_spread_bp / phi | 容量比例、费用价差、冲击指数 | 默认 0.10 / 1.3 / 1.0 |
| scenario.impact_a_bp | 冲击系数，数值或 null | null → CONDITIONAL_SET |
| scenario.terminal_price_change_bp | 调用者给定的到 T60 方向预期 | 默认 0 |

输出状态：`INPUT_REQUIRED`、`INSUFFICIENT_DATA`、`MODEL_INFEASIBLE`、`CONDITIONAL_MODEL_CHOICE`、`CONDITIONAL_SET`。

计算口径：只用严格早于释放日的最近 20 个完整 60 交易分钟路径；价格按当日释放价归一；Q 由调用者给定，不重算；残余统一观察到第 60 个交易分钟；价格预期默认 0；风险为历史路径价格项的标准差；等待负担含残余持到 60 分钟。

完成率与状态语义（2026-10-30 明确）：

| 项目 | 定义 | 不表示 |
|---|---|---|
| `completion` | `mean(residual <= max_unfilled_lots)`，即 20 条历史路径中满足残余上限的比例 | 每路径零残余、残余均值下限、校准后的成交概率 |
| 门槛 0.95 + 默认 `max_unfilled_lots=0` | 至少 19/20 条路径零残余 | 20/20 全部零残余（不是硬门槛，调用者未要求时不额外加严） |
| `h` | 计划释放时长 | 完成保证；实际完成时刻另算 |
| `choice` | `REFERENCE_BASELINE` 时是冻结参考方法；`compare` + `CONDITIONAL_MODEL_CHOICE` 时是**给定假设下的条件最优**（不是推荐）；其它状态一律 `null` | 把条件最优当成推荐；把 `compare` 的 `choice` 当成基准 |
| 风险 `risk_bp` | 20 条路径价格项的经验 SD（baseline/compare） | 与 `mode=exante` 的收缩估计同一口径 |

所有返回（含错误）带 `execution_action=NONE`、`production_eligibility=INSUFFICIENT`、`scope`；`mode`、`completion_note`、`next_action`（如有）一并回显。

补充字段（2026-10-30）：

| 字段 | 含义 |
|---|---|
| `effective_constraints` | 实际参与计算的字段；可选字段用默认值时也写明默认值。`baseline_first`/`compare` 列出 D、完成门槛、残余上限、风险预算、容量、费用、冲击、方向项与完成率公式；`exante` 只列该模式实际回显的 D、冻结预算、完成门槛、残余上限、方向项、容量与完成率公式；其余情景参数仍看 `scenario` |
| `risk_model` | `empirical_sd_20_paths`（baseline/compare，`n_paths=20` 是参考路径数）或 `train_cell_shrunk_sd`（exante，含 κ 与 `n_days=20` 参考路径数）；两套不可互换。这里的 `n_days` 是当日参与回放的参考路径数，**不是** TRAIN 单元的样本量，输出里也没有给出训练样本量 |
| `conditional_choice` / `conditional_method` / `conditional_h` | 被降级状态（`INSUFFICIENT_*`、`REVIEW_REQUIRED`、`MODEL_INFEASIBLE`、`INPUT_REQUIRED`）下的条件最优；此时顶层不再给出最终的 `choice`/`method`/`h`（缺省或 `null`）。`compare` 不属于降级，仍按上一行返回 |
| `constraints_used_for_execution_admission` | 仅 `volume_forecast`：恒为 `false`，该模式不做准入判断 |
| `next_action` | 下一步需要什么；不会要求改母单，也不会静默放宽约束 |

## 偏好入口（`scripts/preference_plan.py`）

在 `decision.py` 的输出之上新增两个字段，原有 `status`/`choice`/`method`/`h` 不变。

额外请求字段：

| 字段 | 含义 | 缺失/非法时 |
|---|---|---|
| `preference.preference` | `cost_first` / `wait_first` / `unspecified` | 不在枚举内 → 拒绝，非默认偏好 |
| `preference.business_note` 或 `business_note` | 一句自由文本业务说明 | 无传输 → 不发送、无计划 |

`plan`（新）：`source`（`deterministic_rank` / `reference_baseline` / `not_available`）、`preference`、`preference_origin`、`objective`、`method`、`h`、`arm`、`predicted_cost_bp`、`fee`、`drift_bp`、`impact_load`、`wait_min`、`completion`、`risk_bp`、`reason`、`missing`、`note`。
`preference_routing`（新）：`preference`、`origin`、`jev_called`、`api_attempts`、`jev`（`payload` / `ok` / `label` / `reject_reason` / `model` / `usage` / `confidence` / `probabilities`）、`skip_reason`。`origin` 与 `plan.preference_origin` 恒一致。

无计划的来源：`jev_rejected`、`jev_unspecified`、`jev_no_transport`、`caller_invalid`、`not_supported_mode`、`gated`。这些情况下 `plan.method` 与 `plan.h` 均为 `null`，原 `status`/`choice` 原样保留。

`conditional_set` 现在不是无计划来源，分两种：①**显式 `mode=compare` + `origin=caller_structured` + `preference=wait_first` + 有合法候选** → 给 `source=deterministic_rank` 的等待计划，`objective=wait_first`、`tie_rule=TIE_NOTE`、`predicted_cost_bp=null`（不填 0、不填 0.5、不给成本区间），原 `status=CONDITIONAL_SET` 与缺失/`null` 的 `choice` 不变；②其余情形（`cost_first`、无新增偏好、`unspecified`、无效偏好、自由文本、基准优先）仍为 `source=not_available`，`reason` 说明缺 `scenario.impact_a_bp` 定值，并提示「只要固定约束下的调度比较可用 `mode=compare` + 结构化偏好」，不代调用者切换 `mode`。

依据：合法集合、`completion`、`risk_bp`、`wait_min` 都不依赖 `a`，只有成本点估计依赖 `a`。因此第二种情形下的计划只表示「等待负担最小」，**不是**成本最优，也不主张经济最优。a 只给区间时的「区间共同成本最优」不在支持范围内（涉及端点与平局判定），仍只给条件集合。

边界：Jev 只做「自由文本 → 一个标签」，不参与算数、排序或风险判断；`unspecified` 不生成新计划；只有完全没有新增偏好时才沿用原模式计划。

## 只读预测模式（mode=volume_forecast）

| 字段 | 含义 | 缺失/非法时 |
|---|---|---|
| parent{symbol,side,qty_lots,release_trading_date,release_label,Dmax_trading_minutes} | 与其它模式相同的基本校验 | INPUT_REQUIRED / INSUFFICIENT_DATA |
| history_npz | 必须含释放日当天与释放前缀；前 20 个交易日窗口完整 | INSUFFICIENT_DATA |

输出（正常与错误路径一致）：`mode=volume_forecast`、`choice=null`、`execution_action=NONE`、`production_eligibility=INSUFFICIENT`。正常时 `status=FORECAST_ONLY`，字段包括 `forecast{act_pre,n_pre,beta,scale,B60,pred60,forecast_horizon_trading_minutes=60,hint_horizon_trading_minutes=D,model_completion_hint,hint_capacity_frac=0.10,hint_note（提示用乘过 s 的容量 `floor(0.10·V·s)` 与 D 长度的 ASAP 目标，非未调整容量）,curve_head,cum5,cum15,cum30}`、`evidence{stage1,stage2}`、`constraints_echo{values,used_for_execution_admission=false}`、`notes`。

边界：`baseline_context.product/window` 必需（用于范围与固定标签校验）；品种/窗口须在支持列表内；`symbol` 按完整字母前缀匹配；IF/IC/T 无夜盘；释放标签固定；释放日 ≤20260430 → `INSUFFICIENT_EVIDENCE`；前缀或历史同前缀无效、历史同前缀均量为 0 → `INSUFFICIENT_DATA`（不补 1）；非 10% 容量时完成提示为 `null`。不做方法/时长选择，不拒单，不改写调用者约束。

`mode=exante_activity`：一律 `INSUFFICIENT_EVIDENCE`、`choice=null`，附 Stage 2 的覆盖缺口与成本差原因。

## 事前模式（mode=exante）

额外的请求字段：

| 字段 | 含义 | 缺失/非法时 |
|---|---|---|
| baseline_context{product,window,size_idx,risk_k} | 冻结单元；size_idx 必须是整数 0/1/2 | INPUT_REQUIRED |
| constraints.risk_sd_budget_bp | 必须等于该单元冻结预算（容差 5e-5bp，存储精度） | INPUT_REQUIRED（不替换、不放宽） |
| constraints.min_completion_probability | 必须 0.95 | INPUT_REQUIRED |
| constraints.max_unfilled_lots | 必须 0 | INPUT_REQUIRED |
| scenario.terminal_price_change_bp | 必须 0（不接受外部分方向预期） | INPUT_REQUIRED |
| scenario.impact_a_bp | 必须是 0 / 0.5 / 2 的定值 | INPUT_REQUIRED |
| history_npz | 必须包含释放日当天且带释放前缀（释放后可为空/无效）；前 20 个交易日窗口完整 | INSUFFICIENT_DATA |

释放标签固定：商品 am 09:15、pm 13:35、night 21:15；IF/IC/T am 09:45、pm 13:05、无夜盘。品种按完整字母前缀匹配（IF≠I、PP≠P）。state 只用释放前的已完成分钟，且要求每一分钟 mask 有效、close 有限为正、volume 有限非负；释放之后完全不读。

输出状态：`REFERENCE_BASELINE`（A 当日可行，choice=A 臂）、`REVIEW_REQUIRED`（A 不可行但有候选，choice=null）、`MODEL_INFEASIBLE`（全部不可行，choice=null）、`INSUFFICIENT_BASELINE`（A 缺失，choice=null）、`INSUFFICIENT_EVIDENCE`（释放日在训练期内）、`INSUFFICIENT_DATA` / `INPUT_REQUIRED`（数据或输入边界）。规则候选始终放在 `research_candidate`，附带每臂预测风险、可行集与 `capability`。

## 模式与基准状态

- `mode`：默认 `baseline_first`；`compare` 仅作条件比较。
- `baseline_context{product,window,size_idx,risk_k}`：查询冻结基准 A 所需，缺则 `INSUFFICIENT_BASELINE`。
- 冻结范围：训练截止 2026-04-30；释放日必须晚于该日；Q 必须等于 `floor(过去20日同窗口均量 × size档)`。
- 状态：`REFERENCE_BASELINE`（顶层 choice 即 A）、`REVIEW_REQUIRED`、`INSUFFICIENT_BASELINE`、`MODEL_INFEASIBLE`、`CONDITIONAL_MODEL_CHOICE`、`CONDITIONAL_SET`。
