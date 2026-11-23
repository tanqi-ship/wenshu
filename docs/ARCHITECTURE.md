# 架构

本文只回答一个问题：**一条 SQL 是怎么被造出来的，每一步的输入输出和失败方式是什么。**

决策理由见 `DECISIONS.md`；里程碑见 `ROADMAP.md`。

---

## 一、六层，以及每层的"确定性与否"

| 层 | 模块 | 输入 | 输出 | 是否调模型 | 失败方式 |
|---|---|---|---|---|---|
| **L0** 输入边界 | `engine/semantic`, `Scope` | 配置数据 | `SemanticCatalog`, `Scope` | ❌ | 配置错误（启动时/加载时暴露） |
| **L1** 理解 | `engine/clarify` + `llm/` | `question` | `Intent` | ✅ **1 次** | 抽不出 → 澄清 |
| **L2** 门禁 | `engine/clarify/slots.py`, `engine/coverage/` | `Intent` + 语义层 + `Scope` | 编译 / 澄清 / 拒答 | ❌ | 槽位落不了地 → 澄清或拒答 |
| **L3** 编译 | `engine/compiler/` | `Intent` + 语义层 + `Scope` | `exp.Select` → SQL | ❌ | `CompileError`（**确定性失败**） |
| **L4** 校验 | `engine/validation/` | SQL 文本 | `Problem[]` | ❌ | `Problem` 列表非空 |
| **L5** 执行 | （待定，M3 之后） | SQL | `rows` | ❌ | 驱动异常 / 超时 |

**核心不变式：只有 L1 不确定。**

这带来一个直接结论：**L3/L4 失败时，重试没有意义**——同样的输入永远得到同样的结果。L3 失败要么改配置（L0），要么回归澄清（L2）。详见第五节。

---

## 二、L0：两个输入

### 语义层（知识）

```python
SemanticCatalog:
    metrics:   Mapping[str, MetricDef]      # 指标：code / name / formula / unit / source_tables / synonyms
    relations: tuple[RelationDef, ...]      # 表关系：左右表 / 左右列 / join_type / cardinality
    dimensions: Mapping[str, DimensionDef]  # 维度：编码 → 各口径/粒度的物理列（M1 引入）
    code_map:  Mapping[str, Mapping[str, str]]  # 维度值 → 数据库存储值
```

当前 `SemanticCatalog` 不持有表结构目录；物理表列名称由独立的校验 `Catalog` 管理。
`LevelDef.table` 留空表示指标主表；非空时明确关联维度的来源物理表。

**`DimensionDef` 是 askdb 缺失的那块，也是本项目相对它最重要的新增。** 它的形状：

```python
DimensionDef(
    code="org",
    name="机构",
    # 粒度/口径 → 物理列。缺了这个，模型只能在"财务口径 vs 管户口径"之间猜。
    levels={
        "financial": LevelDef(group_by="org_no", display="org_nm"),
        "customer_management": LevelDef(group_by="belong_org_no", display="belong_org_nm"),
    },
    default_level_rule="实体是网点时 → customer_management，否则 → financial",
    value_kind="high_cardinality",  # high_cardinality 值不能靠向量匹配，必须精确等值
)
```

### Scope（权限）

```python
@dataclass(frozen=True, slots=True)
class RowPolicy:
    table: str
    column: str
    operator: Literal["eq", "in"] = "eq"
    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Scope:
    tables: frozenset[str]
    columns: Mapping[str, frozenset[str]]
    row_policies: tuple[RowPolicy, ...] = ()
```

**`Scope` 是传进来的，引擎不查 RBAC**（ADR-0004）。测试里直接构造，生产里由调用方填。
`scope=None` 表示不限制；显式 `Scope()` 表示没有可见表，L3/L4 均拒绝。
列白名单未配置某表时不限制该表，显式空集合表示一列都不可见。

---

## 三、L1：Intent 是唯一的不确定产物

`Intent` 定义在 `app/engine/semantic/models.py`，**不含任何 SQL 语法**：

```
metric       : 指标的业务名（不是列名）
time_range   : TimeRange(grain=month_end | month | day | quarter | year | range)
org          : 机构短语原文
dimensions   : 维度名元组
filters      : Filter(dimension, value, operator)   ← value 必须是数据库存储值
comparison   : delta | yoy | mom | rate
top_n        : int | None
top_n_grain  : "month" 表示"每月 Top N"
source_mode  : plain | report | union
```

**两个刻意的设计：**

