---
name: futures-execution-decision
description: 根据期货母单、释放前分钟历史、完成期限和风险预算，在 ASAP、TWAP、按量分批之间生成研究计划，给出方法、计划时长和理由。
metadata:
  version: "1.0.0"
---

# 期货母单执行计划

处理调用者已经决定买卖的期货母单。读取调用者给定的请求与历史文件，用本技能的 Python 脚本计算，再用中文解释结果。输入输出均为 JSON。

## 调用

1. 确认母单品种、方向、整数手数、释放日和分钟、**最晚完成期限** `parent.Dmax_trading_minutes`、价格风险预算及 `history_npz`。分钟历史必须来自对应品种和交易时段。缺少业务条件时保留缺项。
2. 按原请求执行。单纯比较方法用 `compare`。需要参考训练期冻结方案 A 时用 `baseline_first`，它也是默认模式。成本优先填 `preference.preference=cost_first`，最小等待负担填 `wait_first`。
3. 使用能导入 NumPy 的解释器。以下命令从**已安装技能的根目录**运行。把请求和输出参数替换为调用者工作区的路径。若使用独立虚拟环境，将 `python3` 替换为该环境的 Python。

~~~sh
python3 scripts/preference_plan.py --request /path/to/request.json --out /path/to/plan.json
~~~

4. 先读 `status`，再读 `plan.method`、`plan.h`、`plan.reason`。报告计划时长、`predicted_cost_bp`、`completion`、`risk_bp`，以及采用的冲击假设。若需解释残余手数，在 `candidates` 中按 `plan.arm` 读取 `resid_mean` 和 `resid_p95`。

## 解释选择

- `asap` 立即释放整笔目标，`h=0`。容量仍限制每分钟能成交的手数。`twap` 在 `h` 个交易分钟内等时分批。`shape` 按释放前估计的分钟成交量比例分批。**h 指计划释放时长**。业务期限 D 由调用者给出。
- `compare` 先筛掉超期限、完成率不足或风险超预算的候选。成本优先沿用模型成本最低者。等待优先从同一批可行候选中选等待负担最低者。此时 `choice` 可与 `plan.arm` 不同。
- `baseline_first` 只有在冻结 A 可用且符合当前约束时才返回参考方案。若 A 缺失或需复核，保持空计划并说明原因。切换模式由调用者决定。
- 成本优先而冲击系数 `impact_a_bp` 未知时，报告条件比较和缺项。显式 `compare` 加结构化 `wait_first` 可在成本未知时给出等待计划，保留 `predicted_cost_bp=null`。输入或数据不足、无可行候选时，返回实际 `status` 和原因。
- 结构化偏好走本地计算。自由文本偏好只有在调用者授权且显式启用 `--use-jev` 时才允许一次 Jev 请求，发送业务说明以判别偏好标签。所有候选的数值计算和排序仍由脚本完成。

输出恒带 `execution_action=NONE` 和 `production_eligibility=INSUFFICIENT`。结论的适用范围是给定容量、价格代理、冲击与方向假设下的研究比较。[完整字段与历史数据格式](references/contract.md)
