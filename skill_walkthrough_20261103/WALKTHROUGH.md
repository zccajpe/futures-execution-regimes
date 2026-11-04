# 期货执行节奏建议：一次完整调用演示

演示对象是已安装版 `futures-execution-decision`（版本 `fa49d2fe1bee2cc0b62a612729c5ab0875532be7`）。这是研究级的条件计划，不是下单器：输出里的 `execution_action` 恒为 `NONE`，`production_eligibility` 恒为 `INSUFFICIENT`。

先把三个容易混的东西分开：

- **D**（`parent.Dmax_trading_minutes`）：业务方给的最晚完成期限，交易分钟数。
- **h**（`plan.h`）：模型建议的计划释放时长，只是「打算在多久内把单子放出去」。
- **wait_min**：等待负担，按手数加权的模型等待量，残余部分按到第 60 分钟计。

D 是业务要求，h 是释放目标，wait_min 是模型算出来的负担。三者不是一回事，h 也**不保证**在 h 内成交完。

## 1. 调用者要给什么

必填的业务输入（都由业务方决定，skill 不会替你猜）：

| 字段 | 谁定 | 说明 |
|---|---|---|
| `parent.symbol / side / qty_lots` | 业务方 | 品种、方向、整数手。symbol 是品种级研究输入（RB/CU/SC），不编造交割合约月份；订单是构造母单，不是真实成交 |
| `parent.release_trading_date / release_label` | 业务方 | 释放日与释放分钟（须在数据网格内） |
| `parent.Dmax_trading_minutes` | 业务方 | 业务期限。本入口当前覆盖 1–60 交易分钟，这是实现范围 |
| `constraints.min_completion_probability / max_unfilled_lots` | 业务方 | 完成门槛与允许残余手数，缺省 0.95 / 0 |
| `constraints.risk_sd_budget_bp` | 业务方 | **必填**。价格项标准差预算，不是最大亏损金额 |
| `scenario.*` | 业务方声明 | 容量比例、费用价差、冲击系数、冲击指数、方向预期。这些是**要声明沿用的研究假设**，不是实测值 |
| `preference.preference` | 业务方 | `cost_first` / `wait_first` / `unspecified`，用结构化字段，不走自由文本 |
| `history_npz` | 平台提供 | 释放前分钟历史。已有 PandaData 分钟缓存，对应 NPZ 已生成，不需要现场取数 |

## 2. 一个完整请求

下面用 CU 602 手这一条展示完整输入，可以原样复制存档：

```json
{
  "parent": {
    "symbol": "CU",
    "side": "buy",
    "qty_lots": 602,
    "release_trading_date": "20260506",
    "release_label": "09:15",
    "Dmax_trading_minutes": 60
  },
  "history_npz": "data/history/CU_am_20260506_s2_D60.npz",
  "constraints": {
    "min_completion_probability": 0.95,
    "max_unfilled_lots": 0,
    "risk_sd_budget_bp": 17.5059
  },
  "scenario": {
    "cap_frac": 0.1,
    "fee_spread_bp": 1.3,
    "impact_a_bp": 0.5,
    "phi": 1.0,
    "terminal_price_change_bp": 0.0
  },
  "mode": "compare",
  "preference": {
    "preference": "cost_first"
  }
}
```

读法：业务方要买 CU 602 手，释放后 60 交易分钟内必须做完，允许残余 0 手，价格项 SD 不超过 17.5059bp，偏好成本优先；每分钟成交容量按该分钟市场成交量的 10%、费用价差 1.3bp、冲击系数 a=0.5、方向预期 0。`mode` 用 `compare` 表示只做条件比较，不挂冻结基准。

## 3. 一条调用命令

```bash
/opt/homebrew/opt/python@3.13/bin/python3.13 \
  delivery/futures-execution-decision/scripts/preference_plan.py \
  --request skill_walkthrough_20261103/requests/_req_03_CU602_compare_cost_first.json \
  --out     skill_walkthrough_20261103/responses/03_CU602_compare_cost_first.json
```

- `--request`：读哪个请求 JSON。
- `--out`：把完整响应写到哪个文件。

本轮 7 次实际调用都没带 `--use-jev`，全部退出码 0，7 条输出里 `api_attempts=0`、`jev_called=false`、`execution_action=NONE`。原始输入在 `requests/`（`_req_` 前缀），原始输出在 `responses/`（同名去掉 `_req_`），summary 不替代原始输出。

## 4. 每个实际结果及原因

