# 取数入口：如何复用平台已有能力

本文件说明研究脚本怎样取数、真实限制是什么。**没有开发任何新接口、没有改平台代码、没有碰凭据。**

## 1. 用的是哪个入口

| 环节 | 位置 | 作用 |
|---|---|---|
| 远程方法 | `packages/panda-mcp/src/index.ts:238` `@Remote databaseFetch(input): Promise<DataSummary>` | 平台公开的原生远程方法 |
| 落库与分块 | `packages/panda-mcp/src/database.ts` `LocalDatabase.fetch` → `sourceRows` | 取数、必要时按日期递归分块、写本地数据库 |
| 客户端 | `~/.local/share/quantstudio-headless/client.mjs` `export rpc(config, endpoint, args)` | 只读复用，未修改 |
| 配置 | `~/.local/share/quantstudio-headless/config.json` | 指向本会话的本地服务；不读 token |

研究脚本 `scripts/fetch_min.mjs` 只做三件事：拼参数、调用 `rpc(config, 'pandaMcp/databaseFetch', { input })`、把返回的**摘要**写进结果文件。

## 2. 平台源码里确认的行为

- MCP 对 `get_future_min` 返回被截断的预览时，`sourceRows` 会把日期区间二分递归，直到每段都不再被截断，再合并。
- 递归预算 64 次；合并后单数据集上限 **100,000 行**、序列化后 **10 MB**。
- 截断的预览**不会被当作完整历史写入**；分块失败则整次取数失败，不留半份数据。
- 返回值是 `DataSummary`（id、行数、列、时间范围、字节数、有效期），**不含全部行**，因此不会把数据灌进模型上下文。
- `get_future_daily` 等非分钟方法不分块：一次请求超过 1,000 行仍会被截断，所以这些方法按合约逐个请求。

## 3. 实测的调用特征

| 项目 | 实测值 |
|---|---|
| 单数据集行数 | 60 ～ 3,105 行，最大 0.66 MB（按"品种 × 合约 × 月 × 窗口"切块） |
| 单次调用耗时 | 0.5 ～ 4.7 秒（p95 约 4 秒） |
| 并发 | 2（客户端 20 秒超时，留足余量） |
| 失败率 | 0 / 558（三次批量合计，见 `PROGRESS.md`） |
| 单次响应的上下文成本 | 约 200 字节（只有摘要） |

## 4. 为什么按"品种 × 合约 × 月 × 窗口"切

- **按合约切**：不同合约的价格不能拼接，跨换月的价格跳变会污染分钟收益。
- **按窗口切**：日盘上午、下午、夜盘首小时各自成块，夜盘单独研究，不与日盘混算。
- **按月切**：单块控制在几百 KB，便于按需读取与局部重取；也避免触碰 10 MB 上限。
- **按上一交易日主力选合约**：当日主力由当日成交决定，用它选当日合约就是未来函数。

## 5. 结果怎么落、怎么复现

```
output/futures-execution-regimes-20261021/
  raw/datasets/results.jsonl   每次调用的结果（成功带 id/行数/时间范围，失败带错误）
  raw/datasets/index.json      任务名 → 数据集 id
  raw/reference/dominant_map.csv  品种 × 日期 → 当日主力、可交易合约、换月标记
  raw/reference/coverage.csv      每个数据块的行数、交易日数、网格检查结果
  raw/contract_metadata.json      74 份合约的乘数、保证金、时段、上市退市
  notes/data_checks.json          逐块结构核查
```

复现命令：

```bash
python3 scripts/make_minute_tasks.py 202511 202603 --out /tmp/tasks_min_q1.json
node scripts/fetch_min.mjs /tmp/tasks_min_q1.json raw/datasets/results.jsonl --concurrency 2
python3 scripts/coverage_report.py
python3 scripts/inventory_probes.py
```

`fetch_min.mjs` 会跳过 `results.jsonl` 里已成功的任务，因此中断后重跑同一条命令即可续接。

## 6. 真实限制

1. 客户端 `rpc` 超时 20 秒，**不能按整年一次请求**。按月或按契约段切块是硬要求，不是偏好。
2. 单数据集 10 MB 上限：约 5 万行 1 分钟数据。本研究的块远低于此。
3. 平台数据库会按有效期清理：本次统一设 `ttlSeconds = 31536000`（365 天，上限值），但**仍需把研究用到的行转存为工作区内的简表**，不能假设数据库永久保留。
4. 没有盘口/逐笔数据，任何"成交概率""排队位置"都无法从这批数据得出。
5. `time_zone` 过滤按**交易日**生效：夜盘数据虽然发生在上一自然日晚上，但只要 `trading_date` 落在请求区间内就会返回。

## 7. 价格代理（2026-10-21 复核后定稿）

