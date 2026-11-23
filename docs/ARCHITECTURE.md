# 架构

本文只回答一个问题：**一条 SQL 是怎么被造出来的，每一步的输入输出和失败方式是什么。**

决策理由见 `DECISIONS.md`；里程碑见 `ROADMAP.md`。

---

## 一、六层，以及每层的"确定性与否"

| 层 | 模块 | 输入 | 输出 | 是否调模型 | 失败方式 |
|---|---|---|---|---|---|
| **L0** 输入边界 | `engine/semantic`, `Scope` | 配置数据 | `SemanticCatalog`, `Scope` | ❌ | 配置错误（启动时/加载时暴露） |
| **L1** 理解 | `engine/understanding/` + `llm.protocol` | `question` + 业务目录 | `ExtractionResult(Intent, Completion)` | ✅ **1 次** | `ExtractionError` / 原模型异常；单轮准备层仅选择性转澄清 |
| **L2** 门禁 | `clarify/preparation.py` + `slots.py`（已接线）；`coverage/`（待 M4） | 已验证抽取结果 + 业务目录；M4 再接语义存在性/权限决策 | 当前仅必需槽位齐全 / 单个澄清 | ❌（判断不额外调用模型） | 技术错误透传，不将槽位齐全当作授权或可答 |
| **L3** 编译 | `engine/compiler/` | `Intent` + 语义层 + `Scope` | `exp.Query`（Select / Union）→ SQL | ❌ | `CompileError`（**确定性失败**） |
| **L4** 校验 | `engine/validation/` | SQL 文本 | `Problem[]` | ❌ | `Problem` 列表非空 |
| **L5** 执行 | （待实现，M3.4） | SQL | `rows` | ❌ | 驱动异常 / 超时 |

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
    reports:   Mapping[str, ReportDef]       # 指标 → 预计算报表来源、固定维度和显式月份分表
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
    default_level="financial",  # 当前实际字段；M3.1 不自动切换非默认口径
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
org          : 兼容旧结构保留；M3.1 始终 None，非空编译拒绝
dimensions   : 维度名元组
filters      : Filter(dimension, value, operator)   ← 业务原值或存储码，L3 按登记翻译
comparison   : delta | yoy | mom | rate
share        : ShareSpec(dimension) | None  # 同一指标按一个业务维度占上下文整体的比例
top_n        : int | None
top_n_grain  : "month" 表示"每月 Top N"
source_mode  : plain | report | union
```

**两个刻意的设计：**

1. `Intent.confidence` 存在但**不参与任何判断**——它只写日志。可答性由 L2 的确定性槽位检查负责。
2. `Filter.value` 可以保留业务原值或存储编码，L3 按语义层精确登记做确定性翻译，不猜值。
   high_cardinality 只 trim 两端空白；未知/歧义/缺配置拒绝。普通 coded 保持既有契约。
   M3.1 仅接受 eq/in 且拒绝 null/空 IN/非字符串，不能借旧的 None 跳过契约删过滤。
   具体机构进 filters；全行不制造过滤，也不改变外部 Scope。非空 Intent.org 普通/报表路径拒绝，share 保持自身拒绝。

### M3.1 单轮抽取边界（已实现）

`understanding/context.py → 一次 llm.complete → schema.py → Intent`；公共入口为
`await extract_intent(question, catalog, llm=client)`，返回 `ExtractionResult(intent, completion)`。
依赖仅到 semantic 与 llm.protocol，不读 env、不构造客户端、不接编译器/校验器/执行层。

模型只见指标名/同义词/单位、维度名/同义词/默认口径键；无公式、物理表列、JOIN、报表分表、code_map 或 Scope。
排序与 JSON 稳定；按实体查找的去空白 + casefold 检查同命名空间的别名/名称/目录键冲突，
目录键参与冲突检查但不发给模型；该规范化不应用于机构过滤值。

严格单一 JSON、所有层级字段白名单、重复键/非有限常量拒绝、嵌套最多 64 层；不剥围栏或修复。
问题/目录/响应分别最多 4096/32768/32768 字符；调用固定 temperature=0、max_tokens=2048、timeout_s=30，失败不重试。
先完整 schema/日期验证，再拒绝多个不同指标，最后拒绝 unresolved；错误有稳定 EXTRACTION_* code 与定位。
根 metrics/unresolved 必填，缺指标/时间可保留；未知名称不替换；含糊/相对时间用原问题片段 unresolved，不读时钟。
Intent.raw 只诊断，confidence 不作判断；格式正确不证明语义正确或可答。具体结构见已批准的 M3.1 规格。

20 条固定 handwritten_fixture 的响应与预期分别手写，strict replay 提示词漂移报 ReplayMiss，不联网。
有效样本仅在集成测试串 L3/L4；录制脚本需显式 real，坏响应 finally flush 且失败非零，来源另标 recorded_real。
静态 system 1093 字符，当前业务目录 JSON 373 字符，20 个样本完整消息 1541～1558 字符；
≤300 字目标未达，不把字符数冒称 tokens。未进行真实模型或外部数据库验收。
M3.1 本身不做澄清；M3.2 接线见下。M3.3 状态、M3.4 执行和 M4 可答性门禁仍未实现。

### M3.2 单轮准备与澄清（已实现）

`await prepare_question(question, catalog, llm=client)` 复用一次 extract_intent，返回冻结的 PreparationResult：
status、原 ExtractionResult 或 None、missing_slots 元组、Clarification 或 None。
判断只把 Intent.metric/time_range 显式映射给 check_slots 的两项专用 SlotSpec，
不传 raw/confidence，不使用旧默认清单、默认说明或 ClarifyState 预算接口。

- 缺指标/时间：status=needs_clarification，保留原抽取结果，按 metric → time_range 只问首项。
  指标示例按稳定目录键取至多 3 个已登记业务名；时间示例只含固定绝对日期/范围，不读当前日期。
- 必需槽位不缺：status=slots_complete，保留原 Intent/Completion，不表示可答、可编译、已授权或有数据。
- EXTRACTION_UNRESOLVED：仅在片段契约完整时，引用首片段、保留全部片段、slot=None；不按词语猜类别。
- EXTRACTION_MULTI_METRIC_UNSUPPORTED：请求指定一个指标，不选第一项、不重新解析被拒绝响应。
- 后两种分支 extraction=None，missing_slots=() 表示未运行槽位检查，不伪造部分 Intent、Completion 或统计。
- 非目标错误（包括未来未知 ExtractionError）和模型/ReplayMiss 原对象透传，不增加修复或追问生成调用。

slots_complete 分支可由调用方继续尝试 L3/L4；needs_clarification 不编译。
未知指标/维度/机构、yoy/mom/rate、全局 Top N 和空 Scope 在独立集成测试中仍由原编译器拒绝。
准备层不接收 Scope、不写回 org/日期/过滤/来源/对比/排名/占比默认值，不导入编译、校验、coverage、factory 或执行模块。
旧槽位/预算接口只保留兼容，旧“按最可能口径处理”文案不是新路径的回退授权。

`tests/fixtures/clarification/cases.yaml` 独立手写准备层预期，引用未改的 20 条旧 EX 回放；
测试不刷新消息键或响应。demo_prepare 即使 real 环境也只 strict replay，不执行 SQL。
新 CLI 的澄清属于正常协议结果（退出 0），技术错误非零；demo_extract 旧错误退出码保持。
新增 95 项测试，全量 1314、黄金 26/26；本轮只标 M3.2 完成，不实现状态、补答合并或执行。
单轮独立调用不能处理“第二轮只答一个日期”的会话合并，这属于下一步 M3.3。

---

## 四、L3：编译器内部五步

以下展开无 share 的明细 plain 路径；占比和报表在业务指标解析后、普通来源解析前分流，见 M2.4/M2.5 小节。

```
compile_intent(intent, semantic, scope) -> exp.Query
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

