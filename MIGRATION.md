# 迁移计划：从 askdb 起步，逐步换成新引擎

> ## ⚠️ 当前状态：Step 0 **尚未执行**
>
> 本仓库现有 51 个文件，**零行代码复制自 askdb 或 auto-ontology**，
> 全部重新编写（可复核：不含任何第三方版权头，不含任何 askdb 源文件）。
>
> 下文的"抄"与"不抄"是**计划**，不是已完成的事实。
> 每搬入一批，请回来更新本文件状态，并在 `ATTRIBUTION.md` 第二节登记。
>
> 因此 **`web/`（前端）目录当前不存在**，`app/legacy/` 也是空的。

---

## 为什么是"选择性搬入"而不是 fork

askdb 仓库的实际体量（已核实）：

```
outputs/    270.7 MB / 4,539 文件   ← 运行时输出垃圾
specs/       34.6 MB
offline/     28.5 MB
backend/      9.0 MB / 185 文件
frontend/     0.9 MB / 70 文件
.git 历史里还有一段"内层重复 clone → 已删除"的脏记录
无 pyproject.toml · 无 lint 配置 · 无 CI
```

fork 会把这 300+ MB 和历史脏记录全带进来，而**你要改的恰恰是架构本身**。

更重要的原因：删代码比搬代码难得多。fork 之后每删一个模块都要纠结
"这个还要不要"，而重写需要的是**敢于推倒**。物理上分开的仓库给了这个分离。

代价是：搬入时要处理 import 依赖。所以每搬一块就立刻跑一次 import 冒烟。

---

## 一、抄 / 不抄的分界

判据只有一条：**这段代码是不是"编排主干"？**

* 横切能力（连接器、权限、码值、前端、周边服务）→ **抄**
* 编排主干（节点流程、SQL 生成、选表、分诊、prompt）→ **重写**

### ✅ 计划抄（约 19,400 行 + 70 个前端文件）

| askdb 路径 | 体量 | 目标路径 | 理由 |
|---|---|---|---|
| `frontend/` | 70 文件 / 0.9 MB | **`web/`** | React 18 + Vite 5 + AntD 5，中文银行 UI，从零写 3~4 周 |
| `backend/app/models/` | 17 文件 / 960 行 | `app/legacy/models/` | 44 张表；语义层原料（列描述/同义词/角色/语义维度/码值） |
| `backend/app/schemas/` | 14 文件 / 633 行 | `app/legacy/schemas/` | Pydantic |
| `backend/app/crud/` | 10 文件 / 1,381 行 | `app/legacy/crud/` | 数据访问 |
| `backend/app/api/` | 7,010 行 | `app/legacy/api/` | 除 `query.py` / `chat.py` 外照抄 |
| `backend/app/{core,db,middleware}/` | 1,423 行 | `app/legacy/` 对应目录 | 认证、配置、连接池、启动迁移 |
| `services/connector_manager.py` | 1,206 行 | `app/legacy/services/` | ⭐ Oracle/PG/MySQL + sqlglot 方言 + 只读校验 + EXPLAIN + 日期字面量归一化。**auto-ontology 完全没有 Oracle** |
| `services/permission_service.py`<br>`services/row_policy_service.py` | 636 行 | 同上 | ⭐ 三层纵深：表过滤 → 行级 WHERE 注入 → 列白名单 |
| `services/derived_metric_service.py` | 1,346 行 | 同上 | ⭐ 同比/环比/增量/差值 |
| `services/code_mapping_gating_service.py`<br>`services/code_mapping_service.py`<br>`services/high_cardinality_mapping_service.py` | 626 行 | 同上 | 码值映射 + 门控 + 高枚举 KB |
| `services/ambiguity_service.py` | 259 行 | 同上 | ⭐ 纯确定性歧义词典，设计很好 |
| `services/{expertise,knowledge,indicator}_service.py` | 2,060 行 | 同上 | 专业知识 / 知识库 / 指标 |
| `services/llm_service.py` | 438 行 | 同上 | LLM 调用 + JSON 修复（**必须改造为走 `app/llm` Protocol**） |
| `services/display_label_service.py` | 344 行 | 同上 | 结果列中文命名 |
| `services/{audit,auth,user_sync,query_page,conversation_export,mobile_result_policy,database_summary,sql_error,scene_policy}_service.py` 等 | ~2,500 行 | 同上 | 周边能力 |

### ❌ 计划不抄（约 15,700 行，重写）

| askdb 路径 | 行数 | 为什么 |
|---|---|---|
| `services/sql_gen_service.py` | 2,861 | 节点 19 + 图三 S01–S18，5,502 字 prompt —— 正是要推翻的 |
| `services/rebuild_sql_service.py` | 2,766 | AST 重组，与选表/权限深度耦合 |
| `services/query_orchestrator.py` | 2,445 | 24 节点固定 DAG，单函数 ~1,700 行 |
| `services/table_selection_service.py` | 1,464 | **软排序**选表 → 换成 retrieve + AST 强校验 |
| `services/sql_scope_service.py` | 1,063 | 作用域逻辑与新设计冲突 |
| `services/workflow_prompt_service.py` | 885 | 5,502 字 prompt 的宿主 |
| `services/routing_service.py` | 598 | 二分类分诊 + 硬编码关键词 |
| `services/knowledge_migration_service.py` | 1,361 | 一次性迁移工具 |
| `services/evaluation_service.py` | 491 | 思路参考，代码重写 |
| `services/workflow_trace.py` | 209 | 节点 trace 需适配新流程 |
| `services/regression_*` / `local_regression_data.py` / `random_faq_*` | ~1,600 | fixture 代码重写，但**数据设计留着用**（见第六节） |

