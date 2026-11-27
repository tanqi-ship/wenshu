# 路线图

每个里程碑必须有**可运行的验收物**。没有验收物的里程碑不是里程碑，是愿望。

---

## M0 · 骨架与护栏 ✅ 已完成

| 交付 | 状态 |
|---|---|
| 工程骨架 + pyproject + ruff 分圈 + pre-commit | ✅ |
| LLM 访问边界（Protocol + fake / replay / real） | ✅ |
| 校验层：只读 / 名字 / 范围 / 6 条业务规则 | ✅ |
| 澄清层：槽位清单 + 追问预算 | ✅ |
| 假银行 fixture（D-01 ~ D-11 缺陷） | ✅ |
| 评测集 10 条（`why_hard` + `covers` 强制） | ✅ |
| 黄金集回归门禁（原为双引擎，M1.5 改造完成） | ✅ 见 M1.5 |

**验收**：`uv run pytest` 全绿，不需要数据库 / 模型 / 网络。

> ~~待改造：双引擎对照门禁~~ → **M1.5 已完成改造**：因只做单条主链路、不做双引擎对照（ADR-0003），baseline 永远产不出来。
> 改为"黄金集门禁"：引擎产出 vs `eval/golden/*.sql` 手写黄金 SQL，**比 SQL 结构**（不是结果集；
> 比结果集要真库，属于 M3.4/M4）。

---

## M1 · 编译器最小闭环 ✅ 已完成

**目标**：第一次让 `Intent` 变成一条**合规 SQL**，并且打通"编译 → 校验"。

| 小步 | 内容 | 状态 | 落地位置 |
|---|---|---|---|
| 1.1 | `DimensionDef` + `LevelDef` 数据模型 | ✅ | `semantic/dimension.py` |
| 1.2 | `compile()` 第一路：单指标 + `month_end` + 无维度 | ✅ | `compiler/__init__.py`；黄金集 `eval/golden/G-001.sql` |
| 1.3 | 维度分组（GROUP BY 编号列 + `MAX(名称列)`） | ✅ | `compiler/__init__.py::_dimension_items`；`R-NAME-001` 不触发 |
| 1.4 | `Scope` 三注入点（表 / 列 / 行级 WHERE） | ✅ 表/列/行；列白名单于 M2.3 补齐 | `engine/scope.py` + `compiler/conditions.py` |
| 1.5 | 黄金集门禁（替代双引擎） | ✅ | `scripts/golden.py` + `scripts/check_golden.py` + `eval/golden/cases.yaml` |
| — | 语义层容器 `SemanticCatalog` | ✅ 额外完成 | `semantic/catalog.py` |
| — | 编译错误五分类（stage + 稳定 code） | ✅ 额外完成 | `compiler/errors.py` |
| — | 演示/测试共用的语义层（消除三份手写副本） | ✅ 额外完成 | `semantic/bank_mini.py` |
| — | 配置 → `Intent` 加载器 | ✅ 额外完成 | `semantic/models.py::intent_from_mapping` |

**验收结果**：`tests/engine/test_compiler_minimal.py` 14 个测试全过；原有测试无回归。
三种反例分别落在三个 stage：`symbol_table`（指标不存在）、`intent`（时间没解析出来）、
`scope`（指标来源表越权）。

**M1 踩到并修掉的两个坑（写进测试防复发）**：

1. `sqlglot` 的 `Select` arg key 是 **`from_`**（带下划线）。写 `from` 不报错，静默丢掉整个 FROM 子句。
2. 由此发现：**L4 的规则校验抓不到"缺 FROM"**（`SELECT 1` 也是合法 SQL），
   必须靠黄金集结构比对和 `test_from_clause_present` 这类结构断言兜底。

**M1 完成的标志**：
```python
sql = compile_intent(intent, catalog, scope)
result = validate(sql, policy)
assert result.problems == []
```

**这一步之前，项目只有壳；这一步之后，项目有了发动机。**

---

## M1.5 · 黄金集门禁 ✅ 已完成

**目标**：一条命令回答"引擎现在算得对不对"，并且**加新形状时不用手写新测试**。