1. `Intent.confidence` 存在但**不参与任何判断**——它只写日志。可答性由 L2 的确定性槽位检查负责。
2. `Filter.value` 必须是**数据库真实存储值**。编码映射在 L2 完成，不在 L3 猜。

---

## 四、L3：编译器内部五步

```
compile(intent, semantic, scope) -> exp.Select
│
├─ ① resolve   符号表查找
│     metric = semantic.metric(intent.metric)          # 查不到 → UNKNOWN_METRIC
│     dims   = [semantic.dimension(d) for d in intent.dimensions]   # → UNKNOWN_DIMENSION
│     values = resolve_code_map(dim, intent.filters)    # → UNKNOWN_CODE
│
├─ ② check     类型检查（askdb 完全没有这一步）
│     for dim in dims: assert dim.code in metric.dimensions   # → DIMENSION_NOT_ALLOWED
│     assert metric.source_tables ⊆ scope.tables              # → METRIC_OUT_OF_SCOPE
│     resolve_comparison_points(intent)                       # M2.1：多时点意图的 4 条显式校验
│
├─ ③ build     造节点
│     metric_projs = build_metric_projections(metric, points)  # 1 列（M1）或 3 列 delta（M2.1）
│     time_where  = build_time_where(intent.time_range)   # 3 种时间形态
│     dim_projs   = [Column(gb), Max(Column(display))]    # ★ 名称字段永远只做 MAX
│     policy_where= [Condition(p) for p in scope.row_policies]
│
└─ ④ assemble  填 4 个空
      select = exp.Select(
          expressions = dim_projs + metric_projs + derived_projs,
          from_       = From(Table(metric.source_tables[0], alias)),
          where       = And(time_where, business_filters, policy_where),
      ).group_by(*group_cols)
  ⑤ render    select.sql(dialect=...) 
```

> **"填 4 个空"这件事**：SELECT 投影 / FROM / WHERE / GROUP BY。
> 代码写死的是这 4 个空的结构，填进去的内容 100% 来自 L0 的数据。
>
> **③ 的投影构造器（M2.1）**：`build_metric_projections` 收的是**已解析的指标定义**与
> **已校验的时点**，不接收 `Intent` —— "哪些形状合法"的判断留在 ② 一处（`comparison.py`），
> 这里只负责造节点。单时点输出 1 列；双时点 `delta` 输出 `cur_balance` / `base_balance` /
> `delta` 三列，时点条件注入到聚合**参数内部**（见 ADR-0007）。

### 每月 Top N 的装配分支（M2.2）

`compiler/topn.py` 在 check 阶段校验 N、排名维度、时间与形状组合，
将闭区间展开为具体月末列表；显式 points 必须全部是月末且不能与区间混用。
这些时点用于逐月分组，与 M2.1 的对比分段聚合分开处理。

装配复用原有指标投影、维度口径、业务过滤、行级权限，随后构造三层查询：

```text
月末快照 + 业务条件 + 权限条件
  → 按完整月末日期与主体编号聚合（名称只做 MAX）
  → ROW_NUMBER：按月分区、指标降序、所有主体编号升序
  → 外层 rn <= N，输出月末/主体/名称/指标，按月末与排名排序
```

使用完整日期分区可区分不同年份；NULL 指标排在有效值后。
缺失月份保持缺失，区间内没有月末时生成恒假时间条件。
多个维度表示对维度组合排名；目前仅支持 plain 分支，每月 Top N 不能与 comparison 混用。
输出列重名或占用内部排名列 `rn` 时显式报错。
具体取舍见 ADR-0008，手写黄金 SQL 见 `eval/golden/G-010.sql`。

### 受限双表关联（M2.3）

`compiler/sources.py` 收集指标来源与实际使用的维度/非空值过滤来源，
最多两表；主表固定为指标第一来源，默认别名 t1/t2（自定义主表别名不与关联别名冲突）。
两表之间必须恰好一条关系，不能用 `relation_between` 取第一条来掩盖歧义。
INNER 可规范化方向并同步反转基数；LEFT 只允许主表为声明左侧。
有效方向须为 many_to_one/one_to_one；未知、一对多、多对多均拒绝。

指标只读主表；分组和业务过滤按 `LevelDef.table` 绑定，名称只 MAX。
只用于过滤的关联维度也会引入 JOIN，值为 None 的过滤不引入多余表。
时间仍约束主表快照；本轮不支持双表与 delta/Top N/report/union 组合。
公式必须是单个表达式，不能夹带 FROM/WHERE/第二条语句；双表公式不能引用关联表度量或未知限定名。

