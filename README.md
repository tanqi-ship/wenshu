# wenshu —— 银行级智能问数引擎（学习项目）

一条主链路：**一句话问题 → Intent → 编译器展开 SQL → 校验 → 出数。**

> **定位**：这是**学习与工程实践**项目，不是产品。
> 目标是把「问数引擎为什么会不准」这件事拆到底，并给出可验证的答案。
>
> **当前状态**：M0、M1、M1.5、M2（6/6）、**M3 离线问数闭环（4/4）**及 **M4 离线评测（4/4）**已完成；
> **M5.1 真实模型基线已运行一次（2026-11-27），结果见 docs/CONTEXT.md**。
> 编译器能从 `Intent` 产出通过校验的 SQL（单指标 / 月末时点或区间 / 维度分组 / 业务过滤 /
> 行级权限 / **双时点 delta 对比** / **每月 Top N** / **双表 INNER/LEFT JOIN** / **整月报表读取与多月 UNION ALL** / **同口径整体占比**），并且有一条命令能回答"引擎现在算得对不对"：
> `uv run python -m scripts.check_golden`。最新测试与评测结果见 `docs/CONTEXT.md`。
> **尚未实现**：同比 / 环比 / 变化比率、全局 Top N、多跳 JOIN；真实模型基线只覆盖 30 条规范问法、单模型单版本，真实用户准确率仍未验证。见 `docs/ROADMAP.md`。

---

## 一、范围：做什么，不做什么

范围是这份文档里最重要的部分。**边界一旦模糊，项目就会变成第二个 askdb。**

### ✅ 做

| # | 模块 | 一句话 |
|---|---|---|
| 1 | **语义层** | 手写 5~10 个指标 / 3~5 个维度的完整定义（指标公式、口径、码值、表关系） |
| 2 | **编译器** | `Intent + 语义层 + Scope → sqlglot AST → 方言 SQL`，**0 次模型调用** |
| 3 | **校验层** | 业务规则写成可单测的纯函数，每条有稳定 problem code |
| 4 | **可答性门禁** | 由语义层的**显式存在性**判定，不用向量距离阈值 |
| 5 | **澄清** | 声明式槽位清单 + 追问预算 |
| 6 | **评测** | 黄金集：手工 SQL 对照 + `why_hard` 强制项 |

### ❌ 不做（明确不做，不是"以后做"）

| 不做 | 理由 |
|---|---|
| 前端 / 管理后台 | 与"问题→SQL"零关系，且引擎输出形态还在变 |
| 用户 / 角色 / 登录 / JWT / RBAC 表 | 权限只做 `Scope` 输入对象，不做权限系统（见 ADR-0004） |
| 场景管理 / 知识库管理 / 提示词后台 | 那是产品功能 |
| 多数据源连接器 | 只 SQLite（默认）+ 可选 PostgreSQL（见 ADR-0006） |
| 归因分析 / 图表 / 指标卡 / 明细导出 | 下游展示，不属于引擎 |
| 迁移 askdb 的任何代码 | 见 ADR-0003。askdb 只作为**阅读标本**，见 `docs/REFERENCES.md` |

---

## 二、架构（L0~L5 六层流水线）

```
                    ┌─────────────────────────────────────────┐
   L0  输入边界      │  语义层（知识：指标/维度/码值/表关系）      │  ← 数据，可配置
                    │  Scope （权限：表/列/行策略）              │  ← 输入参数
                    └─────────────────────────────────────────┘
                                     ↓
   L1  理解          question ──LLM──> Intent                     ← 唯一一次模型调用
                                     ↓
   L2  门禁          可答性判定：Intent 的每个槽位能否在语义层落地？
                        ├─ 全部落地 → 编译
                        ├─ 部分落地 → 澄清（只问缺的那个）
                        └─ 指标不存在/无权限 → 拒答（原因可解释）
                                     ↓
   L3  编译          Intent + 语义层 + Scope → AST → 方言 SQL     ← 0 次模型调用
                                     ↓
   L4  校验          SQL 文本 → Problem[]  （0 次模型调用）              ← 与 L3 独立
                                     ↓
   L5  执行          SQL → rows（只读、行数上限、超时）
```

**三个必须记住的架构约束：**

1. **SQL 只在 L3 一处生成。** 因此权限只能在 L3 一处注入、校验只需在 L4 一处复核。
2. **L3 和 L4 必须互相独立。** L4 不复用 L3 的任何中间产物，否则编译器的 bug 会被自己的校验放过。
3. **L1 是唯一允许不确定的地方。** 它的输出被 L2 收窄成一个有限、可枚举的结构。

细节见 `docs/ARCHITECTURE.md`。