| 交付 | 落地位置 | 说明 |
|---|---|---|
| 归一化 + 比对 | `scripts/golden.py::canonical_sql / compare` | 解析 → 去注释 → 未加引号标识符小写 → 单行渲染；出 unified diff |
| 用例加载 | `scripts/golden.py::load_cases / cases_from_payload` | 缺字段 / 未知键 / id 重复 / 孤儿 `.sql` / `covers` 格式 → 全部 fail-closed |
| 门禁 CLI | `scripts/check_golden.py` | `--golden/--cases/--output`（照 `ci.yml` 已有契约）；退出码 0/1/2 |
| 用例数据 | `eval/golden/cases.yaml` + `G-001`~`G-007.sql` | 黄金 SQL **手写**，与编译器形状对齐（别名 `t1`、字符串日期） |
| 形状覆盖 | `month_end_single_point` / `dimension_grouping` / `business_filter_eq` / `range_between` / `row_policy_injection` / `row_policy_fail_closed` | 起初只钉了单时点；**补票**后覆盖编译器已实现的 6 类形状（共 7 条用例） |
| pytest 接线 | `tests/engine/test_golden_gate.py`（35） | 参数化跑门禁 + 元数据纪律 + **门禁自己会不会红** |
| 权限形状 | `eval/golden/G-006.sql` + `G-007.sql` | 编译器的第三个输入 `Scope` 首次进黄金集；含 fail-closed（`1 = 0`） |
| 比对器单测 | `tests/engine/test_golden_compare.py`（21） | 该忽略什么（排版/大小写/注释）与**不该忽略什么**（字面量大小写/IN 顺序/缺 FROM） |

**三条刻意的设计**：

1. **不提供 `--update` / `--bless`**。黄金 SQL 一旦能一键刷新，门禁就退化成"引擎没变"的自证。
2. **退出码三档**：`0` 全过 / `1` 引擎算错了 / `2` 用例配置或黄金 SQL 坏了 —— 三档对应三个不同的人。
3. **编号分家**：黄金集 `G-xxx`，评测集 `AQ-xxx`。此前两边都叫 `AQ-001` 但内容不同。

**四种失败已实测会红**：改错日期（1）/ 指标不存在（1）/ 孤儿 `.sql`（2）/ 黄金 SQL 语法错（2）。

**一个反直觉的结论**：**L4 对编译器产出几乎永远不会红** —— 编译器在设计上写不出违规形状。
所以 L4 在编译路径上是兜底防 bug，真正会红的是黄金集。

---

## M2 · 形状扩展 ✅ 已完成（6/6）

按"出错代价从高到低"排序，先做最容易算错的。

| 小步 | 形状 | 对应规则 | 用例 | 状态 |
|---|---|---|---|---|
| 2.1 | 双时点对比 `delta`（同一 SELECT + `CASE WHEN` 分段聚合） | `R-COMP-001` | AQ-004 | ✅ **完成** |
| 2.2 | 每月 Top N（窗口函数 + 外层过滤） | `R-TOPN-001` | AQ-008 | ✅ **完成** |
| 2.3 | 受限双表 JOIN（`RelationDef` 驱动，≤2 表） | `R-TBL-001` | G-011~G-014（AQ-007 为单表反例） | ✅ **完成** |
| 2.4 | 显式报表分支（禁 JOIN、多月 `UNION ALL`） | `R-JOIN-001` | AQ-006 / G-015~G-018 | ✅ **完成** |
| 2.5 | 同口径整体占比（展示过滤不缩小分母） | `R-RATIO-001` | G-019~G-022 | ✅ **完成** |
| 2.6 | 严格机构值登记与独立等值检查 | `R-ORG-001` | G-023~G-026 | ✅ **完成** |

**验收**：M2.1~M2.6 各有手写黄金 SQL 和正反例；M2 验收时为 26/26 黄金用例（25 个形状标签）、991 项测试通过，8 条业务规则均有单测。

**规模纪律**：以完整的配置边界、黄金 SQL 和正反例验收为准，不为满足早期行数估计省略权限或失败检查。

### M2.1 落地记录（2026-11-20）