- **主实验统一用 bar close 作分钟成交价代理**，到达价同样是释放那根 bar 的 close。它仍不是真实成交价。
- 成交额推导均价（amount ÷ volume ÷ multiplier）只作为敏感性代理，且只在"整段窗口每一根 bar 都通过 OHLC 一致性检查"的案例上使用。
- 数据核查结果：TA 有 **69,920 根** bar 的推导均价落在 [low, high] 之外（占 85.8%），其它 11 个品种合计仅 **142 根**（≤0.07%）。TA 的首根实例：OHLC 4596/4600/4592/4592、V=3814、amount=87,912,700、乘数 5 → 推导均价 4,610.84，已出区间；整块 ratio 约 0.297–1.584。原因未查明（不是单位错、不是 bar 移位），因此**判定成交额字段不可靠，主实验不用该字段**。
- 实现上：`assemble.py` 同时保存 close、high、low、volume、`vwap_raw`、`vwap_valid`、`mask` 与每根 bar 的合约。`mask` 表示"bar 存在且价格/量合法"，与 `vwap_valid` 相互独立；均价字段坏不会删除有效的分钟价格与成交量。
- 重复与冲突：跨全部数据块按 (symbol, trading_date, datetime) 全局去重，2026-10-21 复跑结果：0 重复、0 冲突。
- float64 存储；每块按真实交易时段生成期望网格，缺格 0；零成交格 163 个，保留为真实 0（不删）。

## 8. 哪些旧结果受影响

- 旧 `replay_train_val.npz`（TA 用 close、其它品种个别 bar 退为 close）已被本轮重算替换；旧的选择器与状态表结论作废。
- 旧 `INTERIM_REPORT.md` 的数字已标注失效，等待重写。


## 9. 留出流程的实现 bug 与修复（2026-10-21）

- **现象**：留出期 `selector_decisions_test.csv` 407,232 行全部 `NO_*`，0 条推荐。
- **原因**：`selector.py` 用当前输入的情景文件里"训练期日期"计算风险预算的波动尺度；测试文件只含测试日期，训练集为空，预算变成 NaN，`risk <= NaN` 恒为假，于是所有母单被判为不可行。这与市场状态无关，是实现错误。
- **修复**：新增 `--frozen-train-input results/fixed_h_train.csv`，直接读取训练期已冻结的 h、波动尺度、预算与不可行原因；测试不再重算。加入有限性校验：任一组的预算缺失或非正数直接报错退出。
- **核验**：6,100 个训练组与冻结表逐组比较，h 与 σ 的差异 0 处；测试全部组预算有限且大于 0（最小 1.21bp，最大 124.91bp）。
- **影响**：旧留出结果表全部作废（`results/README.md` 已标注），本轮按同一规则、同一批数据、同一参数重算，没有更改样本、费用、冲击、模型或规则。

## 10. 已作废的入口

- `scripts/final_gap.py` 的 `group_table` 使用手写混合进制键，标签解码错误；其输出已被 `fix_math.py` 的 `state_by_product.csv` 取代。
- `scripts/final_tables.py` 的 `fourway_metrics` 风险区间实为均值差区间，已由 `fix_math.py` 的 `multiobjective.csv` 取代。
- `INTERIM_REPORT.md` 与 `results/` 下旧 frontier/pairs/state 系列表为早期版本，仅作历史记录，不作结论。

## 11. 脚本输入输出关系

| 脚本 | 读 | 写 | 是否重取数据 |
|---|---|---|---|
| `build_reference.py` | 缓存主力表 | `raw/reference/dominant_map.csv` | 否 |
| `assemble.py` | `raw/datasets/*`、`raw/tasks/*` | `raw/derived/bars_*.npz` | 否 |
| `run_replay.py` | `raw/derived/*` | `results/replay_*.npz` | 否 |
| `add_t60.py` | 上述 npz + 分钟数组 | `*_t60.npz` | 否 |
| `scenario.py` | `raw/derived/*` | `results/scenarios_*.npz` | 否 |
| `selector.py` | 情景 + 固定 h | `selector_decisions_*.csv` | 否 |
| `fix_math.py` | 回放 npz + `state_cases.csv` | `state_by_product.csv`、`multiobjective.csv`、`state_verify.json` | 否 |
| `build_delivery_tables.py` | 回放 npz + 决策 csv + 固定 h | 配对指标、案例表、翻转阈值、代理翻转 | 否 |
| `final_gap.py`、`final_tables.py` | — | — | **obsolete，不得调用** |

完整重建（含取数）需要 PandaData 会话；重建分析只需上表前八行中已交付的 npz 与 csv。

## 5. 当前入口（2026-10-30）

取数链路没有变化；本轮把"怎么调用研究结论"集中到技能包：

| 想做什么 | 入口 |
|---|---|
| 给定母单与历史 NPZ，比较 ASAP / TWAP / SHAPE | `delivery/futures-execution-decision/scripts/decision.py`（`mode=baseline_first` / `compare`） |
| 只用释放前信息做条件化预测 | 同上，`mode=volume_forecast` |
| 事前选型研究候选（未启用） | 同上，`mode=exante`；证据边界见 `references/evidence.md` |
| 复现本轮契约修订的本地场景 | `skill_demo_20261030/run_demo.py`（7 个场景，纯本地） |

所有输出带 `execution_action=NONE`、`production_eligibility=INSUFFICIENT`；不连接交易接口、不读凭据。

数据口径可以用 `scripts/verify_dataset_counts.py` 一键复核（读 `raw/datasets/results.jsonl`、`index.json`、`raw/derived/bars_*.npz`，写 `notes/data_counts_20261030.json`）。
