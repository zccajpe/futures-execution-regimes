# 请求、历史数据与返回值

常用入口为 `scripts/preference_plan.py --request REQUEST.json --out PLAN.json`。请求文件和输出目录由调用者提供。脚本在终端打印摘要，将候选方案、理由与缺项写入完整 JSON。[可复制的请求](../examples/request.json)

## 填写母单

| 请求字段 | 填写方法 |
|---|---|
| `parent.symbol`、`side`、`qty_lots` | 品种或标识、`buy` 或 `sell`、正整数手数。历史文件由调用者按相同品种和交易时段选择。 |
| `parent.release_trading_date`、`release_label` | 释放日用 `YYYYMMDD`，释放分钟用网格中的 `HH:MM`。 |
| `parent.Dmax_trading_minutes` | 最晚完成期限 D，取 1 至 60 的整数，单位为交易分钟。 |
| `history_npz` | 分钟历史 NPZ 的绝对路径，或相对于这份请求文件的路径。 |
| `constraints.risk_sd_budget_bp` | 必填，非负。表示可接受的价格项标准差，单位 bp。 |
| `constraints.min_completion_probability`、`max_unfilled_lots` | 默认 0.95、0 手。完成率按 20 条历史路径中残余不超过上限的比例计算。 |
| `mode` | `compare` 做条件比较。`baseline_first` 查冻结参考 A，也是默认模式。 |
| `preference.preference` | `cost_first` 优先成本，`wait_first` 优先减少等待负担。省略整个偏好时沿用原模式计划。显式填 `unspecified` 或非法偏好时，偏好计划为空。 |

情景放在 `scenario` 对象里。`cap_frac` 是每分钟最多占用的成交量比例，默认 0.10。`fee_spread_bp` 合并费用与价差，默认 1.3 bp。`phi` 是冲击指数，可取 1 或 0.5，默认 1。`terminal_price_change_bp` 是调用者预期的 60 分钟价格变化，默认 0。用 `effective_constraints` 核对本次计算采用的设置。

`impact_a_bp` 是参与率为 1% 时假设的冲击系数，单位 bp。给非负数值可比较成本点估计。省略、填 `null` 或给有序非负区间 `[lo, hi]` 时返回不同冲击假设下的比较。使用真实成本判断时，需另行标定该系数。

## 准备自己的分钟历史

`compare` 和 `baseline_first` 需要释放日前至少 20 条完整历史路径。每条路径包括与本次相同的释放分钟和随后 60 个交易分钟。脚本取最近 20 条符合要求的路径。用有权使用的 PandaData 或同口径数据在本地生成一个含以下键的 NPZ。合成示例展示了实际写法。[生成示例](../examples/make_demo.py)

| 键 | 格式 |
|---|---|
| `dates` | 长度 N，交易日字符串 `YYYYMMDD`，升序且唯一。 |
| `labels` | 长度 M，按交易顺序排列的唯一分钟标签。必须覆盖释放标签和随后的 60 格。 |
| `close`、`volume` | 两个 N×M 数值数组，分别为分钟收盘价和成交手数。历史行使用有限数，成交量非负，有效窗口内价格为正。 |
| `mask` | N×M 布尔数组。有效分钟为 `True`。缺失分钟与真实零成交量分别记录。 |

## 读一个计划

先看 `status`，再看 `plan`。按量调度在输出中叫 `shape`。ASAP 的计划时长 h 为 0。TWAP 和 SHAPE 从 5、10、20、30、60 分钟中取 h≤D 的候选。对于成本优先，脚本沿用通过约束的最低成本候选。对于等待优先，按 `wait_min` 排序，完全并列时依次优先 ASAP、更短 h、TWAP。

| 状态 | 调用者如何使用 |
|---|---|
| `REFERENCE_BASELINE` | A 在当前约束下可行。`choice` 标识 A，正常计划的来源为 `reference_baseline`。 |
| `CONDITIONAL_MODEL_CHOICE` | `compare` 的 `choice` 是给定假设下成本最低的候选。`plan` 反映业务偏好，两者可以不同。 |
| `CONDITIONAL_SET` | 成本取决于 a，查看 `segments`。**仅**显式 `compare` 加结构化 `wait_first` 且存在合法候选时，可以返回等待计划并令 `predicted_cost_bp=null`。 |
| `INPUT_REQUIRED`、`INSUFFICIENT_DATA` | 读取 `detail`、`plan.missing`，补业务字段或分钟历史。 |
| `INSUFFICIENT_BASELINE`、`REVIEW_REQUIRED` | A 缺失、超出冻结范围，或 A 在本单不可行。读取 `baseline_reason`。由调用者决定是否另发一份 `compare` 请求。 |
| `MODEL_INFEASIBLE` | 所有候选均未通过约束。查看各候选的 `excluded`。 |

`plan.h` 是释放目标数量的计划时长。`completion` 是历史路径的完成比例，默认门槛 0.95 对应至少 19/20 条路径合格。`risk_bp` 是价格项的经验标准差。`wait_min` 按手数加权，并将残余计到共同观察时点 60 分钟。相应候选的 `resid_mean`、`resid_p95` 以手计。以实际输出的 `plan.reason`、`plan.missing` 解释选择或空计划。

**冻结 A。** `baseline_context` 需要 `product`、`window`、`size_idx`、`risk_k`。产品要与 `parent.symbol` 匹配。训练截止日为 2026-04-30，A 用于此后的释放日。手数要匹配历史参考量的规模档（0.25%、1%、4% 向下取整），方向、D、风险档和冲击档要能查到同一行。冻结情景为容量 10%、费用 1.3 bp、φ=1、方向项 0。[冻结参数](../data/frozen_A_params.csv)

**自由文本。** 可用 `preference.business_note` 或顶层 `business_note` 表达偏好。只有显式启用 `--use-jev` 且本机环境已有 `TYPESAFE_API_KEY`，通过输入和状态校验后，脚本才将这句话发送给 Jev 并尝试一次标签判断。结构化偏好优先。查看 `preference_routing.origin` 和 `api_attempts`。

## 其他研究模式

`scripts/decision.py` 保留底层入口，同样接受 `--request`、`--out`。`volume_forecast` 返回总量预测及 `FORECAST_ONLY`。`exante` 返回方向与波动状态、研究候选及基准判断。这两个模式另需释放日当天截至释放标签的有效分钟前缀、匹配的品种与时段。商品上午/下午/夜盘标签为 09:15、13:35、21:15，IF/IC/T 上午/下午为 09:45、13:05。`exante` 还要求匹配冻结预算和情景。`exante_activity` 返回 `INSUFFICIENT_EVIDENCE`。偏好入口只为 `compare` 和 `baseline_first` 生成点计划。[成交量预测的适用范围](volume-evidence.md)

## 回归测试

运行完整 111 项原回归需要本地授权的三份 **RB 历史样本**。将它们放在本技能的 `examples/` 中，分别对应原始请求 `RB_am_20d.npz`、含 2026-07-01 当天前缀的 `RB_am_20d_20260701_prefix.npz`、截至 2026-07-31 的 `RB_am_20d_20260803.npz`。历史断言依赖这些固定样本的实际数值。合成演示使用独立的文件名和目录。首次验收可先运行首页的公开演示。

从技能根目录执行 `python3 -m unittest discover -s tests -v`。