| 交付 | 位置 |
|---|---|
| 投影构造器 | `compiler/comparison.py::build_metric_projections`（接收已解析的指标 + 已校验时点，不接收 `Intent`） |
| 意图校验 | `compiler/comparison.py::resolve_comparison_points`（4 条显式报错，见下） |
| 新增错误码 | `COMPARISON_UNSUPPORTED` / `COMPARISON_POINT_COUNT` / `MULTI_POINT_WITHOUT_COMPARISON`（`intent`）+ `DELTA_NOT_ADDITIVE`（`type_check`） |
| 黄金 SQL | `eval/golden/G-008.sql`（贷款余额）/ `G-009.sql`（不良余额，两层嵌套 CASE） |
| 单测 | `tests/engine/test_compiler_comparison.py`（17） |
| 决策记录 | `DECISIONS.md` **ADR-0007** |

**四条越界都显式报错，一条都不许静默放过**：
`yoy`/`mom`/`rate` → `COMPARISON_UNSUPPORTED`；点数 ≠ 2 → `COMPARISON_POINT_COUNT`；
两个时点但 `comparison` 为空 → `MULTI_POINT_WITHOUT_COMPARISON`；
比率型指标（顶层不是 `SUM`）→ `DELTA_NOT_ADDITIVE`。

最后一条最值得记：**两个时点却没说怎么比，如果放过去，产出的 SQL 是把两个时点的余额加在一起**
—— 跑得通、不报错、数字还挺像。这类错没有任何一层会喊。

### M2.2 落地记录（2026-11-23）

| 交付 | 位置 |
|---|---|
| Top N 意图与月末解析、排名包装 | `compiler/topn.py::resolve_monthly_topn / wrap_monthly_topn` |
| 编译接线 | `compiler/__init__.py`：复用指标、维度、业务过滤与权限注入，再包装排名查询 |
| 黄金 SQL | `eval/golden/G-010.sql`，对应 AQ-008；共 10 条黄金用例、9 类形状 |
| 结果与边界验证 | `tests/engine/test_compiler_topn.py`：结构断言 + SQLite 内存执行 + 越界报错 |
| 门禁接线 | `scripts/golden.py` 向校验器传 `Intent`；反例验证遗漏排名时门禁会失败 |
| 决策记录 | `DECISIONS.md` **ADR-0008** |

**口径**：先按月末与主体编号聚合，再 `ROW_NUMBER() OVER (PARTITION BY 月末 ORDER BY 指标 DESC, 主体编号 ASC)`，
最后外层 `rn <= N`。只读区间内月末快照，不混入月中记录，不回退或补齐缺失月份；
业务与权限过滤在聚合、排名前生效。多维度时按维度组合排名，指标 NULL 排在有效值后。

**显式边界**：N 必须为正整数、必须指定排名维度与已解析时间；
全局 Top N、与 comparison 或 report/union 分支混用均拒绝。配置加载不再把小数或布尔 N 静默转成整数。
`TOPN_INVALID_N / TOPN_UNSUPPORTED / TOPN_DIMENSION_REQUIRED / TOPN_TIME_UNSUPPORTED` 属于 `intent`；
输出列冲突报 `type_check/TOPN_OUTPUT_COLLISION`，时间缺失沿用 `TIME_UNRESOLVED`。

**验收结果**：281 项测试通过；黄金集 10/10；Ruff 检查通过。下一步是 **M2.3 多表 JOIN**。

### M2.3 落地记录（2026-11-23）

| 交付 | 位置 |
|---|---|
| 关系与来源绑定 | `compiler/sources.py`：有效依赖、两表上限、唯一关系、方向/基数与别名 |
| 物理列权限 | `compiler/permissions.py`：在外层包装前检查全部实际列引用 |
| 语义配置 | `LevelDef.table`（默认主表）、`RelationDef.cardinality`（默认 unknown） |
| 条件/公式接线 | `conditions.py` 按表绑定业务/行级策略；`metrics.py` 严格表达式解析与主表指标绑定 |
| 黄金 SQL | `G-011` INNER / `G-012` LEFT / `G-013` 两表过滤与权限 / `G-014` LEFT 右表空权限 |
| 黄金集配置 | 可选 `relations` 是用例级 L0 配置，严格加载、不可污染共用语义层 |
| 单测 | `test_compiler_join.py`（92 项）：结构、SQLite 结果与稳定错误码 |
| 决策 | ADR-0009；已确认设计和实施计划位于 `docs/superpowers/` |

