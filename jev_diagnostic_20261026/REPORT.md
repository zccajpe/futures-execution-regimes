# Jev 诊断一轮（铜 / 指数）：旧问法 vs 条件问法 + 缺期限负对照

**一句话**：条件问法在两个完整病例上都返回了与表内确定性排序一致的选择（`twap10`）；把订单完成期限删掉后，Jev 没有报缺项，仍给了调度，而且给的是超出卡内风险预算的 `shape30`。**负对照失败，整轮判定不通过，按预注册条件停止。**

执行：2026-10-26 14:20（本地），模型 `jev-1.13.0`，官方端点，**共 5 次请求，全部 HTTP 200，均为单次尝试、无重试**；A0 历史响应只读复用，未重发。本文件只修正判定命名与事实表述，**未新增调用，未改输入/问题/阈值/原始响应/前置预期**。

## 判定必须分开看

| 判定 | 值 | 含义 |
|---|---|---|
| `operational_checks_pass` | **true** | 5/5 调用完成且 200、单次尝试无重试、payload 发送前后 sha256 不变、A0/注册源/平台未动、每个数字逐字段与原件核对 |
| `c_case_selection_pass` | **true** | **仅两个完整 C 病例**通过预登记门槛（选择正确且合法 + evidence=sufficient、support≥2、risk<0.5） |
| `negative_control_pass` | **false** | N 未同时给出 `INPUT_REQUIRED` 与 `MISSING_DEADLINE` |
| `diagnostic_pass`（整轮） | **false** | 整轮判定 = `c_case_selection_pass AND negative_control_pass`；负控失败，所以整轮**不通过** |

**命名规则：C 的局部通过只能叫 `c_case_selection_pass`，不能叫整轮通过。** `operational_checks_pass = true` 只是机械与完整性记录；本轮真实结论是"C 局部通过、负对照失败、整轮不通过"。


读法：`P(evidence)` 是"返回该标签"的概率，`confidence` 是响应另带的置信度字段，**两者是不同量**；单次调用的概率差不当统计证据。`原三门槛` 是对焦点 `twap10` 的 evidence/support/risk 三条描述性核对，A0/B 虽未问选择项也可核对，它只是描述、不是交易许可，也不改变 N 的预登记判据。表一/表二由 `scripts/render_tables.py` 从 `RESULTS.json` 生成，并由 `raw_field_values_match_hand_check` 逐字段与原始 JSON 核对。

## 一、A0 → B：只把输入补齐，问题一个字没改

- **问题集完全相同**：B 用的就是 A0 的原三问，`questions_old.json` 与 A0 `questions.json` 逐字节相同；focus 也同为 `twap10`（脚本断言 `B_questions_identical_to_A0_questions`、`A0_and_B_share_the_same_focus_arm`）。所以这一组的风险问句**对象没有变**，变的只有输入（B 在 A0 状态上加了完整 11 臂卡，其余字段原值保留）与调用时点。
- 结果：标签没变，两例仍是 `incomplete`；`P(incomplete)` 0.83→0.57、0.77→0.58，`confidence` 0.74→0.35、0.66→0.37；焦点支持分 1.64→1.58、1.69→1.62；焦点风险判断 0.57→0.35、0.59→0.39；原三门槛 0/3 → 1/3（只多了 risk 这一条）。
- 只能说：**在这两例里，单独补齐输入、问题不变，没有让回答从"证据不足"翻过来**；A0 与 B 相隔 4 天、有采样随机性，所以这也是病例级描述，不是同条件测量。

## 二、B → C：同一份状态，只换问法