---

## 二、搬入时的具体做法

**必须同时做的四件事：**

1. **`app/legacy/` 单独隔离**，配 `ruff-legacy.toml`（只查 `F`/`E9`），
   CI 里非阻塞。目的是"可见但不阻塞"，而不是"一上来就修 17,000 行 lint"。
2. **`llm_service.py` 改造成走 `app/llm` Protocol。**
   否则搬进来的代码会自己去 `httpx.post`，CI 就再也跑不了了。
   改造方式见 `app/legacy/README.md`。
3. **前端放 `web/`**，与后端同仓库、分开部署。
   `pyproject.toml` 的 `packages = ["app"]` 不包含 `web/`，互不干扰。
4. **更新 `ATTRIBUTION.md` 第二节**，逐文件登记来源。askdb 没有 LICENSE，
   默认"保留所有权利"，使用依赖作者授权，留痕对双方都好。

---

## 三、Step 0：先把 baseline 跑出来

**这一步的产出比任何代码都重要。** 没有 baseline，后面所有重构都无法证明没有变差。

```text
1. 搬入后先让旧引擎在新仓库里跑通（不改逻辑）
2. 跑 10 条用例，产出 eval/baseline_report.json：

   {
     "engine": "legacy",
     "cases": {
       "AQ-001": {"correct": true,  "answer_path": "etl_calculated",
                  "duration_ms": 2100, "llm_calls": 4},
       "AQ-006": {"correct": false, "answer_path": "report_hit",
                  "duration_ms": 3400, "llm_calls": 6}
     }
   }

3. 人工核对 correct 字段 —— 这一步不能全自动。
```

人工核对的意义：baseline 的 `correct` 一旦错了，回归门禁的判定就全错，
而且它会**给出虚假的安全感**——比没有门禁更危险。
AQ-006 就是典型：SQL 跑通、数字合理、口径错。

---

## 四、Step 1：替换 SQL 生成（收益最大、风险最小）

保持上游输入不变，只换生成器。这样每一步都能独立验证。

```text
输入（旧引擎已产出，不改）：
  structured_query（节点09 的结构化结果）
  selected_tables（节点12 的选表结果）
  references（报表 / 相似问 / 模板 / 专业知识）

旧：→ 5,502 字 prompt → 模型写 SQL
新：→ 语义层解析 → Intent → 编译器 → AST → 方言 SQL
                    → validation.rules 六条规则校验
```

先只覆盖**单表 + 单指标 + 单时点**，跑通再扩。

| 小步 | 动作 | 验证 |
|---|---|---|
| 1.1 | 单表单指标单时点 | AQ-001 / AQ-003 |
| 1.2 | 维度分组 + 名称字段 MAX | AQ-005（`R-NAME-001`） |
| 1.3 | 双时点对比 | AQ-004（`R-COMP-001`） |
| 1.4 | 每月 Top N 窗口函数 | AQ-008（`R-TOPN-001`） |
| 1.5 | 多表 JOIN + 表数上限 | AQ-007（`R-TBL-001`） |
| 1.6 | 报表分支（禁 JOIN） | AQ-006（`R-JOIN-001`） |

每完成一小步跑 `check_regression` 确认没有红线。

---

## 五、Step 2~4

| Step | 换掉什么 | 关键点 |
|---|---|---|
| **2** | 检索作用域（节点 10 的三选一） | 语义层 retrieve → `Scope` → `TABLE_NOT_IN_SCOPE`。同时接混合召回 + 重排，见 `docs/DECISIONS.md` ADR-0002 |
| **3** | 24 节点 DAG | 换成显式状态机（不引入 LangGraph，见 ADR-0001） |
| **4** | 分诊 + 澄清 | 二分类 → 五分类（含"不可答"）；11 处散落澄清 → `clarify/slots.py` |

---

## 六、fixture 数据的处理

askdb 的回归 fixture **数据设计可以留着用**（省 1~2 周造数据）：

| 现成的 | 内容 | 怎么用 |
|---|---|---|
| `regression_fixture_service.py` 里的 DDL | 3 张明细表 + 21 张报表 | 已参考并扩展成 `bank_fixture.sql` |
| `_seed_knowledge()` | 14 个指标定义（含 `calculation_formula`） | 搬进 `indicator_definition` |
| `_seed_relations()` | 2 条表关系 | 搬进 `table_relation` |
| `regression_faq.py` | 100 条 FAQ | 改造成 `eval/cases/*.yaml` |

⚠️ **但它太干净了**：3 张表、27 个字段全有中文注释、没有历史垃圾。
所以 `bank_fixture.sql` 刻意加了 D-01 ~ D-11 共 11 项缺陷，
详见该文件末尾的清单。

---

## 七、搬完每一块后的自检清单

- [ ] 它 import 时会联网吗？（如果是，改造成走 `app/llm` Protocol）
- [ ] 它有没有硬编码某家银行的字段名？（如果有，抽到 `ValidationPolicy`）
- [ ] 它有没有 `print` / 静默 `except Exception: pass`？
- [ ] 我能不能在 3 个月内说清这个文件每一行为什么存在？
  （不能的话，它该留在 `app/legacy/` 而不是进 `app/engine/`）
- [ ] `ATTRIBUTION.md` 登记了吗？