**边界**：只支持从主事实表到唯一维表的一对一/多对一 INNER/LEFT，关联表只提供维度和过滤。
关系缺失、歧义、未知/不安全基数、三表、反向 LEFT、跨表度量及 delta/Top N/report/union 组合均显式拒绝。
基数是语义层声明，不等于检查过真实数据；测试用主键约束，并用无约束重复数据反例揭示金额放大。

**权限契约补齐**：未传 Scope 不限制，显式空 Scope 在 L3/L4 都拒绝。
列白名单约束指标、时间、维度、名称、过滤、JOIN 键与行策略列，单表也适用；
行策略按物理表绑定，未参与表的策略不引入额外表。LEFT 右表权限放 WHERE，不把无权金额重归为 NULL 分组。

**纠正用例对应**：AQ-007 是单表核销查询，不是 JOIN 正例，本次保持其语义不变。
**验收**：398 项测试通过，14/14 黄金用例通过（13 类形状），原 G-001~G-010 不变；Ruff 检查通过。
M2.3 验收时完成 3/6；其后的 M2.4 已完成，见下节。

---

### M2.4 落地记录（2026-11-23）

| 产出 | 位置与说明 |
|---|---|
| 来源元数据 | `semantic/report.py`：ReportDef 固定粒度、值列/月份列、显式分表；Catalog 可选 reports，不改明细公式 |
| 月份解析 | `compiler/report_time.py`：整月锚点/真实月末/完整区间，支持跨年闰年、排序去重，缺月不猜表 |
| 编译路径 | `compiler/reports.py`：直接投影，单月 SELECT、多月 UNION ALL、最终统一排序，Query 返回类型 |
| 条件与权限 | 共用精确码值谓词；逐分区表/列/行权限，不要求底层明细可见；独立别名保护 L4 |
| 黄金 SQL | G-015 AQ-006 单月；G-016 三个月 ALL；G-017 业务过滤与不同权限；G-018 保留空行策略分支 |
| 单测 | 元数据 30 项 + 时间 24 项 + 编译/SQLite 结果 98 项；原有报表路由负例更新为报表错误码 |
| 决策 | ADR-0010；已确认设计、实施计划在 `docs/superpowers/` |

**守住的边界**：请求维度必须等于报表粒度，预计算值不再次聚合、不乘 100；禁 JOIN、对比和 Top N。
任一分区缺配置、表不可见或引用列不可见整条失败，不回退明细、不丢月。
空行权限只使所属合法分支恒假且保留分支，不承诺每月有数据。

**验收**：562 项测试通过，18/18 黄金用例通过（17 类形状），原 G-001~G-014 SQL 不变；
10 条评测用例纪律、Ruff、变更文件格式、diff 与原演示/跟踪脚本通过。
SQLite 结果确认标准 LP03 = 0.140700（非逾期口径 0.128300）；三方言仅做 AST 渲染/回解析，不冒称外部数据库验收。
M2.4 验收时完成 4/6；其后的 M2.5 已完成，见下节。
L1 自然语言抽取和 L5 生产执行链未因此完成；本轮不自动提交实现。

---

### M2.5 落地记录（2026-11-23）

| 产出 | 位置与说明 |
|---|---|
| 意图 | `ShareSpec` / `Intent.share`，独立于 rate，严格加载且保留旧字段顺序 |
| 编译 | `compiler/share.py`：单表月末单维度 SUM，分组/整体/展示三层，别名隔离与物理列权限复用 |
| 分母边界 | 上下文与行策略在底层，业务拆分过滤在外层；整体只含可见数据，不随展示重新归一化 |
| 独立校验 | `validation/ratio.py`：R-RATIO-001，验证三层、空窗口、金额/NULLIF/时点、底层策略与提前过滤 |
| 黄金 SQL | G-019 普通占比、G-020 机构上下文+单产品、G-021 同列产品权限、G-022 空策略 |
| 专项测试 | 意图 17 + 编译/SQLite 104 + 独立手写 SQL 篡改 57 = 178 项 |
| 决策 | ADR-0011；已确认设计与实施计划在 `docs/superpowers/` |

