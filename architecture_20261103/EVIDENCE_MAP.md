# 证据对照：旧成果 → 新验收标准（2026-11-03，仅对照，不重跑研究）

> 本轮按协调方核对过的锚点修正数字与结论。**只做对照**：不取数、不计算新统计、不改旧报告/skill/数据。凡与旧叙述冲突处，一律以结构表（CSV/JSON）为准，并把冲突列为“需整合纠正”，不覆盖旧报告。

## 大白话：三层各到哪，缺的是哪一类问题

**先分清三个“成本对象”，后文一律标明用的是哪一个**：

- **训练期冻结策略**（各自训练选定的 h，不是固定 h）：留出期中心格 ASAP 5.68bp、固定 TWAP 2.50978、固定 SHAPE 2.54853、逐日自适应 3.25bp（`RESULTS_DETAIL.md` §4）。
- **固定 h=30 的候选**：SHAPE30 2.871916、TWAP30 2.898753（`decision_research/COST_ATTRIBUTION.md` §0、`results/all_candidates_center.csv`）；相对 ASAP 5.677177，**SHAPE30−ASAP = −2.805262bp**（价格 +0.475981、费用 +0.000062、冲击 −3.281305）。**这个 −2.805262 是 SHAPE30−ASAP，不是 TWAP30，也不是 ASAP−TWAP30。**
- **事前规则 P**（9 桶）：P−ASAP 在 VAL/TEST 为 −0.0069/−0.0072bp（区间不含 0）；**P−A 为 +0.0014/−0.0018bp，区间均跨零**（`exante_policy_20261022/EVALUATION.md` §4）。

三层状态：

- **第一层（条件取舍）**：在声明冲击情景下有取舍——a=0.5 时固定 h=30 的 SHAPE30 比 ASAP 模型总成本低 2.805bp；但 a=0 时中心候选相对 ASAP 全部跨零（`decision_research/boundary_ci.csv`）。**取舍存在，且是条件结论。**
- **第二层（事前识别）**：两轮都未过采用门槛。活跃度把总量预测的归一化绝对误差相对降低 15.9%/19.4%（`volume_activity_20261022/STAGE1.md`），但选型覆盖 96.76%/96.80%、固定全集完成率 −2.95/−2.84pp（`volume_activity_20261022/STAGE2.md`）；9 桶规则 P−ASAP 只 −0.007bp 量级、P−A 区间跨零，C1 失败。
- **第三层（相对 ASAP 与冻结 A 的增量）**：B/C 六配置全不采用——k=1、a=0.5 时 B−A/C−A 为 −1.470/−1.689bp，但价格项 SD 比 1.261/1.304 超 1.10；k=2 时只有 −0.240/−0.229bp，本身不过 0.5bp（`decision_research/POLICY_FINDINGS.md`）。

**缺的是两类不同的问题，不能合并成一句**：

1. **呈现/口径问题**（可通过报告整合解决）：三层结论没有按验收表归档；成本三个对象被混写；旧叙述里的 markout 覆盖率与结构表冲突（见下）；残余均值则是**本轮初稿误写为 0，已纠正**，旧主报告原表只列 95 分位为 0。
2. **经济证据问题**（不是整合能解决的）：**尚未获得满足原标准、可改变执行的事前选择规则**。分钟数据也不能标定自身冲击 a。给中心固定 SHAPE30 补一个置信区间只是精度补项，既不会让 a 变得可识别，也不会让风险门槛或事前增量自动通过。**没有强制找到正规则的任务；可靠的负结论本身可交付。**

## 一、主表：验收项 → 现有证据 → 结论 → 缺口性质

> 标签：`已具备可复用` / `需整合表述` / `需从已有缓存补算` / `需新的研究检验` / `超出现有数据` / `需业务约定`。所有数字注明对象（冻结策略 / 固定 h / 事前 P）、范围、分母。

