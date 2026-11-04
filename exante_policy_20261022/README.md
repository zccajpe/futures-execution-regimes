# 事前选型规则阶段（exante_policy_20261022）

本目录是"用已有分钟数据建立下单前选型规则"的完整阶段：设计 → 数据就绪 → 特征/预测缓存 → 一次主拟合 → 统一评价 → 技能工作区版本。

**结论：未验证出优于 ASAP 与冻结 A 的自动选型规则。** 9 个状态桶全部被门控置零；主配置（a=0.5, k=1）留出期相对 ASAP −0.007215bp、相对 A −0.001794bp（区间跨零），远低于 0.5bp 参考改善。技能提供 `mode=exante` 真实运行该规则，但顶层建议只在冻结 A 当日可行时给出，规则结果放在 `research_candidate`。

| 文件 | 内容 |
|---|---|
| `DESIGN.md` | 预注册设计（决策单位、信息集、规则、时间验证、评价标准） |
| `EVALUATION.md` | 主拟合、评价、归因、案例、失败解释、技能一致性 |
| `DATA_READINESS.json` | 19 项数据就绪核查 |
| `features_build.json` | 特征/预测缓存的键对齐与一致性核对 |
| `params_exante.json` | 冻结参数（9 桶、切点、κ、风险单元格、拟合审计） |
| `evaluation.json`、`decisions_{val,test}.csv`、`outcomes_{val,test}.csv` | 统一评价结果、逐单决策与逐单元实现结果 |
| `case_calls.json`、`requests/case_*`、`responses/case_*` | 预注册案例的真实技能调用（Q/D/窗口与研究同一单元） |
| `skill_equivalence.json` | 研究与技能同输入同输出、逐臂风险/可行集一致、泄漏核对 |
| `independent_fit_review.json`、`independent_evaluation_review.json` | 协调方独立复算原件（原样复制，非本会话实现） |
| `budget_tolerance_diagnostic.json`、`sigma_cells_precision.json` | 预算 4 位存储精度复算与单元格精度改写记录 |
| `archive/` | 修正前的参数/评价、旧 REPORT 与首次失败运行诊断（保留，不作结论） |
| `registration.json` | 已注册版本 bc57defb… 的 versionId/origin/exposure/treeDigest、驻留技能列表、树完整性复核 |
| `installed_calls/` | 用安装版绝对路径的四次真实调用：命令、退出码、stdout/stderr、响应与 `results.json` |

复现顺序：

```bash
python3 scripts/readiness.py
python3 scripts/build_features.py
python3 scripts/fit_rule.py            # 一次主拟合（TRAIN 77 日）
python3 scripts/evaluate_rule.py       # VAL/TEST 统一评价（不重拟合）
python3 scripts/diagnose_budget_tolerance.py
python3 scripts/check_skill_equivalence.py
python3 scripts/build_case_calls.py
```

技能最小示例：`delivery/futures-execution-decision/examples/request_exante.json`（相对路径，包可搬迁）。

安装版实际调用（只读、`PYTHONDONTWRITEBYTECODE=1`）：

```bash
python3 installed_calls/run_installed_calls.py     # 重新跑四次调用并写 results.json
```

结果：示例 → `REFERENCE_BASELINE`/asap0；完成门槛 1.0 → `INPUT_REQUIRED`；CU 4%/D60 → `REFERENCE_BASELINE`/twap10（候选 shape10）；RB 1%/D5 → `MODEL_INFEASIBLE`。
