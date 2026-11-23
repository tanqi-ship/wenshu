# M3.1：单轮意图抽取与严格模型输出边界设计

日期：2026-11-23

状态：2026-11-23 用户已复核并批准书面规格；M3.1 已完成离线契约验收，未验真实模型。
基线：`d9fb911`，M2 已完成 6/6；991 项测试、26/26 黄金用例、8 条独立业务规则。

## 1. 目标与已确认边界

接通单轮 `question → 模型抽取 → 严格校验 → Intent`，不是直接让模型生成 SQL。
调用方注入现有 LLMClient，抽取层只调用一次，不自行重试、修复 JSON 或构造模型客户端。
本轮以 FakeLLM / 严格 ReplayLLM 离线验收，不调用真实模型，不联网、不访问外部数据库。

- 复用既有 Intent、编译器、Scope 和独立 L4；不扩展任何 SQL 形状。
- 缺指标、缺时间可保留缺失；未知业务名称不替换成最接近的已登记项。
- 多指标、无法表示或未确认的语义不能通过丢字段、只取第一项、补默认日期来伪装成功。
- 不接 M3.2 澄清编排、M3.3 跨请求状态、M3.4 查询执行或 M4 可答性决策。
- 不引入向量检索、模型置信度门禁、工具调用协议或供应商特有结构化响应依赖。
- 本轮无界面和视觉选择，不需要可视化工具。

首版时间能力仅覆盖有明确年份的绝对日期、月末及明确起止区间。
相对时间、缺年份的月份、含糊的统计时点不使用系统日期补全，作为未解决片段返回错误；
不把“上半年余额”擅自解释成六月末，也不把月末快照改成跨月余额累加。
后续若支持相对时间，须显式传入参考日期并单独设计；当前接口不读取主机时钟。

## 2. 方案选择

| 方案 | 收益 | 风险/成本 | 结论 |
|---|---|---|---|
| 模型直接返回 Intent 并复用配置加载器 | 改动少 | 现有加载器会 str/int 等转换，不能作为不可信响应的唯一边界；单指标结构易丢多指标信息 | 不采用 |
| 严格抽取结构 → 确定性转换 → 既有 Intent | 边界可测，保留缺失/未知，不破坏旧配置与编译契约 | 需新增聚焦的解析与转换模块 | 用户已确认 |
| 扩展 LLMClient，依赖供应商结构化输出/工具调用 | 接口侧可加格式约束 | 改动协议、兼容及回放面，仍不能证明语义正确 | 本轮不做 |

严格校验证明格式和可表示性，不证明模型真正理解了用户，也不证明业务可答。
供应商未来提供格式约束时，本地严格边界仍须保留。

## 3. 模块与调用方向

拟新增 `app/engine/understanding/`，保持职责聚焦：

- `context.py`：从 SemanticCatalog 提取最小业务目录并构造稳定消息。
- `schema.py`：严格 JSON、字段/时间验证、可表示性检查与 Intent 转换。
- `extractor.py`：异步调用一次 LLMClient，串联消息、解析和结果；不编译或执行 SQL。
- `errors.py` / `__init__.py`：稳定错误与轻量公共接口。

公共接口：`async extract_intent(question, catalog, *, llm) -> ExtractionResult`。
ExtractionResult 包含转换后的 Intent 与原始 Completion，供调用方取得文本、模型和 token 元数据。
Intent.raw 保存已校验的抽取结构作诊断，不赋予权限、不参与 SQL 生成；confidence 保持默认，不作判断。
失败抛 ExtractionError；模型传输类异常及 ReplayMiss 沿用原异常，不吞掉或包装成空 Intent。

依赖方向：understanding → llm.protocol / semantic；不依赖 compiler、validation、coverage 或执行层。
业务层不得自己调用 factory/build_llm、读取环境变量、选择 real 或绕过注入客户端。
在独立集成测试中可以串接 `extract_intent → compile_sql → validate_sql`，不在抽取模块里编排。
旧 `scripts/demo_ask.py` 仍标为固定问题桩，不悄悄更换它的 CLI 契约。

## 4. 给模型的最小上下文

