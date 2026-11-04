# 行为验证记录

运行：`env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`
结果（2026-11-04）：**111 项全部通过（OK）**，0 failures / 0 errors：`tests/test_decision.py` 17 项、`tests/test_exante.py` 36 项、`tests/test_volume_forecast.py` 9 项、`tests/test_contract_fixes.py` 14 项、`tests/test_preference_plan.py` 35 项。日志中的 `ResourceWarning: unclosed file` 来自 `decision.py` 既有的 `open()` 写法，属长期告警，不影响结论。

覆盖项：

| 用例 | 验证内容 |
|---|---|
| test_missing_risk | 缺 `constraints.risk_sd_budget_bp` → INPUT_REQUIRED |
| test_frac_qty / test_bad_side / test_bool_D | 3.7 手、side=short、D=True 均被拒（D 的布尔显式拒绝） |
| test_nan_hist | 历史行含 NaN → INSUFFICIENT_DATA |
| test_future_perturbation | 追加 20260803–05 三行并只污染这些行，输出与原文件完全一致 |
| test_new_q_recompute | 改 Q 后候选表确实重算（非查表） |
| test_overcapacity | Q=200000 且 a=null → MODEL_INFEASIBLE |
| test_a_bounds | a=null 末段上界为 inf；[0,2] 不越界；[0.5,0.5] 为点区间 |
| test_budget_changes | 风险预算 1bp 与 200bp 的可行集数量不同 |
| test_b_direction | buy/sell 在 b=−50 下状态正确 |
| test_baseline_missing_ctx | mode=baseline_first 且缺 context → INSUFFICIENT_BASELINE |
| test_baseline_compare_keeps | mode=compare 不返回 REFERENCE_BASELINE |
| test_baseline_out_of_scope | Q=1187（不符研究档 1186）→ INSUFFICIENT_BASELINE |
| test_reference_choice_matches_arm | a=0 / k=2 / 预算 10.8444：status=REFERENCE_BASELINE，choice 等于 baseline.arm，条件最优为 asap0 |
| test_baseline_future_training | 释放日 20251202（早于训练终点）→ INSUFFICIENT_BASELINE 且理由指向训练期 |
| test_cli_relocated | 请求放进复制包的 examples、history 用相对路径；断言 status 为 CONDITIONAL_MODEL_CHOICE/REFERENCE_BASELINE、choice 非空、20 个历史日，且输出与原包逐字段相等 |

环境：仅使用 NumPy 与标准库；未取数、未交易、未改平台。

## 事前模式（tests/test_exante.py，36 项）

| 用例组 | 验证内容 |
|---|---|
| 运行与语义 | `mode=exante` 真实运行规则；`execution_action=NONE`、`production_eligibility=INSUFFICIENT`；`capability.rule_enabled=false`（证据 FAIL）；候选单独字段 |
| 未来不变 | 释放后分钟改成 1e9 或 NaN，`status/choice/state/research_candidate/reference` 逐字段不变（合成数据 + 真实 RB 数据） |
| 前缀有效性 | 释放前某一分钟 close=NaN（mask 仍 True）或 volume<0 → `INSUFFICIENT_DATA`，无 state、choice 为空 |
| 身份匹配 | 品种 I 配 `IF2609.CFE`、品种 P 配 `PP2601.DCE` → `INPUT_REQUIRED`（不做字符串前缀匹配） |
| 会话标签 | am 数据配 pm 窗口、IF 配 night 窗口 → `INPUT_REQUIRED` |
| 规模与日期 | `size_idx=0.5` → `INPUT_REQUIRED`；20260231 → `INPUT_REQUIRED`；奇数手数不匹配规模档 → `INPUT_REQUIRED` |
| 状态优先级 | 全不可行 → `MODEL_INFEASIBLE`（即使 A 缺失）；A 缺失但仍有可行候选 → `INSUFFICIENT_BASELINE`；A 不可行且有候选 → `REVIEW_REQUIRED`；A 可行 → `REFERENCE_BASELINE`，均 `choice` 语义正确 |
| 预算与门槛 | 预算 1bp 不被冻结预算覆盖；完成门槛 1.0 不被放宽；b=20 不被忽略；非零残余提示 — 均 `INPUT_REQUIRED` |
| 并列顺序 | ASAP → 更短 h → 同 h 时 TWAP 先于 shape；风险超预算的臂被排除 |

技能示例：`examples/request_exante.json` + `examples/RB_am_20d_20260701_prefix.npz`，从复制到其他目录的包也能运行（相对路径）。

## 只读预测模式（tests/test_volume_forecast.py，6 项）