**明确边界**：不与 JOIN、report/union、Top N、时点对比组合；已有比例、SUM DISTINCT、嵌套聚合拒绝。
分母 0/NULL 保留 NULL，空源/空权限/缺组不补行；非空 org 不能静默忽略，机构条件须明确放入 filters。
L4 不读取 L3 中间结果，不自动证明业务公式和码值映射正确，相关语义由独立黄金及 SQLite 真值验证。

**验收**：752 项测试、22/22 黄金用例（21 类形状）、10 条评测纪律、Ruff/格式/diff 和演示脚本通过；
G-001~G-018 SQL 保持不变。整数样本验证 300/1000=0.3，产品行权限收缩后为 300/400=0.75。
PostgreSQL/Oracle 仅 AST 渲染/回解析，不宣称外部数据库验收；没有调用模型或联网。
M2.5 验收时为 5/6；其后的 M2.6 已完成，见下节。L1/L5 不因此完成。

---

### M2.6 落地记录（2026-11-23）

| 产出 | 位置与说明 |
|---|---|
| 严格登记 | semantic/exact_values.py；标记、格式、候选冲突检查，别名/编码同空间，只 trim；普通 coded 不变 |
| 共用编译 | conditions.py 仅严格 eq/in，稳定原因错误；None/空 IN 不强制查登记，IN 不允许部分成功 |
| 上下文/权限 | 普通/报表不再静默丢弃 Intent.org；share 旧错误不变，Scope 不重译，默认机构口径不变 |
| 独立 L4 | policy.org_columns 冻结配置；org.py + org_sources.py 按物理身份与查询作用域追踪并检查所有匹配条件 |
| 来源边界 | 投影/CTE/UNION/星号保留保护，变形拒绝，歧义/相关/递归机构匹配保守拒绝；排名/数值统计不误报 |
| 黄金 SQL | G-023 简称；G-024 全称/编码 IN；G-025 上级 JOIN 与 Scope；G-026 别名、整体分母与两类行策略 |
| 专项测试 | 严格登记 46、编译/SQLite/方言 58、独立 L4 攻击/策略 123，共 227 项；另随黄金集增加 12 项门禁测试 |
| 决策 | ADR-0012；已确认设计与实施计划在 docs/superpowers/ |

**验收**：991 项测试、26/26 黄金用例（25 个形状标签）、10 条评测纪律、Ruff/变更格式/diff 通过；
demo_ask 与 trace_compile 可运行。独立样本 B001=100，代码前缀误得 300、名称前缀误得 600；
精确查询不得包含 B0019 或下辖 O001，AQ-002 与旧 G-001~G-022 原 SQL 不变。
新增四条黄金 SQL 在独立 SQLite 数据上验结果；PostgreSQL/Oracle 只验 AST 渲染/回解析及 L4。
本轮无模型、网络或外部数据库调用，不自动提交实现。M2 为 **6/6**，独立业务规则 **8 条**。
**后续**：M3.1～M3.4 离线闭环已交付，见下。

---

## M3 · Intent 抽取、澄清与只读执行 ✅ 已完成（4/4）

到这里才正式接入 question → Intent 的模型调用；编译与校验保持 0 次模型调用，CI 使用回放。

| 小步 | 内容 | 验收物 |
|---|---|---|
| 3.1 ✅ | 严格单轮 `question → Intent` 抽取（离线契约完成） | 20 条固定手写 cassette → 独立 Intent/明确错误；prompt ≤300 字精简目标未达 |
| 3.2 ✅ | 单轮槽位接线：缺时间/指标、多指标与 unresolved 受控请求明确 | 只问一个；技术错误透传；slots_complete 不代表可答，无真实候选歧义消解 |
| 3.3 ✅ | 存储接口/SQLite跨请求状态 + 单槽补答合并/重述/预算/CAS | 10组固定会话与独立进程恢复；续轮只补当前槽位，技术失败不推进 |
| 3.4 ✅ | L5 执行（SQLite 只读 + 行数上限 + 查询预算） | AQ-001 原题和跨进程补答均可离线返回数据 |