上下文只包含指标的业务名称、同义词、单位，以及维度的业务名称、同义词和默认口径键。
这些是业务配置，不是物理选表指令；原有目录不被修改。

明确不发送：指标公式、表名/列名、JOIN 关系、报表物理分表、code_map 全量机构登记、
Scope 的表列白名单/行策略、数据库连接信息、密钥或应用环境变量。
模型只保留机构原文或已知业务值，码值翻译继续交给 L3 已验收的登记机制。

- 按稳定键排序目录和 JSON 字段，不依赖 Mapping 插入顺序或当前时间。
- 同一规范化实体别名若属于多个不同指标或不同维度，报 CONTEXT_INVALID，不用目录顺序裁决。
  冲突检查沿用实体查找的去空白 + casefold，并考虑 L3 可匹配的目录键（虽不发送这些键）；
  按指标/维度各自命名空间进行，不把“机构”维度与同名指标混为一类。
  这只用于配置实体的冲突检查，不作用于机构等业务过滤值。
- 未知业务词允许出现在响应中；目录是理解上下文，不是让模型强选某个已知指标的枚举。
- 默认口径只用于说明既有默认值；明确要求无法通过当前 Intent 表示的非默认口径须放入 unresolved，
  不临时改 DimensionDef.default_level，也不发明 `机构:某口径` 字符串当作已支持维度。

两条消息：稳定 system 指令 + user JSON 数据（question、catalog、协议版本）。
问题与目录都按数据放入 JSON；这不是“提示注入已经被证明解决”的声明。
安全边界仍是字段白名单、纯数据 Intent、L3 权限注入和独立 L4。

ROADMAP 的 prompt ≤300 字保留为精简目标，不以牺牲明确输出结构与失败边界强行满足。
实施时同时记录静态指令长度和完整消息长度（含目录/结构），实际超出则如实说明，
不通过拆成多条消息声称总 prompt 达标，不把字符数冒称 token 数。

## 5. 模型输出结构

新增模型侧抽取结构，不改现有配置加载器的键、默认值或兼容转换。
根对象必须显式给出 metrics 和 unresolved；其他字段省略时使用下列固定缺省值。

```json
{
  "metrics": ["贷款余额"],
  "time": {
    "grain": "month_end",
    "points": ["2026-04-30"],
    "raw": "2026年4月末"
  },
  "dimensions": [],
  "filters": [
    {"dimension": "机构", "operator": "eq", "value": "渝北", "raw": "渝北"}
  ],
  "comparison": null,
  "top_n": null,
  "top_n_grain": null,
  "source_mode": "plain",
  "share": null,
  "unresolved": []
}
```

### 根字段

| 字段 | 类型/缺省 | 转换与边界 |
|---|---|---|
| metrics | 必填，字符串数组 | 指标业务短语；去除两端空白后去掉完全相同的重复项，不作向量/模糊归并 |
| time | 对象或 null；缺省 null | 严格日期结构；缺失不自动补今天/最近月末 |
| dimensions | 字符串数组；缺省 [] | 未知维度原文保留，不从目录取最接近项 |
| filters | 过滤对象数组；缺省 [] | 首版只允许 eq/in，保留业务值给 L3 处理 |
| comparison | null 或 delta/yoy/mom/rate；缺省 null | 表达意图不等于支持 SQL；保留给后续既有校验，不降级成 delta |
| top_n | null 或正整数；缺省 null | bool、数字字符串和浮点都不接受 |
| top_n_grain | null 或 month；缺省 null | 有粒度无 N 拒绝；全局 Top N 不偷偷改为每月，是否支持仍由既有编译器拒绝 |
| source_mode | plain/report/union；缺省 plain | 逻辑来源请求，不是物理选表；仅有明确报表请求才抽为 report/union，不因目录有报表自动切换 |
| share | null 或 {dimension: 非空字符串}；缺省 null | 复用既有 ShareSpec，不把变化比率当整体占比 |
| unresolved | 必填，字符串数组 | 当前结构无法表达或未确认的原问题片段；非空时不输出可直接使用的 Intent |

