# wenshu —— 银行级智能问数引擎（学习项目）

一条主链路：**一句话问题 → Intent → 编译器展开 SQL → 校验 → 出数。**

> **定位**：这是**学习与工程实践**项目，不是产品。
> 目标是把「问数引擎为什么会不准」这件事拆到底，并给出可验证的答案。
>
> **当前状态**：M0 / **M1 最小闭环** / **M1.5 黄金集门禁** / **M2.1 双时点对比** / **M2.2 每月 Top N** / **M2.3 受限双表 JOIN** / **M2.4 报表 UNION ALL** 完成 ——
> 编译器能从 `Intent` 产出通过校验的 SQL（单指标 / 月末时点或区间 / 维度分组 / 业务过滤 /
> 行级权限 / **双时点 delta 对比** / **每月 Top N** / **双表 INNER/LEFT JOIN** / **整月报表读取与多月 UNION ALL**），并且有一条命令能回答"引擎现在算得对不对"：
> `uv run python -m scripts.check_golden`（18 条手写黄金用例，18/18 通过，覆盖 17 种形状）。**562 个测试全过。**
> **尚未实现**：占比 / 机构名等值专门规则（M2 下一步），以及同比 / 环比 / 变化比率、全局 Top N、多跳 JOIN；
> `question → Intent` 抽取（M3）；可答性门禁与黄金集扩到 30 条（M4）。见 `docs/ROADMAP.md`。

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
**下一步：M2.5 占比（分母为整体）**；L1 自然语言抽取与 L5 生产执行仍未完成。


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

`app/engine/validation/rules.py` 里的 6 条规则，每条都有：稳定 problem code + 业务规则编号（`C-xx`）+ 单测。

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
uv sync --group dev
uv run pytest                                # 562 passed，不需要外部数据库 / 模型 / 网络
uv run ruff check .                          # All checks passed
uv run python -m scripts.demo_ask            # 端到端演示：问题 → Intent → SQL → 校验
uv run python -m scripts.check_golden        # 黄金集门禁：引擎产出 vs 手写黄金 SQL
uv run python -m scripts.trace_compile       # 学习工具：把编译器五步逐个打出来
```

**测试包含 SQLite 内存结果验证，不需要外部数据库。** 双表关联默认要求维表键唯一，
基数声明不是数据质量检查；`None` Scope 不限制，显式空 Scope 拒绝访问。JOIN 暂不与 delta/Top N/report/union 组合。

**fixture 数据库默认用 SQLite，零依赖**（ADR-0006）。PostgreSQL 只在需要方言 / EXPLAIN 验证时可选启用。

---

## 五、文档索引

| 文档 | 看什么 |
|---|---|
| **`docs/CONTEXT.md`** | ★ **新会话/新同事先读这一份**：项目是什么、做到哪了、我们在替代 askdb 的哪一步、怎么跑、踩过哪些坑 |
| `docs/ARCHITECTURE.md` | 分层、数据流、编译器内部结构、错误分类、`Scope` 三个注入点 |
| `docs/DECISIONS.md` | 已定的架构决策（ADR），每条都写了"什么情况下翻案" |
| `docs/ROADMAP.md` | M0~M4 里程碑，每步的验收标准和对应评测用例 |
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
│   │   ├── compiler/       # L3 编译器（0 次模型）：__init__ 装配 / time / metrics / conditions / comparison / topn / sources / permissions / reports / report_time
│   │   ├── validation/     # L4 校验层（只读 / 名字 / 范围 / 6 条业务规则 → M2 补到 8 条）
│   │   ├── clarify/        # L1/L2 意图清洗 + 确定性槽位检查
│   │   ├── retrieval/      # L2 检索式定范围：按业务词找候选表/列/指标（M3）
│   │   └── coverage/       # L2 可答性门禁：COMPILE / CLARIFY / REFUSE（M4）
│   ├── llm/                # 模型访问边界：Protocol + fake / replay / real
│   └── config.py
├── eval/cases/             # 评测集（yaml，带 why_hard / covers）
├── eval/golden/            # 黄金集：cases.yaml + G-xxx.sql（手写标准答案）
├── fixtures/sql/           # 假银行库 DDL + 缺陷清单 D-01~D-11
├── scripts/                # demo_ask / check_golden / golden / seed_fixture / trace_compile …
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