**M2.4 报表边界**：`ReportDef` 显式登记指标、月份分表和固定维度；不替换明细公式。
`source_mode=report/union` 按去重后的月份数生成 SELECT 或 UNION ALL，直接读取预计算值，
不 JOIN、不重新聚合、不乘 100。缺月份配置或任一表列权限即整条拒绝；空行策略保留 `1=0` 分支。
当前产品不良率样例登记 202604~202606，更多月份须先补知识配置。
**M2.5 占比边界**：新增独立 `Intent.share=ShareSpec(dimension="产品")`，只支持单明细表、单个月末、
一个拆分维度和普通可加总 SUM。时间/机构上下文/行权限在聚合前生效，整体窗口算完才筛选展示对象；
同列的产品业务过滤不能带走产品行权限。输出原金额、`total_value` 和小数 `share`，零分母返回 NULL、不乘 100。
`R-RATIO-001` 独立检查三层结构及显式行策略；具体业务映射仍由知识和黄金/结果测试保证。
**M2.6 机构边界**：机构全称、登记简称和编码只作精确解析（仅 trim 两端空白），未知、歧义、
缺配置明确拒绝，不扩展前缀或下辖机构。`R-ORG-001` 独立拦截 LIKE、函数变形和派生列洗掉保护；
正常 JOIN 等值键、编号分组、名称展示和 Scope 行权限保持不变。普通 coded 维度仍沿用旧契约。
**M3.1 抽取边界**：`extract_intent(question, catalog, *, llm)` 只调用一次注入模型，严格验证 JSON/字段/日期后返回 Intent 与原始 Completion。
最小上下文只含业务名、同义词、单位和默认口径键，不发送公式、表列、码表或权限。
缺失/未知不补全，多指标或未解决片段明确报错；不声明可答、不编译或执行 SQL。
本轮通过 FakeLLM 和 20 条手写固定回放验证契约，未调用真实模型；不代表真实模型准确率、成本或延迟已验收。
静态提示词 1093 字符，样本完整消息 1541～1558 字符，**未达到 ≤300 字的精简目标**，这些数不是 tokens。
**M3.2 澄清边界**：`prepare_question(question, catalog, *, llm)` 在同一次抽取后，用显式指标/时间槽位检查返回
`needs_clarification` 或 `slots_complete`。缺信息只问一个，多指标要求指定一个；unresolved 只引用首片段，
不猜分类、保留全部片段。技术错误原样传播，不二次调用模型、不写回默认值。
`slots_complete` **只说明必需槽位不缺**，不等于可答、可编译、有权限或有数据；仍需下游 L3/L4。
20 条旧固定回放未改，新增独立准备层预期；澄清是新演示的正常结果，退出码为 0。
**M3.3 会话边界**：`ClarificationService` 注入存储与模型；内存适配器用于测试，SQLite 文件保存跨请求快照。
只补当前缺失的指标/时间，其他条件保持；多指标/unresolved 要求完整重述。默认最多发两次追问，最后一次有效补答仍可完成，
仍缺信息才停止。版本 CAS 防止覆盖并发更新，但不承诺并发模型调用恰好一次；技术失败不推进状态。
会话不保存 Scope/密钥/连接，owner_key 与目录版本由可信调用方提供，不等于登录/权限系统。
10 组固定手写会话、14 份新 cassette 和独立进程恢复测试通过；本地状态读写**不是 L5 业务查询执行**。
专用补答静态指令 717 字符，固定样本消息内容合计 1398～1490 字符，不是 tokens；旧 prompt 精简目标仍未达。
**M3.4** 已将会话接到编译、校验与 SQLite 业务只读执行：只有 L4 无问题才执行，默认最多 100 行、查询预算 2 秒。
**M4.1** 在问数入口把未登记指标归为 `UNANSWERABLE`，指标/表/列无权限归为 `OUT_OF_SCOPE`，两类拒绝不进入 L4/L5。
**M4.3/M4.4** `scripts.evaluate` 一次跑完固定回放评测并按阶段归因失败。真实模型基线见下一条。
**M5.1** 真实模型基线已运行一次（2026-11-27，run `20261127T064047Z-e239b55`；T0～T4 已合并）：前置检查、
记录型客户端与预算、共享判定、四份报告；结果与归因见 `docs/CONTEXT.md` 第十一节。


---

## 三、三条必须从第一天遵守的纪律

### 1. CI 里绝不调用真实模型

`app/llm/` 三个实现由 `WENSHU_LLM_MODE` 选择：

| 模式 | 实现 | 用在哪 |
|---|---|---|
| `fake` | `FakeLLM` 按脚本返回 | 单元测试 |
| `replay` | `ReplayLLM` 回放 cassette | CI / 黄金集门禁 |
| `real` | `OpenAICompatLLM` | 本地手动验证 |