| 案例 | status | choice | plan | 关键数字与一句话原因 |
|---|---|---|---|---|
| 01 RB892 · 基准优先 | `REFERENCE_BASELINE` | `asap0` | asap / h0 | 成本 5.89796bp，等待 4.13918 分，完成 0.95，风险 4.62338bp，残余均值 1.8 手。保留训练截止 20260430 的冻结基准 A，**不是**说它今天成本最优 |
| 02 同一单改 `compare` | `CONDITIONAL_MODEL_CHOICE` | `twap5` | twap / h5 | 成本 5.45016，等待 4.58105，完成 0.95，风险 5.28542，残余均值 1.8。比 ASAP 低 0.4478bp 模型成本，代价是多等 0.44187 分钟、风险抬升（仍低于 5.3435 预算）。本次历史回放中，两者均有 19/20 条路径零残余 |
| 03 CU602 · 成本优先 | `CONDITIONAL_MODEL_CHOICE` | `shape30` | shape / h30 | 成本 5.376365，完成 1.0，等待 17.31802，风险 15.76736，残余 0。同组 ASAP 成本 6.16274 / 等待 13.52417 / 风险 14.08709；TWAP30 成本 5.394625 / 等待 17.2814 / 风险 15.74728。按历史量曲线分配的冲击负担更低，只比 TWAP30 低 0.01826bp，没有置信区间，**不能**说有稳健优势 |
| 04 同一单改 `wait_first` | `CONDITIONAL_MODEL_CHOICE` | 仍是 `shape30` | asap / h0 | 按偏好在合法集合里取最小 wait_min：比 03 少 3.79385 分钟模型等待，多 0.786375bp 模型成本，预算与完成门槛不变。**choice 与 plan 不一致，调用者要先读 status 再读 plan，不能只读 choice** |
| 05 RB3569 · 缺期限 | `INPUT_REQUIRED` | null | null | 当前首先缺少业务期限 |
| 06 SC14 · 无冻结基准 | `INSUFFICIENT_BASELINE` | null | null | `baseline_reason`：frozen A had no feasible arm on TRAIN for this cell |
| 07 CU602 · a 未定 | `CONDITIONAL_SET` | null | null | 只给条件集合，不给点选择 |

关于 02 的「为什么是 TWAP」：D=15 且完成门槛 0.95 把可选集合压得很窄，只有 `asap0`、`twap5`、`shape5` 三条通过；`twap10` 完成率掉到 0.90 被门槛排除，`shape10` 风险 5.90245 超预算被排除。三条里 ASAP 冲击负担 9.20116 最高，`twap5` 8.30556 最低。把 mode 从 01 的 `baseline_first` 换成 `compare` 只是**模式语义对照**，不是新市场样本，也不是新增经济证据。

关于 04 的 h=0：ASAP 是「立即释放全部目标」，但每分钟仍受 10% 成交量容量限制，h=0 **不等于**瞬间全部成交。

## 5. 三种不生成计划的原因

- **05 缺期限**（`INPUT_REQUIRED`）：这是输入缺项，业务方必须给 D。skill 不猜、不自动填 25 分钟；当前实现只覆盖 1–60 交易分钟。
- **06 冻结基准不可用**（`INSUFFICIENT_BASELINE`）：这不是缺字段，是训练期里该单元没有可行的基准 A。所以不能补造 A，也不能把 `conditional_choice=asap0` 拿来顶替冻结基准。业务方若明确只要条件比较，可以另开 `compare`；本例没有替他切换。
- **07 冲击系数未定**（`CONDITIONAL_SET`）：`impact_a_bp` 给 null 时只有条件集合。不填 0.5，不凭感觉选点，也不放宽门槛。要让结果变成一个点，需要补 a 的依据。

三者共同的输出形态是：`plan.method` 与 `plan.h` 都为 `null`，而原有 `status` 与 `choice` 原样保留——降级不等于改写。

## 6. 已知限制

- **三个时间量不同**：D 是业务完成期限；h 是计划释放时长，不保证 h 内做完；wait_min 是按手数加权的等待负担。
- **估值终点**：执行只到 D，完成与残余在 D 末计算；残余部分的价格风险和等待估值用释放后 60 分钟终点。
- **完成率不是概率**：它是释放前 20 条历史路径中残余不超上限的比例（门槛 0.95 + 残余 0 即至少 19/20 条零残余），不是校准过的未来成交概率；单次调用 20 条路径，不能当成 20 个独立生产试验。
- **风险不是亏损**：`risk_bp` 是价格项标准差，不折算金额，不等于最大亏损。
- **成本是模型值**：依赖费用价差 1.3bp、冲击 a=0.5、容量 10% 等声明假设，按量方案按历史成交量曲线分配，不是实时盘口或真实冲击估计。
- **这轮证明什么**：只展示「输入条件 → 合法模型计划 / 不生成计划」的接口行为。没有验证未来经济最优、收益或执行优势，因此不打 Q/V 评分，也不能据此声称生产可用。
