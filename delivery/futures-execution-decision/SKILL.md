---
name: futures-execution-decision
description: 给定构造的期货母单与释放前分钟历史，在调用者指定的完成门槛、风险预算与冲击情景下，比较尽快执行、平均分批、按历史成交量分批的合法计划时长，输出条件性建议与中文理由；只读研究结论，不下单、不连接交易接口。
---

# 期货执行节奏建议（研究级，不下单）

> **分发说明：** 本包不含任何行情数据。`examples/` 与 `tests/fixtures/` 指向的 release 前 20 交易日分钟历史 NPZ（`RB_am_20d.npz`、`RB_am_20d_20260701_prefix.npz`、`RB_am_20d_20260803.npz`）需使用者在自己已授权的环境中按 `../../../DATA_PREPARATION.md` 的同名路径准备；缺这些文件时请求返回 `INSUFFICIENT_DATA` 并说明缺项。算法、阈值与数值内核不因分发做任何改动；无授权数据时请以 `references/contract.md` 的接口语义为准，不要宣称历史结论可复现。

给定一笔构造的期货母单与释放前历史分钟数据，本技能在调用者给定的完成门槛、风险预算与冲击情景下，比较「尽快执行 / 平均分批 / 按历史成交量分批」的合法计划时长，给出**条件性**建议与中文理由。

- `execution_action` 恒为 `NONE`，`production_eligibility` 恒为 `INSUFFICIENT`；不连接任何交易接口。
- 结论是给定假设内的**条件模型选择**，不是已验证优于基准的策略，也不是经济最优。
- 冻结基准 A 见 `data/frozen_A_params.csv`（训练截止 2026-04-30），按 `baseline_context` 查询。

主命令：

```bash
python3 scripts/preference_plan.py --request tests/fixtures/compare_cost_first.json --out /tmp/plan.json
```

`decision.py` 仍可单独调用（`--request examples/request.json --out /tmp/result.json`）；`examples/` 下另有 `request_exante.json`、`request_volume_forecast.json` 两个模式示例。全部用相对路径，整个包搬到别处仍可运行。

## 一次调用返回什么

```json
{"status": "CONDITIONAL_MODEL_CHOICE", "choice": "shape60",
 "plan": {"source": "deterministic_rank", "method": "shape", "h": 60, "arm": "shape60",
          "predicted_cost_bp": 2.3362, "wait_min": 28.92, "completion": 1.0, "risk_bp": 14.69},
 "preference_routing": {"preference": "cost_first", "origin": "caller_structured",
                        "jev_called": false, "api_attempts": 0}}
```

**原 `choice` 与新 `plan` 不是一件事。** `choice` 是 `decision.py` 原有的条件最优，语义不变；`plan` 是按偏好给出的方法与计划时长，只新增、不覆盖 `status`/`choice`/`method`/`h`。两者不一致时按 `status` 一起读，不能把 `plan` 当成改了原推荐。

请求里可加偏好：`preference.preference` 之一 —— `cost_first`（成本优先）、`wait_first`（减少模型等待负担优先）、`unspecified`（未明确）；或 `business_note` 一句自由文本业务说明。`wait_first` 只在同一合法集合内最小化既有 `wait_min`，是「等待负担最小」，**不是**实际最快做完。

## 先备材料，再调用

1. 业务只要「这笔母单在给定约束下怎么排」→ 选 `mode=compare`；要「保持或挑战冻结基准 A」→ 才用 `mode=baseline_first`。本技能不改调用者已写的 `mode`。
2. 复用能核对的现成分钟 NPZ 与匹配上下文，不重复取数。历史口径：**释放日之前最近 20 个交易日，每个交易日取与本次释放标签对应的后续 60 交易分钟参考窗口**；这些全是历史数据。`baseline_first` / `compare` 不要求释放日当天的行情，只有 `exante` / `volume_forecast` 另需当天释放前缀（且不在本入口范围）。路径缺或覆盖不足时，回报缺哪一项（品种/窗口/交易日/分钟数），不要用别的品种或别的窗口顶替。
3. 关键业务字段必须明确，不能猜：母单（`symbol`、`side`、`qty_lots`）、`release_trading_date`、`release_label`、`Dmax_trading_minutes`、`history_npz`、`constraints.risk_sd_budget_bp`。其余是既有可选参数，未给时沿用已声明默认值并在 `effective_constraints` 回显来源：`min_completion_probability`=0.95、`max_unfilled_lots`=0（可选）、`cap_frac`=0.10、`phi`=1.0、`fee_spread_bp`=1.3、`terminal_price_change_bp`=0.0；这些默认值属于要声明沿用的假设，不用另猜。偏好也可以不给（沿用原模式计划）。`impact_a_bp` 给数值→成本点估计可用；给 `null` 或区间→成本比较只有条件集合，但等待目标（`wait_first`）例外见下文。
4. 本小节只说明材料准备，技能不做自动查找或自动取数。

