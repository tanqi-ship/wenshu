# 参考项目阅读地图

> **这不是迁移计划。** 本项目不复制 askdb 或 auto-ontology 的任何代码（ADR-0003）。
> 这份文档回答的是：**想理解某个设计时，该去读哪一个文件**，以及**别人踩过的坑清单**。

两个参考项目的位置：

| 项目 | 路径 | 体量 | 定位 |
|---|---|---|---|
| askdb | `D:\projecct\askdb` | 188 py + 31 jsx | 固定 DAG + 参考资产路线（人工枚举） |
| auto-ontology | `D:\projecct\auto-ontology` | 470 py + 271 ts | ReAct + 自动 ontology 编译 + RAG |

---

## 一、读 askdb：按"要不要学"分三类

### ✅ 值得读（有具体可学的点）

| 文件 | 行数 | 读什么 | 为什么值得 |
|---|---|---|---|
| `backend/app/services/rebuild_sql_service.py` | 2,766 | **纯 sqlglot AST 改造，0 次 LLM** | 全项目最好的"确定性 SQL 改造"样板。想学怎么在 AST 上安全改 SQL，读这一个文件 |
| `backend/app/services/connector_manager.py` | 1,206 | `validate_readonly_sql`（`:623-948`） | 只读 + 表白名单 + 列白名单（含 CTE / 子查询递归）。**列白名单那 250 行递归是最难的部分** |
| `backend/app/services/ambiguity_service.py` | 259 | 确定性歧义词典 | 小而干净，不调模型 |
| `backend/app/services/derived_metric_service.py` | 1,346 | 同义/环比/增量的词表与正则 | 派生指标怎么做到确定性的 |
| `backend/app/models/` 全部 | 960 | `db_column_meta` 的字段设计 | 语义层原料：`column_role` / `semantic_dimension_code` / `code_mapping_*` |
| `backend/app/services/permission_service.py` + `row_policy_service.py` | 636 | 三层权限纵深 | 表过滤 → 行级 WHERE 注入 → 列白名单。**注意它的文档自承"曾有泄漏 bug"** |
| `AGENTS.md` | — | 架构速览 | 一份非常好的工程笔记，含"spec 与代码不一致"的警告 |

### 🟡 只读结论，不读实现

| 文件 | 结论 |
|---|---|
| `sql_gen_service.py` | 「节点19」= 5,500 字 prompt + 模型自由写 SQL + 9 层事后校验。**这就是本项目要推翻的东西**，不必细读 |
| `query_orchestrator.py` | 24 节点固定 DAG，单函数 1,700 行。读完 `record_node_state` 的调用点即可 |
| `table_selection_service.py` | 模型给的表结构输出被代码丢弃一半（`join_plan`/`combination_mode` 全部重算），只剩 `filters` 有用 |

### ❌ 不必读

`frontend/`（31 jsx）、`auth` / `wecom` / `user_sync` / `audit`、`analytics_service` / `evaluation_service` / `regression_*`、`specs/003` `specs/005`（未实现的草案）、`experiments*`、`outputs/`。

---

## 二、读 auto-ontology：只读它的三块

| 文件 / 目录 | 读什么 |
|---|---|
| `docs/architecture.md`（337 行） | **整个项目最值得读的一篇**。每个步骤标了"是否调模型 / 默认是否开启 / 由哪个设置控制" |
| `retrieval/entity_coverage/` | 可答性打分的完整实现（10 行核心逻辑）。**注意 ADR-0002 已论证不用它的阈值法** |
| `retrieval/text_to_sql/react/prompts.py:7-10` | 「规则块放 prompt 会掉准确率」的原始出处 |
| `auto_ontology/dal/schema.py` | ontology 数据模型：`term` / `column_attribute` / `sql_attribute` / `term_family` |
| `auto_ontology/semantic/semantic_fk.py` | 语义外键推断：**推断出的 FK 必须"join 返回行"才允许存** |

---

## 三、★ 设计检查表：askdb 踩过的坑（本项目必须避免）

这一节是前三节的浓缩。每条都是代码里核实过的事实，不是猜测。

| # | 坑 | 事实（带行号） | 本项目的对应防线 |
|---|---|---|---|
| 1 | **维度过滤没进最后一步的 prompt** | `sql_gen_service.py:1448-1453` 只挑 4 个 key 丢掉 `filters`；`:1494-1498` 主动剔除 filter 类键；`:1516` 硬编码 `[]` | L3 编译时 `filters` 是**必填输入**，编译不出来就报错 |
| 2 | **选表降级时过滤条件被清空** | `table_selection_service.py:1544` `filters = []` | 无降级路径；编译失败不会"猜一个更宽的范围" |
| 3 | **权限相关的强校验只在 KB 命中时生效** | `_validate_filters` 读 `code_mapping_source`，但 `build_execution_context` 不产出该键 | `Scope` 是必填输入，缺了直接编译失败（fail-closed） |
| 4 | **向量重排失败 → 全部候选置 0 分** | `knowledge_service.py:733-736` | 不用 LLM 重排（ADR-0002） |
| 5 | **rule 写在 prompt 里但代码不执行** | `workflow_prompt_service.py:559` 要求"报表命中也要走选表"，代码直接跳过 | 规则要么在代码里，要么根本不存在（不留双重来源） |
| 6 | **疑似死分支** | `organization_resolution` 只可能 `none`/`resolved`，但 `query_orchestrator.py:1812` 在判 `"clarification"` | 分支必须有测试覆盖，否则删掉 |
| 7 | **置信度按"参考来源"给，不按"对不对"给** | `sql_gen_service.py:1473-1488` 五档；且 `sql_template`(0.3) 优先级高于 `expertise`(0.5) | `Intent.confidence` 只写日志，不参与任何判断 |
| 8 | **重试不回传原 SQL** | `sql_gen_service.py:1519` 只回灌错误信息 | 重试必须携带原 SQL（README 纪律 3） |

## 四、参考但**明确不采用**的做法

| 做法 | 出处 | 不采用的理由 |
|---|---|---|
| 用向量距离阈值判"可答" | auto-ontology `entity_coverage` | 阈值无法向审计解释；换 embedding 模型即失效（ADR-0002） |
| 用 LLM 给候选打 rerank 分 | askdb `knowledge_service.py:697` | 多一次模型调用 + 分数不可复现 + 阈值是拍的（ADR-0002） |
| ReAct 循环 24 次工具调用 | auto-ontology | 本项目瓶颈就是模型调用次数；借鉴它的**工具校验设计**，不借鉴循环 |
| 完全不澄清 | auto-ontology「The ReAct agent doesn't ask clarifying questions」 | 银行口径歧义必须问（`clarify/slots.py` 的存在理由） |
| 一次迁移 19,400 行现成代码 | 原 MIGRATION.md | 本项目要改的正是架构本身；且参考项目无法在本机运行（ADR-0003） |

---

## 五、怎么用这份地图

1. **写某个模块之前**，先读对应那一行的"读什么"——15 分钟够。
2. **设计完之后**，用第三节的 8 条坑**逐条检查你的设计**。这 8 条是从真实代码里挖出来的，不是清单式建议。
3. **不要试图读完 askdb。** 188 个文件里对你有用的不超过 8 个。