| 验收项 | 现有文件与段落/字段 | 目前支持什么结论（对象/范围/分母/条件） | 缺口性质 |
|---|---|---|---|
| 完成率 | `results/paired_metrics_complete.csv`（`base_completion`、`base_n_completed`、`sel_*`、`n_paired`）；`RESULTS_DETAIL.md` §3 | 留出、买、1%、D=60、a=0.5：ASAP 2424/2424；固定 TWAP 与固定 SHAPE 各 2410/2424（99.42%）；逐日自适应 2401/2424（99.05%）。全留出母单覆盖率 68.1%（完成优先）/93.3%（成本优先），不可行单列（`RESULTS_DETAIL.md` §3） | 已具备可复用；需整合表述（须同时给未完成计数） |
| 成本点估计 + 样本不确定性区间 | `decision_research/COST_ATTRIBUTION.md` §0；`results/all_candidates_center.csv`；`decision_research/boundary_ci.csv`；`decision_research/POLICY_FINDINGS.md`；`exante_policy_20261022/EVALUATION.md` §4 | 固定 h=30：SHAPE30 2.871916、TWAP30 2.898753 vs ASAP 5.677177（差 −2.805262 为 SHAPE30−ASAP）；训练冻结策略见 `RESULTS_DETAIL.md` §4。a=0 的中心候选区间全部跨零（如 twap30 +0.489 [−0.460, +1.414]）。P 与 B/C 都已有 5 日块 bootstrap 区间 | 已具备可复用；**可选补算**（固定 SHAPE30 的 a=0.5 区间无现成证据，属精度补项） |
| 费用/冲击情景范围（与成本区间分列） | `decision_research/COST_ATTRIBUTION.md` §2；`decision_research/envelope.csv`（747 行，risk_k 两档） | 在所列中心队列上：a=0 的点估计无成本优势、a=0.5 出现、a=2 更大；区间跨零不证明市场没有优势。φ=1 为主、φ=0.5 敏感性有记录；翻转点 0.07–0.09bp 只是该样本位置 | 已具备可复用 |
| 执行后价格变化（markout，不称因果冲击） | `results/paired_metrics_complete.csv`（`base_mo15_cov`、`sel_mo15_cov`、`*_mo5/30_bp`）；`results/case_methods.csv`（`markout5/15/30_bp`） | 留出同一行：ASAP `base_mo15_cov`=1.0；固定 TWAP 0.9693331；固定 SHAPE 0.9724861；自适应 `sel_mo15_cov`=0.9734814。**旧报告写的 99.8%/98.4% 与结构表不一致，按 CSV 为准并列为待纠正**。字段含义：`scripts/run_replay.py:149` 记的是“该单具有相应后续分钟价格的成交手数 / 该单总成交手数”，`scripts/build_delivery_tables.py::metrics` 再对这些比例取逐母单平均，所以这里是**成交量可观测占比的逐母单平均**，不是“有数据的订单数 / 订单数” | 已具备可复用；需整合表述（含口径冲突） |
| 残余 | 同上（`base_R_over_Q_mean`、`sel_R_over_Q_mean`、`*_p95`）；`results/case_methods.csv`（`resid_lots`） | 留出同一行：ASAP 0；固定 TWAP 4.121e-05；固定 SHAPE 3.036e-05；自适应 8.292e-05（均非零），95 分位为 0；TWAP/SHAPE 实际完成 2410/2424。**本轮初稿曾误写“均值为 0”，已纠正；`RESULTS_DETAIL.md` §4 原表只列 R/Q 95 分位为 0，未写均值** | 已具备可复用；需整合纠正 |
| 模拟回执对照 | 同一课题此前阶段的本地模拟撮合记录 §9–§15（未随公开仓发布） | 已有：接入与账户只读；GFD 挂单与**真实撤单终态 `cancelled`**（filled 0/unfilled 2，§9）；**市价 IOC 开多 20 手全部成交** @3084（tradeId 3025，费 62.2968，§10）；平仓 20 手 @3084（tradeId 3063，费 62.2968），累计费用 124.59，价格损益 0，持仓/活动委托/保证金/冻结均 0（§13）；账本闭合与边界（§15）。§14 记录“20 手执行触发者未核实”，原样保留、不补猜测 | 已具备可复用（接入/撤单/回执/核账）；边界：**无自然 partial、无 >L1、无自身冲击、无队列、无三臂比较**，不新增模拟实验 |
| 同母单三方法 | `RESULTS_DETAIL.md` §4（训练冻结策略）；`results/all_candidates_center.csv`（固定 h 全候选）；`results/paired_metrics_complete.csv`（baseline=asap/fixed_twap 两行） | 同一冻结母单、同一路径、同一业务约束下三方法可比；但“训练冻结策略”与“固定 h=30”是不同对象，不能互相替代 | 已具备可复用；需整合表述 |
| 公平冻结基准 A | `delivery/futures-execution-decision/data/frozen_A_params.csv`；`exante_policy_20261022/EVALUATION.md` §4；`decision_research/POLICY_FINDINGS.md` | A 训练期选定后冻结；A 在范围 E 内 86.5%/86.8% 有定义；P−A 区间跨零，B/C−A 见第三层 | 已具备可复用 |
| 第一层：条件取舍 | `decision_research/COST_ATTRIBUTION.md` §0/§3/§5；`decision_research/same_h_pairs.csv`（45 行） | 固定 h=30 时 SHAPE30 比 ASAP 低 2.805bp（全部来自冲击项）；同 h 下上午 h=20/30 按量分批再省 0.097/0.119bp（CI 不含 0），下午/夜盘无；边际 5→30 省 2.33bp、30→60 只省 0.39bp | 已具备可复用（须写成条件结论） |
| 第二层：事前识别 | `volume_activity_20261022/STAGE0.md`（18,740 中 843=4.50%）；`volume_activity_20261022/STAGE1.md`（β=0.5233）；`volume_activity_20261022/STAGE2.md`；`exante_policy_20261022/EVALUATION.md` §4 | 选择空间薄但存在；总量预测改善；两条事前规则均未过采用门槛（覆盖、全集完成率、C1） | 已具备可复用；需整合表述 |
| 第三层：决策增量 | `decision_research/POLICY_FINDINGS.md`；`exante_policy_20261022/EVALUATION.md` §4；`volume_activity_20261022/STAGE2.md` | 相对冻结 A 无采用增量；在 a=0.5 的例子中 B/C 于 k=1 失败于风险门槛、于 k=2 失败于成本门槛（其他配置见 `decision_research/POLICY_FINDINGS.md` 六配置表，不概括全部 a）；P 对 A 区间跨零 | 已具备可复用 |
| 实际改变决策的幅度 | `volume_activity_20261022/STAGE2.md` §选型变化；`exante_policy_20261022/EVALUATION.md` §4（非 ASAP 占比 4.5%/4.2%）；`jev_assisted_20261022/CONCLUSION.md` | 变化面存在但幅度小，多数“改名”在容量受限时成交相同；没有任何规则被采用 | 需整合表述（分散在三处） |
| 分母与“不能只报完成子集” | `volume_activity_20261022/STAGE2.md` §覆盖与固定全集口径；`RESULTS_DETAIL.md` §3；`exante_policy_20261022/EVALUATION.md` §4 | 已有“缺计划按未执行记账”的全集口径与覆盖率；条件子集另列（允许） | 已具备可复用 |
| 回顾性口径 | `exante_policy_20261022/EVALUATION.md` §4；`decision_research/POLICY_FINDINGS.md` | VAL/TEST 已被反复查看，只能称**回顾性时间顺序验证**；74 个交易日是日期抽样单位（5 日块 bootstrap），不是 74 个独立同分布样本 | 需整合表述（报告措辞） |
| 中文报告 | `REPORT.md`、`STAGE_REPORT_20261022.md`、`DECISION_GUIDE.md` | 有中文总报告与阶段报告 | 需整合表述（按三层 + 验收表重排，不重写证据） |
| 工程复用 | `ENGINEERING.md`、`DATA_COVERAGE.md`、`results/*.npz`、`decision_research/*.py` | 取数、对齐、回放、归因、包络、评价模块齐全可复现 | 已具备可复用 |
| skill 输入输出与安装调用 | `delivery/futures-execution-decision/{SKILL.md,references/contract.md}`；`VALIDATION.md`（76 项）；`skill_demo_20261030/installed_calls/summary.json`；`local_skill_cases/CASES.md`（13 例） | 已安装 `24df1ac1…`；现有分支为参考/条件/空值，缺 D 或预算返回 `INPUT_REQUIRED`，降级时 `choice`/`method`/`h` 为空并保留 `conditional_*`；安装版 10 项检查通过。**“稳健建议”当前不成立**（见 G3），不需要为此新开发 | 已具备可复用；需整合表述（把“当前无稳健建议”写清） |