回放**找不到记录时抛 `ReplayMiss`，绝不联网**。否则 CI 会在某天悄悄开始调真实模型，慢、花钱、结果不可复现，而流水线还是绿的。

### 2. 规则进代码，不进 prompt

`app/engine/validation/rules.py`、`ratio.py` 与 `org.py` 的 8 条规则，每条都有：稳定 problem code + 规则编号（`C-xx` / `R-xxx`）+ 单测。

这不是凭空的偏好。NVIDIA auto-ontology 在它的 ReAct 提示词 docstring 里给出了他们的实测结论：

> "rule blocks in the writer prompt **lost accuracy every time they were measured**"
> —— `auto_ontology/retrieval/text_to_sql/react/prompts.py:7-10`

**注意边界**：那是他们在「ReAct + 策略训练」语境下的结论，原句后半段是 "a policy being trained should learn conventions from reward, not from a list"。**本项目把它当假设，不当定论**——验证方式是黄金集评测（M4）。

### 3. 重试必须携带原 SQL

`ValidationResult.feedback()` 只生成问题清单，**原 SQL 必须由重试构造器一起回灌**。

askdb 的重试只回传错误信息、不回传原 SQL，而 prompt 里却写着"优先在原 SQL 结构上做局部修复"——模型看不到原 SQL，所谓"修复"实际退化成重新抽签。

---

## 四、快速开始

```bash
uv sync --locked --group dev
uv run pre-commit install                      # 每台电脑装一次本地提交钩子；首次提交会联网下载钩子环境
uv run pytest                                # 不需要外部数据库 / 模型 / 网络
uv run ruff check .                          # All checks passed
uv run python -m scripts.demo_ask            # 固定问题桩：问题 → Intent → SQL → 校验
uv run python -m scripts.demo_extract --case EX-001  # 离线抽取：严格回放 → Intent，不执行 SQL
uv run python -m scripts.demo_prepare --case EX-008  # 离线准备：缺时间时只问时间，正常退出 0
uv run python -m scripts.demo_prepare --case EX-010  # 多指标请求指定一个，正常退出 0
uv run python -m scripts.demo_session start --case SS-001 # 保存“渝北的贷款余额”，返回会话 ID 和版本
uv run python -m scripts.check_golden        # 黄金集门禁：引擎产出 vs 手写黄金 SQL
uv run python -m scripts.evaluate            # 离线评测：抽取/澄清/黄金/执行 + 失败归因
uv run python -m scripts.trace_compile       # 学习工具：把编译器五步逐个打出来
```

真实模型基线是人工步骤，需要密钥，不在 CI 里跑：

```powershell
$env:WENSHU_LLM_MODE = 'real'
$env:WENSHU_LLM_BASE_URL = 'https://api.deepseek.com/v1'
$env:WENSHU_LLM_MODEL = 'deepseek-flash'
$env:WENSHU_LLM_API_KEY = Read-Host 'DeepSeek API Key'
$env:WENSHU_LLM_THINKING = 'disabled'
uv run python -m scripts.evaluate_real --case EX-001 --repeats 1   # 冒烟
uv run python -m scripts.evaluate_real                            # 全量
```

`eval/real/<run_id>/` 是运行产物，已忽略、不提交；结论写回 `docs/CONTEXT.md`。

会话演示默认写入忽略目录 `build/clarification_sessions.db`。复制 start 返回的 `session_id` 后，
可在新的终端/进程中继续（将下方占位符替换成真实 ID）：

```powershell
.\.venv\Scripts\python.exe -m scripts.demo_session reply --case SS-001 --session-id <会话ID> --expected-version 1
.\.venv\Scripts\python.exe -m scripts.demo_session show --session-id <会话ID>
```

SS-001 补答固定为“2026年4月末”，最终 Intent 保留“渝北”机构过滤。所有演示仅严格回放；
show 零模型调用，澄清/停止是正常协议结果，版本冲突和技术错误返回非零。
CLI 的固定 owner 只用于本地演示，尚无 HTTP 接口或用户认证。

离线问数闭环（造数命令只运行一次，不覆盖现有文件）：

```powershell
uv run python -m scripts.seed_sqlite_business
uv run python -m scripts.demo_query start --case AQ-001
uv run python -m scripts.demo_query start --case AQ-001 --scope B001
uv run python -m scripts.demo_query start --case SS-002 --scope B002
uv run python -m scripts.demo_query reply --session-id '<上一步的 session_id>' --expected-version 1 --scope B001
```