metrics、dimensions、unresolved 必须是数组，不能用字符串或 null 冒充；元素须是非空字符串，不做 str() 转换。
metrics 为空时得到 Intent.metric=None；单个业务短语去除两端空白后保留；多个不同短语报 MULTI_METRIC_UNSUPPORTED。
同义但不完全相同的多个短语也不在首版自动合并；需要明确多指标/歧义处理后再扩展。
用户问未知指标时允许单个未知短语，不在 L1 假装实现 M4 的 UNANSWERABLE 判定。

unresolved 项必须为非空原问题片段并能在 question 中找到；该数组描述事实，不是模型控制的 ready/complete 开关。
非空直接报 UNRESOLVED，错误保留片段供未来澄清；本轮不生成追问，不自行忽略后继续编译。
无法表达的多口径、公式式临时指标、相对/含糊时间等应放入此数组。
仍不能静态证明模型没有漏写某个片段；这种漏提取属于未来真实模型评测要测的语义错误。

### 机构和全行

根 org 不在白名单内；转换后的 Intent.org 始终 None。
具体机构应成为 filters 中的“机构/上级机构”业务过滤，允许保留未知机构原文，交 L3 报 UNKNOWN_CODE。
“全行”不填到 org，也不制造“机构=全行”过滤；外部 Scope 仍照常限定可见范围，不代表绕过权限。
不得把未知机构删除以退回全行，或自行展开成名称前缀、下辖编码列表。

### 过滤字段

仅允许 dimension、operator、value、raw 四个键；dimension 为非空字符串，raw 可省略为 ""。
operator 省略为 eq；只允许 eq/in，其他运算符不能落到既有普通 coded 路径的 EQ 兜底。
eq.value 必须是非空字符串；in.value 必须是非空的一层字符串数组，逐项非空。
null、空 IN、数值、布尔、嵌套容器、单字符串冒充 IN 数组一律拒绝，不借现有 None 跳过契约丢条件。
保留原始字符串值（不删除内部空白或 casefold），L3 自己做已登记业务值翻译和安全字面量渲染。
Filter.raw、time.raw 若提供，必须为字符串；不把 raw 文本当 SQL 或后续指令执行。

### 时间字段

只允许 grain、points、start、end、raw；grain 为既有合法枚举。
points 省略为 []，否则须为 ISO `YYYY-MM-DD` 字符串数组；start/end 缺省 null，二者同时为 null 视为未给区间，
仅给单边或一边 null 拒绝；完整区间不能与非空 points 混用。
time 非 null 时必须有非空 points 或完整区间，真实日期与起止正序由代码验证。
month_end/day 必须使用非空 points，不能借用 start 当时点；month_end 的点还必须确为月末。
range 必须使用完整区间；month/quarter/year 可使用点或完整区间，但不改变既有编译能力。
不修日期、不丢边界，不沿用宽松时间编译中可能忽略额外字段的行为。
month 可表示明确报表月份；quarter/year 等字段即使可表示，也不因此放宽现有来源/时间能力。
字符串日期不接受 bool、时间戳、数字、非法日历日或依靠宽松解析接受的无横杠写法。
不由代码补缺少的年份/边界，不把未知 grain 改成 range，不丢掉冲突日期字段。

明确单个月末可由模型抽为具体日期并由代码验证日历正确，但该日期是否忠实于原问题仍是 L1 的不确定性。
只说“2026年4月余额”、没有明确统计时点且不能唯一理解时，应保留为 unresolved，而非默默决定月末或月累计。
不把全年每月请求擅自截到今天；明确绝对范围可包含未来日期，真实数据是否存在不在本阶段证明。

## 6. 严格解析、转换与调用预算

顺序固定：输入/上下文检查 → 构造消息 → 一次 complete → 响应类型/长度检查 → 严格 JSON → schema/日期 → 可表示性 → Intent。