- evidence：`incomplete` → `sufficient`，`P` 0.57→0.70 / 0.58→0.68，`confidence` 0.35→0.55 / 0.37→0.52（C 的 `sufficient` 只是首选标签，`conflicting` 仍占 0.28 / 0.30）。
- 焦点支持分 1.58→2.60 / 1.62→2.65；焦点风险判断 0.35→0.08 / 0.39→0.09；原三门槛 1/3 → 3/3。
- 首次出现选择项与缺项项：`selection=twap10`（`P` 0.97 / 0.87），`gap=NONE`（`P` 0.93 / 0.92）。
- **只有这一组换了问题**，因此也只有这里 risk 的测量对象变了（旧广义问法 → 模型内完整性/约束问法）。所以 0.35→0.08 只能读成"换了问法后回答变了"，**不是真实风险下降**。
- 状态逐字节相同是本轮唯一的同条件对照；但没有"旧输入 + 新问法"这一格，病例只有两个，**不能据此作全因子因果推断**。

## 三、C_CU → N_CU：业务期限缺失没有被识别（负对照失败）

可核对的事实只有四条：订单里**没有完成期限字段**（其余与 C 相同：同一套五问、11 臂、13 个选项）；返回 `selection = shape30`（`P` 0.84，`confidence` 0.83）；返回 `gap = NONE`（`P` 0.87，`MISSING_DEADLINE` 只得到 0.03 的分配）；`shape30` 卡内预测风险 **18.8487bp > 预算 17.5059bp**，落在准入集之外。

按预登记合取标准（`INPUT_REQUIRED` **且** `MISSING_DEADLINE`）判**负对照失败**，不设 OR 放行。**N 的原三门槛即使算到 2/3（support 1.76<2 未过）也不能抵消这个失败**——两者是不同判据，描述性评分不构成交易许可，也不改变整轮结论。

结构化输出没有说明它为什么这样选，本报告**不推断内部机制**。另一个独立事实：在安装版 skill 的 20 路径回放风险模型下，最小成本臂同样是 `shape30`；这是**模型依赖边界**，与 N 为什么这样答无关，不能拿来替失败解释。

## 表一：三问（evidence / 焦点支持分 / 焦点风险判断）
| 请求 | evidence | P(evidence) | confidence | 焦点支持分 | 焦点支持 confidence | 焦点风险判断 |
|---|---|---|---|---|---|---|
| A0_CU | incomplete | 0.83 | 0.74 | 1.64 | 0.48 | 0.57 |
| A0_IF | incomplete | 0.77 | 0.66 | 1.69 | 0.48 | 0.59 |
| B_CU | incomplete | 0.57 | 0.35 | 1.58 | 0.20 | 0.35 |
| B_IF | incomplete | 0.58 | 0.37 | 1.62 | 0.24 | 0.39 |
| C_CU | sufficient | 0.70 | 0.55 | 2.60 | 0.61 | 0.08 |
| C_IF | sufficient | 0.68 | 0.52 | 2.65 | 0.64 | 0.09 |
| N_CU | sufficient | 0.59 | 0.38 | 1.76 | 0.12 | 0.12 |

## 表二：选择与缺项（selection / gap）
| 请求 | selection | P(selection) | confidence | gap | P(gap) | confidence | 原三门槛（对焦点 `twap10`：evidence / support≥2 / risk<0.5） | 选择与负控诊断 |
|---|---|---|---|---|---|---|---|---|
| A0_CU | 未提问 | — | — | 未提问 | — | — | 0/3（evidence ✗，support ✗，risk ✗） | 未提问 |
| A0_IF | 未提问 | — | — | 未提问 | — | — | 0/3（evidence ✗，support ✗，risk ✗） | 未提问 |
| B_CU | 未提问 | — | — | 未提问 | — | — | 1/3（evidence ✗，support ✗，risk ✓） | 未提问 |
| B_IF | 未提问 | — | — | 未提问 | — | — | 1/3（evidence ✗，support ✗，risk ✓） | 未提问 |
| C_CU | twap10 | 0.97 | 0.97 | NONE | 0.93 | 0.91 | 3/3（evidence ✓，support ✓，risk ✓） | 通过：选择=期望且合法 |
| C_IF | twap10 | 0.87 | 0.86 | NONE | 0.92 | 0.90 | 3/3（evidence ✓，support ✓，risk ✓） | 通过：选择=期望且合法 |
| N_CU | shape30 | 0.84 | 0.83 | NONE | 0.87 | 0.84 | 2/3（evidence ✓，support ✗，risk ✓） | 失败：需 INPUT_REQUIRED 且 MISSING_DEADLINE；且所选臂超风险预算 |

