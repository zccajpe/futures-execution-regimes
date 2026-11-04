# 数据准备

本仓库**不分发**任何 Pandadata 原始行情、分钟 NPZ/NPY 派生数组、数据库文件或账户/委托/成交回执。研究使用的是 PandaData 历史分钟量价，需要使用者自备该供应商的授权与访问权限；本仓库不含任何凭据，也不代替使用者取数。

因此：

- 只读脚本、`SKILL.md`、`references/`、`data/`（冻结参数与冻结基准）可以直接阅读；
- 任何真正跑一遍历史请求或历史接口测试的动作，都要求使用者先在授权环境里把下面描述的分钟历史准备到仓库内的同名相对路径；
- **没有这些数据的 clone 不能宣称"111 项历史接口测试可跑"或"整个研究可直接复现"。**

## 技能需要的历史分钟数据格式

技能入口 (`delivery/futures-execution-decision/scripts/`) 从 `.npz` 读取一个二维历史网格，必须包含以下 5 个键：

| 键 | 形状 / 类型 | 含义 |
|---|---|---|
| `dates` | `(N,)` 字符串 `YYYYMMDD`，升序、无重复 | 交易日，要求**释放日之前最近 20 个交易日** |
| `labels` | `(M,)` 字符串 `HH:MM`，无重复 | 分钟标签；必须包含请求里的 `release_label`，且其后再有至少 60 个标签 |
| `close` | `(N, M)` 浮点 | 逐分钟收盘价，历史行不得含 NaN/inf |
| `volume` | `(N, M)` 浮点 | 逐分钟成交量，历史行不得为负 |
| `mask` | `(N, M)` 布尔 | 该分钟是否有有效数据 |

读取时会校验形状一致性、标签/日期唯一性与升序、以及 `release_label` 之后是否还有 60 个交易分钟；缺一项就返回 `INSUFFICIENT_DATA` 并指出缺什么，不会换品种或换窗口顶替。

`exante` / `volume_forecast` 两个模式另有要求：NPZ 需包含释放日当天（`dates` 里要有 `release_trading_date`），且当天释放后的分钟为空或无效（只读释放时点及以前的信息）。

## 当前历史测试需要的 3 个 fixture

技能包的 `examples/` 下需要以下 3 个文件（与 `tests/` 里的请求一致，均为相对路径）：

| 文件 | 用途 | 覆盖 |
|---|---|---|
| `examples/RB_am_20d.npz` | 基础 20 日历史网格 | RB 上午时段，135 个标签（09:01–11:30） |
| `examples/RB_am_20d_20260701_prefix.npz` | `exante` 释放前缀示例 | 21 行，含释放日当天 |
| `examples/RB_am_20d_20260803.npz` | `compare` / `baseline_first` / 偏好入口 | RB 位于 20260706–20260731 的 20 个交易日 |

三者都是**使用者自行准备**的内容：从你已获授权的 PandaData 分钟数据里，按上述口径（释放日前最近 20 个交易日、每日与释放标签对应的后续 60 个交易分钟）导出，写成同名 `.npz` 放在 `delivery/futures-execution-decision/examples/` 下即可，无需修改任何脚本或请求。

`.gitignore` 已排除 `*.npz` / `*.npy` 以及 `data/history/`、`raw/datasets/` 等目录，所以本地准备的文件不会被误提交。

## 研究侧数据（不可由本仓库重建）

`scripts/` 下的取数/对齐脚本面向下面这些本地中间物，它们同样**不在**本仓库内：

- 取数日志与索引（`raw/datasets/` 下的结果行与 `index.json`）；
- 派生分钟块与回放数组（`raw/derived/*`、`results/replay_*.npz`、`results/scenarios_*.npz`）；
- 训练/验证/留出切分与冻结参数的上游产物。

这些路径在 `ENGINEERING.md`、`DATA_COVERAGE.md` 与各研究目录的设计文档里有说明。缺这些中间物时，研究脚本会因文件缺失而失败——这是设计上的显式失败，不做静默回退。仓库里保留的是**结果表与文档**（`results/*.csv`、`decision_research/*.csv`、各目录的 `*.md` / 评价 `*.json`），不是生成它们所需的原始行情。

## 供应商与授权

- 数据来源为 **PandaData**，受其服务条款与使用授权约束；本仓库不转授任何数据权利。
- 本仓库不包含 API key、token、私钥、账户快照、委托/成交回执或私有会话配置。
- 复现时请在**你自己的**授权环境中取数；不要把凭据写进请求文件、脚本或提交物。

## 最小自检（不依赖任何行情）

在没有准备 NPZ 的环境里，可以确认代码本身可用：

```bash
cd delivery/futures-execution-decision
python3 -c "import numpy; print(numpy.__version__)"   # 核心依赖
python3 scripts/decision.py --request examples/request_compare.json --out /tmp/x.json
```

第二条命令在没有 `RB_am_20d_20260803.npz` 时会返回 `status=INSUFFICIENT_DATA`、`detail` 指出 `history_npz not found`，并仍带 `execution_action=NONE`。这正说明：**接口可用，但历史结论需要真实数据才能复现。**