### 显式报表来源与多月合并（M2.4）

`ReportDef(metric_code, value_column, month_column, dimensions, partitions)` 独立于明细公式。
`SemanticCatalog.build(reports=...)` 在 L0 验证指标、维度、合法年月/标识符、无外部维度来源及输出名冲突；
L3 再验证手工构造的目录，配置错误转 `REPORT_CONFIG_INVALID`。不保存 SQL，也不扫描/猜测表名。

- `source_mode=report/union` 均进 `compiler/reports.py`；plain 完全沿用原路径。
- `report_time.py` 将 month 日期锚点、真实 month_end 或月初到月末完整区间规范成排序、去重的 YYYYMM。
  不混用点与区间，不接受日/季度/年度或部分月份；任一月份未登记即 `REPORT_PARTITION_MISSING`。
- 请求维度按业务 code 去重后必须与报表粒度完全一致；即使已有过滤也不能省略维度或聚合预计算比例。
  过滤只支持该粒度的 eq/in，共用码值转换；None 不过滤、空 IN 恒假，不借用明细列绑定。
- 每个分支投影 `月份 AS stat_month`、请求顺序的维度编号/可选名称、`值列 AS metric.code`。
  直接读取，不 SUM/AVG/MAX/GROUP BY/DISTINCT/COALESCE/*100，也不与 JOIN/对比/Top N 混用。
- 逐表限定真实月份列；一月 Select，多月 `exp.Union(distinct=False)`，统一外层按 stat_month 和维度编号排序。
  分支分别校验 FROM，不能要求 Union 根具有 FROM。返回类型拓宽到 Query，SQL 文本接口不变。

**权限**：先解析全部物理来源并检查表权限，逐分支复用 `SourcePlan` 的单表字段接口、行策略和列白名单；
不进入 JOIN 规划器，因此合法三个月 UNION 不受明细双表上限影响。只要求实际报表权限，不要求底层明细权限。
任一表/列拒绝令整条失败；未用表策略忽略。空行策略保留分支的 `WHERE 1=0`，其他合法月份可返回数据。
每个分支别名独立（默认 t1、t1_2、t1_3），防止独立 L4 的列名目录被后续分支覆盖。

**知识/数据责任**：源表唯一键/外部数据检查保证每月每组只有一行，编译器不连接数据库证明唯一性或行数。
同名列不是同一口径的证据；产品标准 npl_rate 的来源显式登记，不能替换为逾期口径表。
G-015~G-018 以及 SQLite 结果验证这些边界；既有 G-001~G-014 不变。详见 ADR-0010。

### 同口径整体占比（M2.5）

`Intent.share=ShareSpec(dimension="产品")` 是独立业务意图，不复用 comparison=rate。
字段追加到 Intent 末尾，严格加载 share.dimension；未给 share 的旧输入不改变 SQL 或错误码。
先派发到 `compiler/share.py`，保证 report/union/Top N 等分支不会默默忽略占比请求。
首版只接受唯一明细来源、真实单个月末、一个拆分维度和普通可加 SUM（含条件 SUM）。
拒绝已有比例、DISTINCT/嵌套聚合、外部列、子查询公式和形状混用，不放宽旧 JOIN 规则。

三层职责固定：

1. **分组**：物理 WHERE 为时间 → 非拆分维度上下文 → 当前表全部行策略；按编号分组、名称只 MAX。
2. **整体**：原样传递各组金额，`SUM(分组金额) OVER () AS total_value`；不分区、不累计、不提前筛选或 LIMIT。
3. **展示**：占比维度的业务 eq/in 过滤在这一层；输出编号/可选名称/原金额/整体/share，按编号排序。
   占比使用小数提升和 `NULLIF(total_value, 0)`，不乘 100、不取整、不填补 NULL。

同一 prd_no 列可能同时出现在行权限和展示过滤；按输入来源区分，不能按列名全部外移。
最内层构造后检查所有实际物理列权限；无关表策略不引入 JOIN，空行权限在分组前恒假、结果零行。
内层/中层/外层别名避开实际来源与调用者别名，防止派生目录掩盖物理未知列。
Scope=None 不限制、显式空 Scope 拒绝；整体只来自可见数据，不承诺全库分母。
占比的非空 Intent.org 必须先规范到 filters；M2.6 同时为普通/报表入口补齐显式拒绝，不猜测口径。

**数值**：整数 300/1000 必须是 0.3；若产品行权限只允许总额 400 的产品，比例为 0.75。
零/NULL 整体返回 NULL；缺组、缺时点、无权限不补行。原数据的 NULL、零、负值不被钳制；负金额比例未必在 0~1。

**独立 L4**：`validation/ratio.py` 的 R-RATIO-001 从 SQL 文本重新识别三层链、同一金额、空窗口、
除法保护和统计时点；用原始 Scope 独立识别必须保留在底层 AND 中的行策略。
拆分列的非权限取值条件进入底层时报 RATIO_DENOMINATOR_FILTERED；丢权限报 RATIO_SCOPE_POLICY_MISSING；
其他不合法规范形状报 RATIO_SHAPE_INVALID。不 import 编译器或语义目录，不接收拆分计划。
只在 share 存在时触发，继续运行现有规则；业务维度/公式是否选对和非拆分上下文是否完整，仍靠知识、黄金及结果测试。
见 ADR-0011、G-019~G-022；这不是通用 SQL 等价证明或完整生产权限系统。

### 机构值精确解析（M2.6）

`DimensionDef.value_kind="high_cardinality"` 显式启用严格登记，普通 coded 不变。
`semantic/exact_values.py` 提供中立配置检查、候选索引和原因异常，不 import L3 或 SQL；
`SemanticCatalog.build` 验证标记及已提供映射，允许暂缺/空映射，仅实际解析业务值时报告缺失。
别名与存储编码共享 token 空间，收集全部候选，不能优先返回键命中；只 trim，不删内部空白或 casefold。
已登记简称与编码等幂解析；未知/歧义/非法配置拒绝，手工 replace 目录也重新检查，不静默返回零结果。

`compiler/conditions.py` 为各形状复用严格 eq/in，IN 任一项失败整条拒绝；None 不过滤、空 IN 恒假。
外部 Scope 是授权存储值，不进业务别名翻译；财务/管户默认列不变，不推导下辖机构。
普通/报表未规范 Intent.org 报 ORG_CONTEXT_UNRESOLVED，share 保留 SHARE_CONTEXT_UNRESOLVED。
演示目录仅显式登记 fixture 全称、示例简称和 JOIN 样本编码，旧“两江分行 → O003”保留为历史演示别名。

新增稳定错误：UNKNOWN_CODE / AMBIGUOUS_CODE（symbol_table）、EXACT_VALUE_MAP_MISSING /
EXACT_VALUE_MAP_INVALID（type_check）、EXACT_FILTER_UNSUPPORTED / ORG_CONTEXT_UNRESOLVED（intent）。
缺配置不透传、歧义不打分裁决、不从名称前缀回退。见 ADR-0012 与 G-023~G-026。

### 错误分类（决定给谁看）

| stage | code 示例 | 谁的问题 | 用户可见文案 |
|---|---|---|---|
| `symbol_table` | `UNKNOWN_METRIC` / `UNKNOWN_DIMENSION` / `UNKNOWN_CODE` | 语义层没覆盖 | "暂不支持「X」这个指标" |
| `type_check` | `DIMENSION_NOT_ALLOWED` / `EXACT_VALUE_MAP_MISSING` | 语义层配置 | "指标/维度组合或严格值登记尚未配置" |
| `intent` | `ORG_CONTEXT_UNRESOLVED` / `EXACT_FILTER_UNSUPPORTED` | 输入尚未规范或能力不支持 | "请先明确机构口径并规范到过滤条件" |
| `scope` | `METRIC_OUT_OF_SCOPE` / `TABLE_NOT_IN_SCOPE` | **权限** | "您没有「X」的查询权限" |
| `internal` | `AST_INVALID` | **编译器 bug** | "系统内部错误" + 告警 |

**这个分类本身就是产出**：它把"为什么答不了"从一句模糊的失败，变成五个可统计、可归因的口径。

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

规则清单见 `app/engine/validation/rules.py`；R-RATIO-001 位于 `ratio.py`，R-ORG-001 位于 `org.py`。每条规则：

- 稳定 `code`（如 `NAME_FIELD_IN_GROUP_BY`）→ 评测按 code 聚合，定位退化
- 业务规则编号（如 `C-11`）→ 业务方能对着《规则汇编》复核
- 纯函数 + 单测（输入 SQL 字符串，断言 `Problem[]`）

**目前 8 条**：原 6 条加 `R-RATIO-001` 与 `R-ORG-001`。
Scope 的表范围和 L3 列/行策略已实现，但不把这些现存检查另算作已交付的独立 R-SCOPE-001 规则。

**R-ORG-001 独立边界**：`ValidationPolicy.org_columns` 是复制冻结的 schema.table → 列集合，
默认保护演示明细及 dim_org 的机构列；其他 schema、改名字段、报表列须显式配置，空映射是可信方明确退出。
规则不 import L3/语义目录、不接收 code_map，无 Intent 也检查；不按列名片段或字符串“支行”猜机构。
`org_sources.py` 以独立 Catalog 和查询作用域传递“普通/精确/已变换/无法确认”状态，
覆盖直接投影/重命名、CTE、派生列、UNION 按位置合并及单源星号；保护不能经函数或混合分支洗掉。
仅允许原始机构列与字符串 EQ（双向）/IN，或直接列等值 JOIN；真实 %/_ 字符在等值中不禁。
WHERE/HAVING/JOIN ON/CASE/FILTER 等条件均检查；窗口控制不当标识符来源，COUNT/SUM/AVG 不误标。
递归机构输出、相关作用域、LATERAL 表值参数和多源歧义不能确认时 fail-closed；不建设通用血缘平台。
非精确匹配报 ORG_MATCH_NOT_EXACT，来源不明报 ORG_MATCH_SOURCE_UNRESOLVED。
独立规则证明匹配形状，不证明编码登记、业务唯一性、默认口径正确或完整权限无遗漏；这些仍需知识及结果测试。

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
| 拒答原因 | 11 种 reason 散在 4 个节点 | 5 类 stage，可统计 |

**注意：wenshu 不是"更好的 askdb"。** 它放弃了 askdb 擅长的东西（澄清广度、周边功能、中文业务规则沉淀量），只深挖一件事：**把 SQL 生成这件事变成确定的**。