| 用例 | 验证内容 |
|---|---|
| 预测对账 | RB am 20260701 的 `act_pre=1.294895`、`s=1.154318`、`B60=106858.6`、`pred60=B60·s` 与 Stage 1 固定案例一致；`status=FORECAST_ONLY`、`choice=null`、`NONE`、`production_eligibility=INSUFFICIENT` |
| 期限与缩放容量 | 同输入下 D=5/D=60 的 `hint_horizon_trading_minutes` 分别 5/60，60 分钟总量预测不变；用原始 NPZ 的过去 20 天分钟量与 ASAP 满额恒等式独立推导：D=5 为 **0.60**、D=60 为 **1.00**；未乘 s 的容量给出 0.45（作为区分性反例）；响应与独立值一致（s=1.154318≠1） |
| 容量范围 | `cap_frac=0.05` 时提示为 `null`，`hint_note` 说明超出 10% 提示范围，不静默替代 |
| 未来不变 | 释放后量价改成 1e9/NaN，`forecast` 等字段逐字段不变 |
| 前缀与历史 | prefix 缺失、prefix NaN（mask 仍 True）、**仅前 20 天同前缀量为 0（释放后正常）** → `INSUFFICIENT_DATA`，不补 `act_pre=1` |
| 范围与标签 | P/PP、I/IF、未知品种、IF 夜盘、窗口与标签不匹配 → `INPUT_REQUIRED` |
| 训练期日期 | 20260430 → `INSUFFICIENT_EVIDENCE` |
| ACT 选型拒绝 | `mode=exante_activity` → `INSUFFICIENT_EVIDENCE`、`choice=null`、`NONE` |

## 2026-10-30 契约修订的验证（tests/test_contract_fixes.py，14 项）

| 用例 | 验证内容 |
|---|---|
| `REVIEW_REQUIRED` | 状态为复核时 `choice=null`，条件最优保留在 `conditional_choice`，并带 `next_action` |
| `INSUFFICIENT_BASELINE` | 基准不可用时 `choice=null`、`conditional_choice` 不丢 |
| `REFERENCE_BASELINE` | 参考状态仍返回 `choice = baseline.arm`，条件最优另存 |
| `compare` | 情景比较仍返回条件选择（`CONDITIONAL_*`），不被误当参考 |
| 研究级出口 | 缺 D、缺风险预算、历史缺失、模型不可行四条错误/不可行出口都带 `execution_action=NONE`、`production_eligibility=INSUFFICIENT`、`scope` |
| 完成率语义 | 存在「完成率 ≥ 门槛但残余均值 > 0」的可行臂，证明门槛是路径比例而非零残余；`max_unfilled_lots=0` 下 16/20 路径零残余即 0.8 |
| 实际生效约束 | `effective_constraints` 与 `risk_model` 在正常输出中存在；省略可选约束时仍显示默认值，而 `constraints` 保持原样回显 |
| 降级完整性 | 被降级状态的 `method`/`h` 同时移到 `conditional_method`/`conditional_h`，顶层不再残留 |
| 下一步语义 | 不可行出口的 `next_action` 要求保持母单不变，不出现"放宽"指令 |

## 偏好入口与条件集合等待例外（tests/test_preference_plan.py，35 项，2026-11-04）

结构化偏好在 `mode=compare`/`baseline_first` 下复用核心选择，`cost_first` 按原条件首选、`wait_first` 按 `wait_min` 精确排序（同值 ASAP → 更短 h → TWAP 先于按量分批）；基准优先、缺输入、基准不可用、`MODEL_INFEASIBLE`、`REVIEW_REQUIRED`、`unsupported_mode` 一律在调用前停下，`api_attempts=0`、`jev_called=false`。自由文本只在 `--use-jev` 且有既有授权时发一次请求，且只判一个标签。

唯一新增行为（`ConditionalSetWaitTests`，8 项）：`mode=compare` + 有效结构化 `wait_first` + `status=CONDITIONAL_SET` + 有合法候选时给出 `plan`，`planned cost` 为 `null`；`status`/`choice`/`a`/`effective_constraints` 与门槛均不变。`cost_first`、未明确、自由文本无传输、`baseline_first`、`a` 定值五类情形行为不变（`test_unfixed_a_baseline_first_stays_bounded`、`test_unfixed_a_wait_first_only_in_compare_mode` 守住模式边界）。

CLI 回放（10 条，均 `exit_code=0`、零 Jev、`execution_action=NONE`，请求与响应归档在 `output/futures-execution-regimes-20261021/plan_recovery_20261104/cases/`）：旧 7 条 `status`/`choice`/`method`/`h`/候选指标/生效约束逐字段不变；08（`wait_first`）与 09（`wait_first` + `a=[0,2]`）得 `plan=asap/h0`、成本 `null`；10（06 仅改 `mode=compare`）得 `CONDITIONAL_MODEL_CHOICE`/`asap0`/5.1884bp。旧已装 `fa49d2fe` 对 08 仍无计划，差异来自本轮改动。