### M3.1 落地记录（2026-11-23）

- `understanding/`：稳定最小目录、严格 JSON/schema/日历与可表示性边界、单次注入调用；未知和缺失不补全。
- `tests/fixtures/intent_extraction/cases.yaml` + 20 份固定手写 cassette，分别编写响应/预期；
  provenance=handwritten_fixture，synthetic/handwritten-fixture，tokens=0；真实录制另放 recorded/intent_extraction。
- `demo_extract` 永远严格离线；`record_cassette` 接同一入口，坏响应落盘并返回失败；旧 demo_ask 保持固定桩。
- 新增 228 项，全量 1219 tests、26/26 黄金、10 条评测纪律、8 条业务规则；旧 G-001～G-026 与 AQ 原文件不改。
- 静态 system 1093 字符、业务目录 JSON 373 字符，20 条样本完整消息合计 1541～1558 字符（EX-001：1550）；
  **未达 ≤300 字精简目标**，不通过拆消息伪称达标，不作为 token 统计或真实模型质量/成本/延迟证据。
- ADR-0013 记录边界；当时 M3 为 1/4。后续 M3.2 见下，仍未接状态/执行/M4。

### M3.2 落地记录（2026-11-23）

- `clarify/preparation.py` 单次复用抽取，再调用两项显式必需槽位检查；新 PreparationResult/Clarification 冻结，集合为 tuple。
- 缺指标/时间仅问首项；unresolved 引用首片段且保留全部片段，不猜分类；多指标要求指定一个，不返回部分 Intent。
- slots_complete 只表示必需槽位不缺，未知名称/不支持形状/无权限仍由旧 L3/L4 处理；不写回任何默认值。
- `tests/fixtures/clarification/cases.yaml` 20 条独立手写预期，引用旧 cassette 不改键或响应；`demo_prepare` 严格离线。
- 新 CLI 的澄清正常退出 0，技术错误非零；旧 demo_extract/demo_ask/trace_compile/record_cassette 契约不改。
- 新增 95 项（核心 37、回放/集成 29、CLI 29），全量 1314 tests、26/26 黄金、10 条评测纪律、8 条业务规则保持。
- M3.1 prompt/协议与 20 份 cassette、旧黄金 SQL/AQ 原文件不改；原 prompt ≤300 字目标仍未达，不声称真实模型质量/成本/延迟验证。
- ADR-0014；当时M3为2/4，后续M3.3见下，尚未实现M3.4执行或M4。

### M3.3 落地记录（2026-11-24）

- `clarify/session/`：冻结快照/响应、严格JSON codec、SessionStore协议、内存/SQLite适配器、单槽补答协议与状态机。
- 只补缺失metric/time_range，其余条件保持；多指标/unresolved完整重述；默认最多两次追问，技术错误不推进、预算耗尽不猜值。
- owner/会话ID隔离，目录版本与摘要变化零模型调用要求重启；CAS保护版本更新，模型调用不承诺并发exactly-once。
- `demo_session start/reply/show`固定离线，可在不同进程恢复；SQLite仅存应用状态，不能标记L5业务执行完成。
- 10组独立手写会话清单、14份新固定cassette，旧20份EX/黄金SQL/AQ不改；新增161项，全量1475 tests、黄金26/26、10条评测纪律、8条规则保持。
- 新补答静态prompt717字符，9次固定补答的完整消息1398～1490字符；不是token测量，旧≤300字目标仍未达，不声称真实模型评测。
- ADR-0015；M3为3/4，下一步M3.4业务只读执行/行数上限；无真实模型、联网或外部数据库访问。

### M3.4 落地记录（2026-11-25）

- `query.py` 使用当次 Scope 将 M3.3 接到 L3/L4/L5；仅无校验问题的 SQL 放行，不修改旧校验契约。
- `execution.py` 按只读模式打开独立业务 SQLite，默认返回 100 行、查询预算 2 秒；不建库、不自动重试。
- `seed_sqlite_business` 保存 3 个账期、2 个机构的快照。`demo_query` 严格回放，AQ-001 返回 20210000 元，B001 仅返回 12660000 元；跨进程补答使用新的 Scope。
- 新增 32 项测试，全量 1507、黄金 26/26；未调用真实模型或外部数据库。M3 的离线验收完成。

