# 期货母单执行计划

给定一笔已经决定买卖的期货订单，这个 skill 比较立即释放全部数量（ASAP）、按时间均分（TWAP）和按历史分钟成交量分批（SHAPE）。它返回执行方法、计划释放时长、预计成本、完成率、价格风险和理由。输出用于研究判断。

**公开交付版 1.0.0** · 技能名 `futures-execution-decision`。包版本写在技能声明的 `metadata.version`。要复用完全相同的文件，请记录 `git rev-parse HEAD` 的提交号。QuantStudio 会给实际安装的文件另分配本机版本 ID。[技能声明](skills/futures-execution-decision/SKILL.md)

研究中，分批可以用更长等待换取更低的**模型**成本。真实冲击尚未标定，已测试的市场状态规则也未达到替换冻结基准的标准。报告用同一批订单量化了这两项判断。[最终报告与证据](research/REPORT.md)

## 安装 Python 依赖

准备 Git 和 Python 3.10 或更高版本。要在 QuantStudio 使用，请先在其**当前工作区**打开终端，再执行以下命令。独立使用命令行时可以选任意目录。

~~~sh
git clone https://github.com/zccajpe/futures-execution-regimes.git
cd futures-execution-regimes
python3 -m venv .venv
.venv/bin/python -m pip install -r skills/futures-execution-decision/requirements.txt
~~~

运行依赖只有 NumPy。下文命令都从仓库根目录执行。Windows 可使用 Git Bash，将 `python3` 换成 `py -3`，将 `.venv/bin/python` 换成 `.venv/Scripts/python.exe`。

## 跑通第一笔订单

下面两条命令先在新目录 `demo/` 创建合成分钟数据和一份请求，再用正式入口写出完整计划。演示可离线运行，无需行情或模型密钥。重新生成时请选择新的演示目录。

~~~sh
.venv/bin/python skills/futures-execution-decision/examples/make_demo.py demo
.venv/bin/python skills/futures-execution-decision/scripts/preference_plan.py --request demo/request.json --out demo/plan.json
~~~

这笔合成买单是 60 手。调用者要求在 15 个交易分钟内完成，并把成本放在首位。每分钟成交量固定为 100 手，模型容量为其中的 10%。ASAP 在第 1 分钟释放全部目标，仍需 6 分钟才能成交完。TWAP 每分钟释放 6 手，持续 10 分钟。两者都完成全部数量，模型成本分别为 6.30 和 4.30 bp。这里 1 bp 是名义金额的万分之一。均匀成交量使按量分批与同长度 TWAP 并列，程序返回 TWAP。

终端摘要应含 `plan_method="twap"` 和 `plan_h=10`。打开 `demo/plan.json`，可在 `plan.reason` 读到理由，在 `candidates` 对照其他方法。合成数据仅用于验证安装。使用自己的订单时，复制请求，填写手数、期限、风险预算，并将 `history_npz` 指向自己有权使用的分钟历史。相对路径从**请求文件所在目录**解析。[请求模板](skills/futures-execution-decision/examples/request.json) · [字段、数据格式与空计划处理](skills/futures-execution-decision/references/contract.md)

## 在 QuantStudio 安装和调用

以下使用 QuantStudio 0.1.36 的本地技能创作流程。先按上文把仓库克隆到当前工作区，并准备好 Python 环境和演示请求。

1. 将当前 Git 提交中的**完整技能目录**提取到一个新草稿。下面的命令只复制版本控制中的文件。若同名草稿已存在，请使用一个新的目录名，并在下一步填入相同的名字。

   ~~~sh
   mkdir -p ../quantskills-drafts/skill
   mkdir ../quantskills-drafts/skill/futures-execution-decision-1.0.0 && git archive HEAD:skills/futures-execution-decision | tar -x -C ../quantskills-drafts/skill/futures-execution-decision-1.0.0
   ~~~

2. 打开 **技能 → AI 创建技能**。在创作会话中粘贴下面这句话。

   > 请按原样导入当前工作区的 `quantskills-drafts/skill/futures-execution-decision-1.0.0`。该目录已经包含完整的 SKILL.md、脚本和冻结参数。直接用 `quantskills_asset_draft` 对此目录执行 `prepare`，展示确认卡。

3. 核对卡片上的 `skill-futures-execution-decision`，点击 **生成并开始**。如果只点了 **确认生成**，可以从 **技能 → 我的创建** 找到该技能并开始对话。
4. 在**新建的技能会话**中粘贴下面的调用示例。把请求路径换成自己的文件，即可处理其他订单。

   > 用当前会话已安装的 futures-execution-decision 读取工作区内 `futures-execution-regimes/demo/request.json`。用该仓库 .venv 中的 Python 运行技能的 `scripts/preference_plan.py`，把完整结果写到 `futures-execution-regimes/demo/quantstudio-plan.json`。告诉我选什么方法、计划释放多久、为什么。若没有可用计划，指出缺项或哪条约束未通过。

平台安装的单位是 `skills/futures-execution-decision/` 整个目录。技能许可随包分发。[包目录](skills/futures-execution-decision/) · [LICENSE](skills/futures-execution-decision/LICENSE)

## 核对研究或验证自己的安装

| 目的 | 入口 |
|---|---|
| 看哪种条件支持分批、证据有多大 | [最终报告](research/REPORT.md)及其段末的原始聚合表 |
| 看真实历史案例中的输入、选择和回退理由 | [十组固定调用记录](research/evidence/skill-cases.json)，其中的母单为研究构造 |
| 运行原有 111 项回归检查 | 先按[数据与测试说明](skills/futures-execution-decision/references/contract.md#回归测试)准备三份授权分钟数据，再运行下面的命令 |
| 查早期推演、批量取数和研究脚本的来源 | [整理前的固定提交](https://github.com/zccajpe/futures-execution-regimes/tree/98dc73bc73c162b3839981435dcd0c1b043457dc) |

~~~sh
.venv/bin/python -m unittest discover -s skills/futures-execution-decision/tests -v
~~~
