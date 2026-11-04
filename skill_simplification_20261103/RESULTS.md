# 执行调度 skill 简化：结果（2026-11-03）

## 结论

一次调用现在能做的事：给一笔已决定要交易的母单，加上业务期限 D、风险预算和冲击情景，返回一个**方法与计划时长 h**，外加通过检查的合法基准、不可行时为 null，以及一句中文理由。

- 入口：`delivery/futures-execution-decision/scripts/preference_plan.py`。
- 它只新增 `plan` 与 `preference_routing` 两个字段，`decision.py` 的 `status`、`choice`、`method`、`h` 全部保持原样。
- 调用者可给结构化偏好，此时 **0 次 Jev 请求**；只有自由文本业务说明才会送 Jev，且只判一个标签。
- 任何不确定的情况都退回「没有新计划」，不会退回猜一个偏好。全部返回 `execution_action=NONE`、`production_eligibility=INSUFFICIENT`。

这不是经济最优结论，也不是生产验收。下文每个数字都是给定假设内的模型结果。

## 改了什么

| 项目 | 内容 |
|---|---|
| 新增入口 | `scripts/preference_plan.py`（一个函数 `decide()` + 一个 CLI） |
| 新增字段 | `plan`（方法、h、目标、指标、理由）、`preference_routing`（偏好来源、是否调用 Jev、请求次数） |
| 偏好枚举 | `cost_first` 成本优先 / `wait_first` 减少模型等待负担优先 / `unspecified` 未明确 |
| 复用 | `decision.py` 的候选计算、`effective_constraints`、`candidates`、既有 `wait_min`；不重算、不改约束 |
| 未改 | 正常内核、`choice` 语义、四个模式语义、冻结参数、旧报告与既有失败判定 |

`wait_first` 只在**同一合法集合内**最小化既有 `wait_min`。它是「等待负担最小」，不是实际最快做完。

## 实测结果

测试：`python3 -m unittest discover -s tests` → **103 项全部通过**（决策 17 + 事前 36 + 预测 9 + 契约 14 + 偏好入口 27）。原有 76 项一项未改、全部保持通过。

四个既有用例，每个都跑「无偏好 / 成本优先 / 等待优先」三种调用，共 12 次调用，**0 次 Jev 请求**（记录见 `cases/call_counts.json`）：

| 用例 | 原状态 | 无偏好 | 成本优先 | 等待优先 |
|---|---|---|---|---|
| RB892 D15 | `REFERENCE_BASELINE` | asap0（保留基准 A） | asap0 | asap0 |
| CU602 D60 | `CONDITIONAL_MODEL_CHOICE` | shape30 | shape30 | asap0 |
| RB3569 缺 D | `INPUT_REQUIRED` | null | null | null |
| SC14 D5 缺 A | `INSUFFICIENT_BASELINE` | null | null | null |

CU602 这一行是本次唯一产生不同计划的地方，且两支都来自同一批合法候选：

- 成本优先 shape30：费用 1.30 + 方向 0.00 + 冲击 4.08 = 5.3764bp，等待负担 17.32，完成率 1.000，价格项 SD 15.77bp。
- 等待优先 asap0：成本 6.1627bp，等待负担 13.52（合法集合内最小），完成率 1.000，价格项 SD 14.09bp。

两者都在 D=60、完成门槛 0.95、预算 17.5059bp 之内。等待优先换来 3.79 分钟等待负担的下降，代价是 +0.79bp 模型成本——**这是给定假设内的模型取舍，不是实测节省或实测损失**。

RB892 说明了基准优先的优先级：即便另给偏好，计划仍是基准 A 的 asap0，不因别的候选点估计更便宜而替换。

### 迁移与自包含

包被复制到临时目录后，用相对路径直接跑通新入口和原 `decision.py`（`test_relocated_package_runs_from_relative_path`）。可复用代码与测试里没有家目录形式的绝对路径；新增测试用包内 `tests/fixtures/*.json` 配 `examples/RB_am_20d_20260803.npz`，不依赖任何本机研究目录。

## Jev 有没有减少「人读句子 + 判断走哪条路」

**已实现并在本批命中**：从自由文本到三个标签之一的映射被收进一次调用，调用者不再需要自己把「别让我久等」翻译成某个固定字段。结构化偏好路径完全不需要 Jev。

**本批真实结果**：预登记写死在 `jev_prereg/PREREG.json`（2 个偏好 × 2 种说法，发送前已固定措辞与期望标签），随后按此执行，**恰好用 4 次、无重试、4/4 命中**：