AQ-001 全行返回 `20210000` 元，`B001` 范围返回 `12660000` 元。SS-002 首轮缺指标，固定补答为“贷款余额”，
以补答当次的 `B001` 权限执行。`build/business_fixture.db` 是只读业务测试库；`build/query_sessions.db` 仅保存会话。
默认最多返回 100 行，超限通过 `truncated` 标记，查询预算 2 秒；SQL 校验未通过时不执行。

**测试包含 SQLite 内存结果验证，不需要外部数据库。** 双表关联默认要求维表键唯一，
基数声明不是数据质量检查；`None` Scope 不限制，显式空 Scope 拒绝访问。JOIN 暂不与 delta/Top N/report/union 组合。

**fixture 数据库默认用 SQLite，零依赖**（ADR-0006）。PostgreSQL 只在需要方言 / EXPLAIN 验证时可选启用。

---

## 五、文档索引

| 文档 | 看什么 |
|---|---|
| **`AGENTS.md`** | AI 和新会话的统一入口：读取顺序、硬边界、常用命令和完成标准 |
| **`docs/CONTEXT.md`** | ★ **新会话/新同事先读这一份**：项目是什么、做到哪了、我们在替代 askdb 的哪一步、怎么跑、踩过哪些坑 |
| `docs/DEVELOPMENT.md` | 双机切换、分支、提交、PR、测试门禁和 Definition of Done |
| `docs/EVALUATION.md` | 测试与评测的区别、黄金集纪律、指标定义和真实模型评测边界 |
| `docs/GLOSSARY.md` | 项目术语统一表：架构、语义层、模型、评测、本体和工程流程 |
| `docs/ARCHITECTURE.md` | 分层、数据流、编译器内部结构、错误分类、`Scope` 三个注入点 |
| `docs/DECISIONS.md` | 已定的架构决策（ADR），每条都写了"什么情况下翻案" |
| `docs/ROADMAP.md` | M0~M5 里程碑，每步的验收标准和对应评测用例 |
| `docs/REFERENCES.md` | 读 askdb / auto-ontology **该读哪些文件**（学习地图，不是迁移计划） |
| `ATTRIBUTION.md` | 代码来源与设计参考登记 |
| `fixtures/sql/bank_fixture.sql` | 故意"脏"的测试库，文件末尾列了 D-01~D-11 共 11 项缺陷 |
| `eval/cases/*.yaml` | 评测集，每条必须有 `why_hard` + `covers` |
| `eval/golden/` | 黄金集：`cases.yaml`（用例 = `Intent` + 指向黄金 SQL）+ `G-xxx.sql`（**手写**标准答案） |

---

## 六、目录结构

```
wenshu/
├── app/
│   ├── engine/
│   │   ├── semantic/       # L0 语义层领域模型（Intent / MetricDef / DimensionDef / RelationDef）
│   │   │                   #    + bank_mini.py：演示与测试共用的唯一一份假语义层
│   │   ├── compiler/       # L3 编译器（0 次模型）：__init__ 装配 / time / metrics / conditions / comparison / topn / sources / permissions / reports / report_time / share
│   │   ├── validation/     # L4 校验层（只读 / 名字 / 范围 / 8 条业务规则，含独立占比/机构规则）
│   │   ├── understanding/  # M3.1 严格单轮抽取 + 最小业务上下文
│   │   ├── clarify/        # M3.2单轮准备 + M3.3 session/跨请求状态、存储、补答合并
│   │   ├── retrieval/      # 占位：只有职责说明，尚无代码
│   │   └── coverage/       # 占位：只有设计说明，尚无代码；已实现的可答性分类在 query.py
│   ├── llm/                # 模型访问边界：Protocol + fake / replay / real
│   └── config.py
├── eval/cases/             # 评测集（yaml，带 why_hard / covers）
├── eval/golden/            # 黄金集：cases.yaml + G-xxx.sql（手写标准答案）
├── fixtures/sql/           # 假银行库 DDL + 缺陷清单 D-01~D-11
├── scripts/                # demo_ask / check_golden / golden / evaluate_real / seed_fixture / trace_compile …
├── tests/                  # engine 单测（纯内存）+ cassettes
└── docs/                   # 上下文 / 架构 / 决策 / 路线图 / 参考地图
```

---

## 七、这个项目要回答的问题

不是为了"做一个问数系统"，而是为了回答这四个问题，每个都要有数据：

1. **把 SQL 从模型手里拿走，准确率能提高多少？** → 黄金集对照（M4）
2. **哪些业务规则可以代码化，哪些只能留在 prompt？** → `validation/rules.py` 的边界
3. **问数到底需要多少次模型调用？** → 编译路径 0 次，理解路径 1 次
4. **"答不了"和"没权限"能不能分开告诉用户？** → `Scope` + 可解释拒答

回答不出来的部分，会如实写成"未验证"，而不是当成结论。