### M4.1 落地记录（2026-11-25）

- `query.py` 仅将 `symbol_table/UNKNOWN_METRIC` 映射为 `UNANSWERABLE`，将 L3 的 `METRIC_OUT_OF_SCOPE`、`TABLE_NOT_IN_SCOPE`、`COLUMN_NOT_IN_SCOPE` 映射为 `OUT_OF_SCOPE`。
- 两类业务拒绝均返回固定中文消息、会话 ID 和版本，不携带 SQL、物理表列名或内部错误；拒绝不进入 L4/L5。澄清、空结果、校验失败和执行失败保留原契约。
- `demo_query --case EX-009` 可严格离线验证未知指标；新增 4 项测试，全量 1511、黄金 26/26、Ruff 通过。

### M4.2 落地记录（2026-11-25）

- 新增手写黄金 `G-027`～`G-029`，分别覆盖明细不良率、业务过滤与行策略下的不良率、报表单月产品过滤；`AQ-003` 获得黄金对照，`AQ-006` 继续由 `G-015` 覆盖。
- 新增 `G-030` 预期编译拒绝，黄金加载器严格支持 `expected_compile_error`，断言列权限缺失为 `scope/COLUMN_NOT_IN_SCOPE`，不伪造可执行 SQL。
- 黄金门禁从 26 条扩展到 30 条，覆盖 28 种形状标签；评测集交叉覆盖为 8/10。新增 14 项测试，全量 1525、黄金 30/30、Ruff 通过；未调用真实模型或外部数据库。

### M4.3/M4.4 落地记录（2026-11-25）

- 新增 `scripts/evaluate.py`，统一运行抽取 20 条、澄清 20 条、黄金 30 条和 AQ-001 本地 SQLite 端到端样本，共 71 条固定回放评测。
- 输出 `eval/report.json` 与 `eval/report.md`，记录每套通过率、模型回放调用数、耗时、最终状态和覆盖率；报告明确 `replay`、无真实模型、无外部数据库。
- 失败按 `understanding`、`clarification`、`semantic`、`scope`、`compiler`、`validation`、`execution` 等稳定阶段归因；本次 71/71 通过，失败归因表为空。
- 新增 3 项报告测试，全量 1528、黄金 30/30、Ruff 通过。M4 离线门禁、报告和归因完成；真实模型质量评测仍未完成。

---

## M4 · 门禁 + 评测 + 结论 ✅ 离线部分已完成（4/4）

各小步的落地记录在上文 M3.4 之后；真实模型质量评测未做，M0～M4 的离线里程碑到此全部完成。

| 小步 | 内容 | 验收物 |
|---|---|---|
| 4.1 ✅ | 可答性判定（语义层显式存在性，不用向量距离） | 未登记指标 → `UNANSWERABLE`；无权限 → `OUT_OF_SCOPE` |
| 4.2 ✅ | 黄金集扩到 30 条（覆盖 7 种形状 × 口径陷阱） | `eval/golden` 30 条，30/30；评测集覆盖 8/10 |
| 4.3 ✅ | 评测脚本 + 报告（通过率 / 模型调用数 / 回放耗时 / 覆盖率） | `scripts.evaluate` 生成 `eval/report.json` 与 `eval/report.md`（机器产物，不提交），71/71 |
| 4.4 ✅ | **失败归因**：每条错答标注属于哪一类 | 报告中的稳定阶段/错误码归因表 |

**4.4 是项目的核心产出。** 没做这一步，前面所有代码都只是 demo。

---

## M5 · 真实模型基线 ✅ 基线已运行（2026-11-27，M5.1）

| 小步 | 内容 | 验收物 |
|---|---|---|
| 5.1 | 真实模型基线：DeepSeek `deepseek-flash`、关闭思考、20 抽取 + 10 AQ、每题 3 次 | `scripts/evaluate_real.py` + `scripts/call_recorder.py` + `scripts/judging.py`；`eval/real/<run_id>/` 四份报告 |

**M5.1 落地记录（2026-11-27）**