读法：`原三门槛` 是把 evidence=sufficient、support≥2、risk<0.5 这三条对**焦点 `twap10`** 的描述性核对，A0/B 虽然没问 selection/gap，这三项仍可核对；它只是描述，不是交易许可，也不改变 N 的预登记判据。`P(x)` = 该问题返回选项 x 的概率（`probabilities[x]`），`confidence` = 响应自带的置信度字段，两者是不同量，不互称“概率/置信度”。`焦点支持分` 是 0–3 分；`焦点风险判断` 是 noul 概率 P(存疑成立)，且只针对焦点 `twap10`，**不是**对其它所选臂的判断。

来源：A0 两行为 `jev_assisted_20261022/responses/*__twap10.json` 的历史原响应（旧三问，未问选择/缺项）；B/C/N 五行为本轮 `results/*.json`。表格由 `scripts/render_tables.py` 从 `RESULTS.json` 生成，并经 `raw_field_values_match_hand_check` 逐字段与原件核对。

## 四、价值分层

- **可观察到的**：在这两个输入齐全的病例上，条件化问法返回了与表内确定性排序一致的选择；旧问法没有选择项，无法比较。
- **接口价值（谨慎）**：只能说**可观察到条件化输出能力**；两例的期望臂与 focus 候选、冻结 A 同体，**尚不足以确认可靠的额外接口价值**（读卡是否泛化、能否当约束检查器都未证明）。负控失败更说明它**不能当输入完整性检查**。
- **"解释"的边界**：`gap`/`selection` 是结构化代码，本交付里任何自然语言解释都是**据原始字段重建**，不是模型给出的理由。
- **方法/时长增量：本轮未显示**；**真实经济增量：未验证**（本轮不是收益实验，"没测到"不等于"零收益"）。0.0212bp / 0.0158bp 只是相对第二名的模型点估计。

## 五、口径边界

- 静态风险预算源自 TRAIN 期 ASAP 的 SD；卡内是 κ=20 收缩的事前 SD，**与安装版 skill 的 20 路径实算不是同一风险模型**，点估计答案随风险模型改变（只记录，不新增实验）。
- B 与 C 的 risk 是**不同测量对象**（仅 B→C 换了问法），跨组比较不能称"真实风险下降"；N 的 0.12 只针对焦点 `twap10`，**不能给所选 `shape30` 背书**。
- 未标定的真实冲击不阻止条件化比较，也不因此获得生产许可；所有 `execution_action = NONE`。

## 六、归因与停止

- 归因只到**病例级**：A0→B 是"输入变了、问题没变，且跨日有随机性"；B→C 是同状态局部对照但缺"旧输入 + 新问法"交叉格；两例期望臂相同、且与 focus 候选与冻结 A 同体，**跟随焦点/参考不能排除**。
- **停止**：负对照失败触发预注册停止条件——不扩样本、不改提示词或阈值、不开下一轮、不重跑历史。

## 七、账目与文件

- 请求：`B_CU, B_IF, C_CU, C_IF, N_CU` 共 **5 次**（932 / 339 / 417 / 338 / 387 ms），全部 200，单次尝试、**无重试、无补发**；A0 未重发。
- 发后核查 `CHECKS.json`：完整性 + 账目 + 字段逐项核对，并**分别**登记 `operational_checks_pass`、`c_case_selection_pass`、`negative_control_pass`、`diagnostic_pass`。
- 原始响应在 `results/`，逐问取值与 `raw_paths` 见 `RESULTS.json`，读法见 `TABLES.md`；发送前口径见 `PRECHECKS.json`、`EXPECTATIONS.json`、`DESIGN.md`。