表权限检查覆盖所有实际参与表；`compiler/permissions.py` 在聚合查询装配完成、Top N 包装之前，
检查指标、维度、名称、时间、业务过滤、JOIN 键与行策略引用的物理列。显式列限制下不接受通配符指标。
行策略根据 RowPolicy.table 绑定别名；未使用表的策略不生效也不引入表，原策略顺序保持不变。

**LEFT 的安全语义**：ON 只放关系键；业务过滤和行级权限都在 WHERE。
无右表限制时保留未匹配主表行到 NULL 分组；有右表筛选或权限时，这些行会被排除。
右表空策略生成 WHERE 1 = 0，不能只放 ON 后继续汇总无权主表金额。

**基数不是数据检查**：编译器不读库；many_to_one 声明必须由维表键唯一约束或独立数据验证保证。
不能靠 DISTINCT/任意 MAX 掩盖重复。见 ADR-0009 与 G-011~G-014；L4 仍只解析最终 SQL 文本。

### 错误分类（决定给谁看）

| stage | code 示例 | 谁的问题 | 用户可见文案 |
|---|---|---|---|
| `symbol_table` | `UNKNOWN_METRIC` / `UNKNOWN_DIMENSION` / `UNKNOWN_CODE` | 语义层没覆盖 | "暂不支持「X」这个指标" |
| `type_check` | `DIMENSION_NOT_ALLOWED` | 语义层配置 | "「X」指标不支持按「Y」拆分" |
| `scope` | `METRIC_OUT_OF_SCOPE` / `TABLE_NOT_IN_SCOPE` | **权限** | "您没有「X」的查询权限" |
| `internal` | `AST_INVALID` | **编译器 bug** | "系统内部错误" + 告警 |

**这个分类本身就是产出**：它把"为什么答不了"从一句模糊的失败，变成四个可统计、可归因的口径。

---

## 五、为什么不循环

| | 有循环 | 本项目 |
|---|---|---|
| 谁生成 SQL | 模型，每次可能不同 | 编译器，纯函数 |
| 失败后 | 再试一次（可能偶然对） | **不重试**——同样输入同样结果 |
| 唯一能动结果的是 | 重试次数 | 改 `Intent`（回 L2）或改配置（回 L0） |

**编译失败的三种正确响应**（互斥）：

| 失败原因 | 响应 | 触发条件 |
|---|---|---|
| 覆盖不足（指标/维度不存在） | **降级到长尾路径**（M4 之后才有） | 对应 `symbol_table` 类错误 |
| 意图不完整（用户没说清） | **澄清**，只问缺的那一个槽位 | L2 槽位检查 |
| 配置有 bug | **报错给维护者，不重试、不降级** | `type_check` / `internal` |

> 把"重试"从"缩短失败率的手段"降级为"只在长尾路径使用"，是这个架构最大的一处简化。

---

## 六、L4：校验层

校验器只接收 **SQL 文本**，不接收编译器的任何中间产物。这条是硬的：

> 否则编译器把自己的中间结果传给校验器，编译器的 bug 会被自己的校验放过。

规则清单见 `app/engine/validation/rules.py`。每条规则：

- 稳定 `code`（如 `NAME_FIELD_IN_GROUP_BY`）→ 评测按 code 聚合，定位退化
- 业务规则编号（如 `C-11`）→ 业务方能对着《规则汇编》复核
- 纯函数 + 单测（输入 SQL 字符串，断言 `Problem[]`）

**目前 6 条，M2 补到 8 条**（新增 `R-SCOPE-001` 范围校验、`R-ORG-001` 机构名等值匹配）。

---

## 七、和 askdb 的结构性差异（一句话版）

| 维度 | askdb | wenshu |
|---|---|---|
| SQL 谁写 | 模型自由写 | 编译器展开 |
| 模型调用次数 | 13~14 次串行 | **1 次** |
| 业务规则 | 5,500 字 prompt | 代码 + 单测 |
| 失败可重试 | 是（4 次） | **否**（确定性） |
| 维度口径 | prompt 里的一行散文 | `DimensionDef.levels` 字典 |
| 权限注入点 | 分散多处（曾泄漏） | **一处**（L3 的 ④） |
| 拒答原因 | 11 种 reason 散在 4 个节点 | 4 类 stage，可统计 |

**注意：wenshu 不是"更好的 askdb"。** 它放弃了 askdb 擅长的东西（澄清广度、周边功能、中文业务规则沉淀量），只深挖一件事：**把 SQL 生成这件事变成确定的**。