- 只接受一个 JSON 根对象；允许首尾空白，不剥 Markdown 围栏，不截取正文中看起来像 JSON 的子串。
- 所有层级未知键拒绝；sql、table、formula、scope、confidence、org 等不会被静默忽略。
- 重复 JSON 键拒绝（含嵌套），NaN/Infinity/-Infinity 拒绝；空响应、解析深度异常不变成空 Intent。
- 不把字典/数组 str() 后冒充业务名称，不把 "2" 转成 top_n=2，不把 false 当作 0。
- schema 通过后可调用原 intent_from_mapping 构造已验证值；不得用它代替前面的严格验证。
- metrics/unresolved 仅用于模型边界，转换后不污染旧 Intent 的配置接口，不给旧加载器增加宽松入口。

首版固定资源上限：question 4096 字符、目录 JSON 32768 字符、响应 32768 字符。
响应非字符串或超限在 JSON 解析前即报 SCHEMA_INVALID，避免先解析再发现超限。
超限明确失败，不截断问题、目录或响应；输入应是非空、可编码为 UTF-8 的字符串。
调用参数为 temperature=0、max_tokens=2048、timeout_s=30；客户端不得自行重试，抽取层也不追加修复调用。
目录/输入失败为 0 次调用；解析/转换失败为 1 次调用；传输失败或 ReplayMiss 不增加第二次调用。

缺指标/缺时间是可表示的缺失，不在本轮输出“可答”结论。
格式合法的未知指标、未知维度/机构及受限形状不代表授权或可编译；后续检查仍必须运行。

## 7. 稳定错误与职责

ExtractionError 提供稳定 code、message 和字段/片段定位；不使用 CompileError 冒充编译阶段错误。

| 场景 | code / 异常 |
|---|---|
| 问题类型、空白、编码或长度非法 | EXTRACTION_INPUT_INVALID |
| 目录字段、候选歧义或目录大小非法 | EXTRACTION_CONTEXT_INVALID |
| 空响应、JSON 语法、重复键、非有限常量或解析深度异常 | EXTRACTION_JSON_INVALID |
| 非对象根、未知键、类型/枚举、缺必填字段、响应超限 | EXTRACTION_SCHEMA_INVALID |
| ISO 日期/日历/时点形状或区间冲突 | EXTRACTION_TIME_INVALID |
| 多个不同指标短语 | EXTRACTION_MULTI_METRIC_UNSUPPORTED |
| 非空未解决片段 | EXTRACTION_UNRESOLVED |
| 模型传输/协议失败、回放未命中 | 沿用 LLMError 子类 / ReplayMiss |

顺序优先级：先完整 JSON/schema/日期，再检查多指标，最后 unresolved；不靠丢坏字段得到部分成功。
没有“未知指标自动换名”“解析失败默认全行/当月”“回放失败转真实模型”等成功兜底。
不按 confidence 或“模型说可以”跳过缺失、权限、编译或独立校验。

## 8. 离线样本、回放与脚本

新增 `tests/fixtures/intent_extraction/cases.yaml` 独立清单及至少 10 个稳定用例（EX-001 起），
手写回放放在 `tests/cassettes/intent_extraction/`，不改 AQ/G 的原有问题或标准 SQL。
每条包含 question、手写响应的 cassette 路径、独立 expected Intent 或 expected_error、why_hard、provenance。
业务成功样本覆盖单指标月末、机构过滤、维度分组、delta、每月 Top N、明确报表与整体占比；
缺时间、未知指标、多指标至少各一例；其余边界由参数化单元测试补齐。

- provenance 明确为 handwritten_fixture；model 标记 synthetic/handwritten-fixture，token 数为 0，不伪造真实模型名或调用统计。
- 响应 JSON 与 expected 由人分别编写，不从被测抽取器或编译器的输出自动生成/刷新。
- cassette 键可以按真实消息与现有 message_key 机械计算；测试不能运行时重新造键来掩盖 prompt 漂移。
- ReplayLLM 必须 inner=None、strict=True；提示词/目录变更导致未命中须显式审查样本，不回落网络。
- 不把“十个问题”强行解释成现有 AQ-001~AQ-010 都应成功编译；AQ-007 多指标和 AQ-010 未知指标不因此获得新能力。

新增离线 demo_extract CLI，按样本读取问题并真正通过注入的严格回放客户端走抽取入口，展示 Intent 或稳定错误。
它不是重新手写一张 question → Intent 分支表，不执行 SQL；输出明确说明是假响应契约演示。
默认只用回放，不因环境配置为 real 就偷偷调用真实模型；未命中返回非零，解析失败不展示伪成功。