## 二、会影响交付判断的缺口（≤5，按优先级）

| 缺口 | 会改变哪个判断 | 最小动作与具体输入 | 继续/停止条件 |
|---|---|---|---|
| G1 报告整合与口径统一（呈现类） | “交付是否可读、可复述”；不改经济结论 | 纯整合：按三层重排结论；成本对象分三档标注；用 `results/paired_metrics_complete.csv` 的 `base_*`/`sel_*` 值替换旧叙述里的覆盖率与残余均值；VAL/TEST 改称回顾性验证 | 三层各有位置、口径冲突全部标注即停；不重新计算 |
| G2 事前规则未获支持（证据边界） | 是否继续投入事前识别 | 现有证据已足以给出**负结论**：P/B/C 均未过原标准。若仍要继续，需提出与已做三关**不同**的新问题，不得换名重跑 | 交付负结论即停；无新问题则不再投入 |
| G3 skill“稳健建议”缺位（证据+约定） | 返回分支与用户可读性 | 现状：**当前没有满足条件的稳健建议**，应保持条件/空值输出并写明理由；仅靠业务阈值不能启用，需跨假设稳定性与样本证据 | 有证据支持稳健判定才启用；否则维持现状即停 |
| G4 skill 调用时的输入约定 | 其他执行单元实际调用 skill 作判断时的输入约定；不是交易授权，不在本轮启动任何实测或下单 | D、风险容忍、未完成处置三项由调用者给；已冻结的 D 档/风险档/情景是**明确标注的研究范围**，不因没有真实订单而阻塞研究 | 约定明确后由后续批次决定是否做调用验证；本轮不启动实测；未确认不影响历史研究结论 |
| G5 固定 SHAPE30 的 a=0.5 成本区间（可选补算） | 可能改变固定 SHAPE30 条件比较的证据强度；尚未计算，不预判区间；不会改变已有 P/B/C 的失败判定 | 用 `results/replay_test_t60.npz` 与既有 5 日块 bootstrap 口径补一个区间；无现成证据 | 可选；**不得用区间追认原标准成功**；不做也不影响 P/B/C 已有的负结论（区间可能改变的是该条件比较的强度，不是失败判定） |

