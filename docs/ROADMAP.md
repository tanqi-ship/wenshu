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

> ~~待改造：双引擎对照门禁~~ → **M1.5 已完成改造**：因不迁移 askdb（ADR-0003），baseline 永远产不出来。
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
| 1.4 | `Scope` 三注入点（表 / 列 / 行级 WHERE） | ✅ 表+行级；列白名单 M2 | `engine/scope.py` + `compiler/conditions.py` |
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

## M2 · 形状扩展

按"出错代价从高到低"排序，先做最容易算错的。

| 小步 | 形状 | 对应规则 | 用例 | 状态 |
|---|---|---|---|---|
| 2.1 | 双时点对比 `delta`（同一 SELECT + `CASE WHEN` 分段聚合） | `R-COMP-001` | AQ-004 | ✅ **完成** |
| 2.2 | 每月 Top N（窗口函数 + 外层过滤） | `R-TOPN-001` | AQ-008 | ⬜ |
| 2.3 | 多表 JOIN（`RelationDef` 驱动，≤2 表） | `R-TBL-001` | AQ-007 | ⬜ |
| 2.4 | 报表分支（禁 JOIN，多报表 `UNION ALL`） | `R-JOIN-001` | AQ-006 | ⬜ |
| 2.5 | 占比（分子 + 分母是整体） | 新增 `R-RATIO-001` | — | ⬜ |
| 2.6 | 机构名等值匹配（禁 LIKE / 前缀 / 包含） | 新增 `R-ORG-001` | — | ⬜ |

**验收**：7 种形状各有 1 条黄金集用例通过；`rules.py` 8 条规则全有单测。

**预期规模**：每种形状约 30~40 行 + 1 个单测，总增约 250 行。

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

---

## M3 · Intent 抽取 + 澄清接线

到这里才允许出现第二个模型调用点。

| 小步 | 内容 | 验收物 |
|---|---|---|
| 3.1 | `question → Intent` 抽取（prompt 目标 ≤ 300 字） | cassette 回放测试：10 个问题 → 10 个 Intent |
| 3.2 | 槽位检查接线：缺时间 / 缺指标 / 指标歧义 | 澄清文案只问缺的那一个槽位 |
| 3.3 | 追问状态持久化（跨请求） | 集成测试：第 2 轮不必重跑全链路 |
| 3.4 | L5 执行（SQLite 只读 + 行数上限） | 端到端：问题 → SQL → rows |

**M3 完成的标志**：端到端能回答 AQ-001，`WENSHU_LLM_MODE=replay` 下可复现。

---

## M4 · 门禁 + 评测 + 结论

| 小步 | 内容 | 验收物 |
|---|---|---|
| 4.1 | 可答性判定（语义层显式存在性，不用向量距离） | 单测：指标不存在 → `UNANSWERABLE`；无权限 → `OUT_OF_SCOPE` |
| 4.2 | 黄金集扩到 30 条（覆盖 7 种形状 × 口径陷阱） | `eval/cases/*.yaml` 30 条，全部有 `why_hard` |
| 4.3 | 评测脚本 + 报告（准确率 / 编译失败率 / 模型调用数 / 延迟） | `eval/report.md` |
| 4.4 | **失败归因**：每条错答标注属于哪一类 | 一张归因表 |

**4.4 是项目的核心产出。** 没做这一步，前面所有代码都只是 demo。

---

## 里程碑与"要回答的四个问题"的对应

| 问题 | 什么时候能答 |
|---|---|
| 把 SQL 从模型手里拿走，准确率能提高多少？ | **M4.3** |
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