接通既有 record_cassette 脚本到同一抽取入口，仅用于将来显式 real 配置下的手动录制；本轮不运行录制。
录制时原始响应应能留存以供审查，解析失败不伪装成成功录制；复用注入客户端并确保 flush。
真实录制样本与手写样本在清单/目录中区分，不自动替换已审阅样本，不把录制接线等同于真实模型验收。

## 9. 验收矩阵

### 严格边界

- 非空问题、空白/非字符串/超限、上下文顺序稳定及敏感物理字段不进入消息。
- 正常 JSON、围栏/解释正文/多对象、根数组/null、重复键、NaN、未知嵌套字段、类型强转攻击。
- 缺 metrics/unresolved、未知业务名称、缺指标/时间、多指标不能只取第一个、unresolved 不能被丢弃。
- 非法日期、闰年/月末、无年份/相对/含糊时间样本、冲突 points/区间、缺边界、不读取当前时间。
- 过滤的 null/空 IN/数值/嵌套值、根 org、权限/SQL 字段注入；异常不能删掉业务过滤继续查询。
- 已登记/未知机构原文保留，全行不生成机构过滤，显式非默认且无法表达的口径不回退默认。
- top_n 的 bool/字符串/浮点，global/month 区别，comparison/share 的区别，明确报表与 plain 缺省。

### 调用纪律与结果

- FakeLLM 验证每个路径的 0/1 次调用，不重试、不解析修复、不构造隐藏客户端。
- 严格 ReplayLLM 跑至少 10 条独立清单，覆盖成功及明确错误；网络调用禁止，ReplayMiss 必须可见。
- 有效且可编译的抽取结果接既有 L3/L4，并与手写 Intent/黄金 SQL 或独立预期对照。
- 未知指标到既有 UNKNOWN_METRIC、未知机构到 UNKNOWN_CODE；多指标不生成编译输入。
- 原 991 项测试、26 条黄金用例和 8 条业务规则全部回归，旧 G-001~G-026 不改 SQL。
- 全量 pytest、黄金集、用例纪律、Ruff、变更格式和 diff 检查通过；旧 demo_ask/trace_compile 保持可运行。
- 本轮不宣称真实模型准确率、token 成本或延迟达标；人工 fixture 的通过率不能作为这些指标。

## 10. 文档与交付边界

实现后同步 README、CONTEXT、ARCHITECTURE、ROADMAP、cassette 说明及 ADR-0013。
特别修正旧文档“Filter.value 一律必须预先是存储编码”和“模型 org 原文可直接编译”的过期说明，
与 M2.6 的登记翻译、Intent.org 拒绝契约保持一致；不借机重写无关检索或权限架构。
按实测记录新增数量，只标 M3.1 完成；M3.2~M3.4、M4 和任意自然语言自动出数仍未完成。
既有录制脚本的 Step 1 占位说明改为实际抽取职责，不声称已有完整 answer_question 流水线。
实现完成先汇报，不自动提交实现代码；后续 Git 提交按用户指令使用中文信息。

## 11. 设计工作检查清单

- [x] 检查 Intent/配置加载器、默认机构与过滤契约、模型注入、回放/录制、现有脚本与里程碑。
- [x] 用户确认单轮、离线优先、保留未知/缺失、不接多轮与执行的范围。
- [x] 比较直接加载、严格边界和供应商协议升级；用户确认严格抽取层方案。
- [x] 无界面/视觉决策，不需要可视化工具。
- [x] 自查类型边界、语义不确定性、时点/机构契约、回放真实性、错误优先级和范围一致性。
- [x] 用户复核本书面规格。
- [x] 编写实施计划，再编码、回归并同步进度。

本文为已获用户书面复核批准的设计；设计本身不代替实现验收，实际离线验收结果见下，不代表真实模型已验收。

实施结果：见同日实施计划、ROADMAP M3.1 落地记录和 ADR-0013。1219 tests、26/26 黄金通过；prompt 精简目标未达如实记录。
