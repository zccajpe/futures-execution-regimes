# 成交量活跃度研究（volume_activity_20261022）

三关结构（见 `DESIGN.md`）：Stage 0 选择空间 → Stage 1 单信号预测增量 → Stage 2 执行增量。
当前进度：Stage 0 完成（rev1）、Stage 1 通过预测门、Stage 2 完成但未过执行门（`INCOMPLETE_COVERAGE`）。技能落地与注册未做，等待复核。

| 文件 | 内容 |
|---|---|
| `DESIGN.md` | 三关规格、停止条件、`act_pre` 复用定义 |
| `STAGE0.md`、`choice_space*.json/csv` | 选择空间核查（门：READY_FOR_FORECAST_REVIEW） |
| `REVIEW_CORRECTIONS.md`、`archive/invalid_stage0_1/` | 第 1 轮实现修正记录与作废输出 |
| `STAGE1.md`、`params_stage1.json`、`forecast_events.csv`、`forecast_evaluation.json` | 活跃度预测增量（门：READY_FOR_EXECUTION_REVIEW） |
| `STAGE2.md`、`execution_decisions.csv`、`outcomes.csv`、`execution_evaluation.json` | 执行增量（判定：INCOMPLETE_COVERAGE） |
| `independent_stage0_review.json`、`independent_stage1_review.json`、`independent_stage2_review.json` | 协调方独立复核原件（原样复制） |
| `STAGE2_CORRECTIONS.md` | Stage 2 口径修正记录（时长解析、全集完成率、拒绝组解释、OOS 案例） |
| `registration.json`、`registration_*.json` | 已注册版本 097a1374… 的注册/驻留/最终复核原件与验证状态（truthful，含未运行的检查） |
| `installed_calls/` | 安装版绝对路径的 5 次真实 CLI 调用：D60/D5 预测、ACT 拒绝、缺数据拒绝、原 exante 参考 |
| `skill_calls/` | 工作区技能实际调用：预测正常、缺数据拒绝、ACT 选型拒绝、原 exante 兼容、四个 Stage 1 案例对账 |
| `scripts/` | `stage0_choice_space.py`、`stage1_activity_forecast.py` |

复现：

```bash
python3 scripts/stage0_choice_space.py      # Stage 0，约 21 秒
python3 scripts/stage1_activity_forecast.py # Stage 1，约 1.5 秒（一次拟合 + 一期评价）
python3 scripts/stage2_execution_value.py  # Stage 2，约 28 秒（一次执行评价）
python3 scripts/fix_stage2_flags.py        # 口径修正后处理（不重选）
python3 scripts/run_stage_skill_calls.py   # 工作区技能实际调用与四案例对账
```

边界：VAL/TEST 都已看过，全部结果只是时间顺序上的回顾核验；未做 Stage 2、未改技能与数据。