## 边界（必须一起读）

- 默认不联网；未启用 Jev 时自由文本不产生新计划，结构化偏好按确定性规则处理。只有显式加 `--use-jev`、且运行方用**既有授权**把 `TYPESAFE_API_KEY` 注入环境变量时，才允许为一次自由文本偏好发出一次请求（不重试）。不要把 key 发给本技能、不要写进请求文件或产物；没有该环境变量就按「偏好未确立」处理。
- 结构化偏好 → 0 次 Jev。只有自由文本才送 Jev，且只判一个标签；不送候选表、数量、D、预算、冲击或价格。
- 偏好请求了但没确立（标签越界、语义未明确、自相冲突、网络失败、无传输、非法枚举值），或显式 `unspecified` → `plan` 不给方法与 h，原 `status`/`choice` 保留。**不猜偏好、不回退默认。** 只有完全没带新增偏好或说明时才沿用原模式计划。
- 输入校验失败、`INSUFFICIENT_BASELINE`、`MODEL_INFEASIBLE`、`REVIEW_REQUIRED`、`CONDITIONAL_SET` 一律在没有合法点选择时停下，0 次 Jev；`exante`/`exante_activity`/`volume_forecast` 不在本入口范围。其中 `CONDITIONAL_SET` 只表示**成本比较**缺定值 `a`、成本仍未定；等待目标例外见下一条。
- 唯一例外（a 未定仍可给等待计划）：**显式 `mode=compare` + 有效结构化 `wait_first` + `status=CONDITIONAL_SET` + 有通过既有数值检查的合法候选**时，按下面同一套等待排序给出 `plan`，`predicted_cost_bp` 为 `null`（不填 0、不填 0.5、不给成本区间）。此时原 `status=CONDITIONAL_SET` 与 `choice=null` 原样保留；计划只在等待负担口径下成立，成本未知。`cost_first`、无新增偏好、`unspecified`、无效偏好、只有自由文本，以及基准优先与 a 定值情形，都不享有此例外，行为不变。
- 合法候选要求生效约束齐全且为数值、方法与 h 在既有网格内且 `h<=D`、风险/完成/费用有限。缺检查依据即无计划。合法集合、`completion`、`risk_bp`、`wait_min` 都不依赖 `a`；只有成本点估计依赖 `a`。
- 成本优先复用原条件首选，不从舍入字段重排；等待优先按既有 `wait_min` 精确排序，同值 ASAP → 更短 h → TWAP 先于按量分批。
- Jev 响应按信任边界校验：完整 `choice`、标签在枚举内、概率覆盖三个标签且和为 1（仅容 1e-6 浮点误差）、`choice` 与最大概率项一致；不合规即结构化失败，不重试。HTTP 用标准库且拒绝跟随重定向。
- `D`（`parent.Dmax_trading_minutes`）只由调用者给，任何模式都不改；`h` 是计划释放时长，**不保证在 h 内完成**；`completion` 是 20 条历史路径中残余不超上限的比例，不是校准过的成交概率。
- 两种风险口径不可互换：`empirical_sd_20_paths`（baseline/compare）与 `train_cell_shrunk_sd`（exante）。风险是价格项 SD，未折算金额。

字段与各模式契约见 `references/contract.md`；证据与已知失败见 `references/evidence.md`、`references/volume-evidence.md`。

测试：`python3 -m unittest discover -s tests -v`（111 项：决策 17 + 事前 36 + 预测 9 + 契约 14 + 偏好入口 35）。