| 请求 | 说法 | 期望标签 | 实际标签 | confidence | 映射后的计划 | 合法 |
|---|---|---|---|---|---|---|
| cost_first_p1 | 这笔单子我更在意综合成本，能便宜一点更好。 | cost_first | cost_first | 1.0 | shape30 | 是 |
| cost_first_p2 | 费用方面尽量省，成本优先。 | cost_first | cost_first | 1.0 | shape30 | 是 |
| wait_first_p1 | 等待负担小一点，别让我一直盯着等。 | wait_first | wait_first | 1.0 | asap0 | 是 |
| wait_first_p2 | 这次希望尽量减少等待负担。 | wait_first | wait_first | 1.0 | asap0 | 是 |

每次都是单次 HTTP 请求，`200`，模型 `jev-1.13.0`，输入约 474–482 tokens、输出 45 tokens，延迟 240–771 ms。`jev_prereg/results/` 存的是实际发送的请求与真实响应字段摘录，不是完整 HTTP 原文。密钥只在内存中使用，未写入任何产物。

这与本地先算出的期望值完全一致，因此映射后的计划就是上表 CU602 那两行（shape30 / asap0），且都在生效约束内。

**这仍然只证明语义接口在本批 4 次里可用**：

1. 4/4 命中说明这几个说法的标签映射可以交给一次调用，**不**说明换说法、换品种、边界说法也稳；样本只有 4 次，没有统计意义。
2. 它**不**说明市场状态择时有效，也**不**说明省下了钱。语义映射正确 ≠ 执行更划算。Jev 全程没有参与成本、风险或排序计算，那几个数字全部来自 `decision.py`。
3. 旧的 Jev 诊断结论仍然有效，不能被本轮命中掩盖：它不是输入完整性检查器（缺 D 的负对照仍给出 shape30，预测风险 18.8487bp 超过 17.5059bp 预算）；两个完整用例里没给方法/时长或经济增量；通过的用例恰好都跟随焦点候选，无法排除跟随焦点；阈值型分数不可照搬，也不能越过硬风险预算。

## 仍然未证明的

- 冲击系数 a 仍未标定。成本排序依赖给定的 a；a 只在区间内时入口不生成点选择。
- 风险是价格项标准差，未折算成金额；两种风险模型（`empirical_sd_20_paths` / `train_cell_shrunk_sd`）不可互换。
- `h` 是计划释放时长，不保证在 h 内完成；完成率是 20 条历史路径中零残余的比例，不是校准过的未来概率。
- 没有 CU602 该配对的置信区间，因此不主张任何方案优于基准，也不称两者等价。
- 本轮未接入下单、账户或任何交易接口。

## 复现

```bash
cd delivery/futures-execution-decision
python3 -m unittest discover -s tests
python3 scripts/preference_plan.py --request tests/fixtures/compare_cost_first.json --out /tmp/plan.json
python3 scripts/preference_plan.py --request tests/fixtures/compare_free_text.json --out /tmp/note.json
```

第二条给结构化偏好，0 次 Jev；第三条带自由文本但没有 `--use-jev`，因此不发送、也不生成计划——这正是「不确定就退回无计划」的行为。

## 本轮额度与停止条件

- Jev 额度：4 次上限，**实际用 4 次**，无重试。停止条件（任一即停）已在发送前固定：请求失败或超时、标签与预登记不符、同一偏好的两种说法不一致、映射计划不合法。本批四条都没有触发。
- 未换词、未回填期望、未追加请求。

## 注册状态

新版本 `skill-futures-execution-decision@fa49d2fe1bee2cc0b62a612729c5ab0875532be7` 已通过 QuantStudio 原生本地创作接口注册，并驻留到原研究会话；39 个文件，655,179 字节。旧版 `24df1ac1` 保留，原计算内核与冻结参数未变。原生结果见 注册记录（未随公开仓发布；如需复现请在本地准备 `skill_simplification_20261103/native_registration.json`） 与 当前驻留（未随公开仓发布；如需复现请在本地准备 `skill_simplification_20261103/resident_current.json`）。

协调方另外实际运行了安装版的成本优先与等待优先两个包内示例，均成功返回合法计划，均为 0 次 Jev 请求；这是安装后运行检查，不增加上述四次真实试验。记录见 安装版调用（未随公开仓发布；如需复现请在本地准备 `skill_simplification_20261103/installed_smoke.json`）。本轮的最终源码入口见 [SKILL.md](../delivery/futures-execution-decision/SKILL.md)。
