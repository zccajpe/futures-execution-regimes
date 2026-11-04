# 计划恢复：验证结果

日期 2026-11-04。本轮验证复用既有数据，不改数值内核（`decision.py`、`exante_core.py` 与冻结文件未改），已装 `fa49d2fe` 版本保留原样。

## 1. 单元测试

包根运行 `env PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/opt/python@3.13/bin/python3.13 -m unittest discover -s tests`，退出码 **0**，`Ran 111 tests ... OK`，0 failures / 0 errors。完整实际输出留在 `unittest_full_output.log`，逐项清单在 `unittest_verbose_output.log`。日志里的 `ResourceWarning: unclosed file` 来自 `decision.py` 既有的 `open()` 写法，是长期存在的告警，不影响结论，本轮未改。

本轮新增 8 个偏好用例（`tests/test_preference_plan.py::ConditionalSetWaitTests`），新增的基准边界用例已显式加 `wait_first`，因此偏好入口合计 35 项、总数 111 项。`SKILL.md` 与 `tests/validation_summary.json` 的项数说明已按实际 111 更新。

## 2. 固定 10 条 CLI 回放

命令统一为包根 `scripts/preference_plan.py --request ... --out ...`，**不带** `--use-jev`。请求在 `cases/requests/`，响应在 `cases/responses/`，命令、退出码与关键字段见 `cases/CASES.json`。10 条全部 `exit_code=0`、`api_attempts=0`、`jev_called=false`、`execution_action=NONE`、`production_eligibility=INSUFFICIENT`。`history_npz` 全部是已存在的绝对路径，本轮**没有取任何新数据**。

**旧 7 条全部不变。** 01 → `REFERENCE_BASELINE`/`choice=asap0`/`plan=asap·h0`/成本 5.8980；02 → `CONDITIONAL_MODEL_CHOICE`/`twap5`/`plan=twap·h5`/5.4502；03 → `CONDITIONAL_MODEL_CHOICE`/`shape30`/`plan=shape·h30`/5.3764；04 → 原 `choice=shape30`，`plan=asap·h0`/6.1627；05 → `INPUT_REQUIRED`/无计划；06 → `INSUFFICIENT_BASELINE`/无计划、`mode=baseline_first` 未变，`plan` 为空；07 → `CONDITIONAL_SET`/无计划。逐字段核对：`status`、`choice`、`method`、`h`、`detail`、`needed_input`、`a_range`、`segments`、各臂 `candidates` 指标、`effective_constraints` 与 `plan` 的方法/h/数值全部与 20261103 原输出相同。

**新增 3 条的实际结果。** 08（07 只把 `preference` 换成 `wait_first`）：`status=CONDITIONAL_SET`、`choice=null` 原样保留，`plan=asap·h0`、`predicted_cost_bp=null`，等待负担 13.52 最小——asap0/twap5/shape5/shape10 的 `wait_min` 同为 13.52417，正好走平局规则；完成率 1.000、价格项 SD 14.09bp，门槛与预算一行未改。09（08 再把 `a` 换成 `[0,2]`）：候选表、`effective_constraints` 其余字段与 07 完全一致，结果同为 `plan=asap·h0`、成本 `null`；区间分支仍然只给条件集合，`a` 没有被补成定值。10（06 只把 `mode` 换成 `compare`，原 06 的 `plan` 仍为空）：`status` 变为 `CONDITIONAL_MODEL_CHOICE`、`choice=asap0`、`plan=asap·h0`、成本 5.1884（费用 1.30 + 方向 0.00 + 冲击 3.89），完成率 1.000、价格项 SD 12.05bp，约束同样未改。

独立对照：用旧已装 `fa49d2fe` 跑同一份 08，输出仍为无计划（`old_installed_08.json`），说明新计划确实来自本轮改动，而不是环境或数据差异。

## 3. 怎么读这些结果

- **条件比较与相对 A 的增量必须分开。** 08/09 是 `compare` 模式下的等待口径计划，响应里的 `baseline_note` 写的是 model-only comparison，它们不涉及冻结基准 A，也不证明对 A 有增量。10 同样只是模式语义对照：它把 `baseline_first` 下「A 不可用 → 空计划」改成「`compare` 下可给条件计划」，既不是修复、也不是绕过冻结基准验证，不能当成对 A 的增量证据。
- **成本仍然未知。** 08/09 的 `predicted_cost_bp` 就是 `null`，不填 0、不填 0.5、不给区间；本轮没有取得 `a`，所以成本比较依旧只是条件集合。10 的成本 5.1884bp 是调用者给了 `a=0.5` 的既有口径结果，没有新数据来源。
- **哪些空计划保留。** 05（缺 D）与 06（基准单元不可用）仍然无计划；07（`cost_first` + `a` 未定）仍然无计划，本轮仍保留成本点选择对 `a` 定值的要求，未实现区间共同最优识别。08/09 的 `status`/`choice` 也仍是 `CONDITIONAL_SET`/`null`——新增的只是 `plan` 字段，不是改推荐。
- **口径限制。** 这 10 条是为对照而精选的演示案例：7 条给了计划（01–04、08、09、10），3 条仍是空计划（05 缺 D、06 基准单元不可用、07 成本目标缺 `a`）。任何比例都不能当作生产环境的空计划率；本轮也没有重跑完整历史、没有为拿到计划调参。

## 4. 本地注册与安装版核对

新版 `skill-futures-execution-decision@560f5c0cbec1eafb281e56ec87e4675f8e443dee` 已通过 QuantStudio 原生创作接口注册并驻留原研究会话（39 文件，668,780 字节）；旧版 `fa49d2fe` 保留。数值内核、冻结数据及其他驻留技能未改。

协调方对同一份 08 输入分别调用旧、新安装版：旧版无计划，新版为 `asap/h0`，成本仍为 `null`，原 `status/choice/candidates/effective_constraints` 一致，两个调用均为零 Jev、`execution_action=NONE`。见 安装版对照（未随公开仓发布；如需复现请在本地准备 `plan_recovery_20261104/installed_comparison.json`）、原生注册记录（未随公开仓发布；如需复现请在本地准备 `plan_recovery_20261104/native_registration.json`）与原研究会话驻留（未随公开仓发布；如需复现请在本地准备 `plan_recovery_20261104/resident_current.json`）。完整十组请求和响应见 [原始记录](RAW_INPUT_OUTPUTS.json)。