## 三、已有且不应重做

- 三关（`volume_activity_20261022/STAGE0.md`、`volume_activity_20261022/STAGE1.md`、`volume_activity_20261022/STAGE2.md`）、9 桶规则（`exante_policy_20261022/EVALUATION.md`）、包络与归因（`decision_research/*`）、Jev 两轮（`jev_assisted_20261022/`、`jev_diagnostic_20261026/`）：**不换名重跑**。
- 模拟回执：接入、GFD 挂单与撤单终态、IOC 全成/全平、费用与账本闭合已验证（§9–§15）；**不新增**模拟实验，也不据此推断三臂优劣或自身冲击。
- 可沿用的决定：不采用 ACT 选型；总量提示只作非强制警告、不硬拒单；有效范围内参考冻结 A；无可行方案时明确“无计划”，不静默退回 ASAP。
- 幅度边界：a=0.5/k=1 时 B/C−A 为 −1.470/−1.689bp 但 SD 比 1.261/1.304；k=2 时 −0.240/−0.229bp 不过 0.5bp；a=0 时约 0。**均为回顾性条件研究，不是实际收益。**

## 四、下一步：只做整合（不新开计算）

**下一批只做一件事**：另写一份按新架构整合的**报告草稿**（及必要的 skill 说明草稿），保留旧报告与已装包不动；本轮只是建议、尚未执行。

1. 草稿按三层组织，并把成本对象、分母、未完成单列、回顾性口径写死（G1）。
2. 草稿用结构表数值写 markout 覆盖率（旧报告与 CSV 的冲突在此说明，不改旧报告）；残余均值以本轮初稿纠正后的非零值为准。
3. skill 说明草稿写明“当前无满足条件的稳健建议”，保持条件/空值分支（G3），不改已装包、不做新开发。
4. **不推荐**现在为 G5 补区间：P/B/C 的区间已足以支撑现有负结论，补一个中心区间不会改变任何采用判断；若日后需要更精确的第一层表述再单独开。
5. 停止条件：草稿三层各有位置、口径冲突写明、skill 分支写清 → 下一批完成；本轮不做草稿落盘。

## 五、需要业务方确认的最少事项（与历史研究解耦）

仅影响其他执行单元调用 skill 时的输入约定，不影响本阶段结论；**不要求用户现在有真实订单**：①实际 D 取值；②风险容忍到 k/预算的映射；③未完成单处置；④冲击/费率情景档位是否覆盖业务认知；⑤若要用“稳健建议”，其判定标准（证据侧要求，不是单纯选阈值）。

（本文件只做旧成果对照；下一步为文档整合，不含新计算。）