| 交付 | 位置 |
|---|---|
| T0 关闭思考模式 | `app/config.py`、`app/llm/real.py`、`app/llm/factory.py`、`.env.example`；7 项测试 |
| T1 共享判定 | `scripts/judging.py`、`scripts/evaluate.py`；26 项测试，离线 71/71 |
| T2 记录器与预算 | `scripts/call_recorder.py`；7 项测试 |
| T3 运行器与报告 | `scripts/evaluate_real.py`；11 项测试，输出 `report.json`、`report.md`、`calls.jsonl`、`review.md` |
| T4 文档与 PR | 本节、`EVALUATION.md`、`GLOSSARY.md`、`README.md`、`AGENTS.md`、`CONTEXT.md`；PR #1 已 squash 合并为 `5aded78` |
| T5 冒烟 | 2026-11-27：EX-001 单次；接口和格式正常，模型把“截至2026年4月末”放进了 `unresolved`；未改 prompt |
| T6 全量 | 正式基线 run_id `20261127T064047Z-e239b55`：30 题 × 3 次，90 次调用、67,764 token |
| T7 归因 | 61 次失败全部在抽取/澄清；42 次 unresolved 格式、19 次时间/指标缺失；无 compiler/validation/execution/transport 失败 |
| M5.2 测量口径 | 2026-11-27：AQ-006 显式报表、AQ-008 显式 1–4 月、AQ-007 标为 excluded；SQLite 补 202604 报表表；运行器单列排除题 |

**验收状态**：运行器实现完成；前置检查、预算/连续 `transport` 中止、黄金对照、密钥不落盘与 `eval/real/` 忽略已验证。
正式基线（run `20261127T064047Z-e239b55`）抽取全对 30%、多数 35%、尝试 35%；执行全对 20%、多数 30%、尝试 26.67%；
不稳定题 EX-007、EX-010、AQ-005。T6 暴露的两个判定 bug 已由 PR #2（`e239b55`）修正。
**M5.2 测量口径修正**：全量 1587 项测试、离线 71/71；round 1 基线中 AQ-006/007/008 不可直接与后续轮次比较，round 2 未运行。
**EX-004 测量修正记录（2026-11-27）**：经人工确认，将抽取预期与手写 cassette 的 delta 时点统一为当前五月末、基准四月末，消息键和生产逻辑不变；新增独立候选正反判定及严格回放到 SQLite 的数值断言，4 项测试先失败再通过。保留 round 1 的原始报告与当时汇总；EX-004 的旧通过不能证明差值方向正确，无 compiler 阶段失败也不保证全部 SQL 业务语义正确。后续直接跨轮比较除 AQ-006/007/008 外再排除 EX-004，仍保留它参与 round 2。设计与实施见 [Spec](superpowers/specs/2026-11-27-ex004-delta-measurement-design.md) / [Plan](superpowers/plans/2026-11-27-ex004-delta-measurement-plan.md)，当前交付状态与验证结果见 `CONTEXT.md`。

**未完成/下一步**：prompt 迭代（unresolved 逐字与月末归一化）、可配置语义层、PostgreSQL 闭环、本体/ReAct；真实用户准确率未验证。

---

## 里程碑与"要回答的四个问题"的对应

| 问题 | 什么时候能答 |
|---|---|
| 把 SQL 从模型手里拿走，准确率能提高多少？ | **M4.3 离线 + M5.1 基线（已运行，30 题/单模型单版本）** |
| 哪些规则可以代码化，哪些只能留 prompt？ | **M2**（8 条规则的边界就是答案） |
| 问数需要几次模型调用？ | **M3**（编译路径 0 次，理解路径 1 次） |
| "答不了"和"没权限"能分开告诉用户吗？ | **M4.1** |

---

## 明确挂起（不在任何里程碑里）

这些不是"以后做"，是"本项目不做"。真需要时另开项目：

- 前端与交互
- 用户 / 角色 / 认证 / RBAC 表
- 场景与知识库管理后台
- 多数据源连接器（Oracle / MySQL / Trino…）
- 归因分析、图表、指标卡、导出
- 向量检索与重排（ADR-0002 已论证：先量出纯规则能到多少，再决定）
