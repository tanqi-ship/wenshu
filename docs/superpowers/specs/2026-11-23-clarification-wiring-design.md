# M3.2：单轮意图与确定性澄清接线设计

日期：2026-11-23

状态：用户已确认推荐方案及行为边界；本文待书面复核，尚未实现。
基线：`cf7adca`，M3.1 已提交；1219 项测试、26/26 黄金用例、8 条业务规则，M3 完成 1/4。

## 1. 已确认的目标与范围

把单轮问题经过 M3.1 得到的结果，与已有确定性槽位检查接起来。
需要补充信息时一次只返回一个追问；必需槽位齐全时保留原抽取结果，供调用方后续使用。
这不是完整 answer_question 流水线，也不是查询执行或可答性门禁。

用户已确认：

- 缺指标询问指标，缺时间要求明确年份的日期/时点/范围；二者都缺先问指标。
- unresolved 只围绕第一个原问题片段请求明确，不自动猜测它属于时间、口径还是其他类别。
- 多指标请求用户指定一个，不取第一项、不合并指标、不发明候选清单。
- JSON/schema/日期/输入/目录错误和模型调用失败保留原异常，不伪装成用户缺信息。
- 不增加第二次模型调用，不写回默认日期、机构、过滤或口径，不改变 Scope。
- 不接跨请求状态、补答合并或数据库执行；不提前实现 M4。
- 本轮继续 FakeLLM/严格 ReplayLLM 离线验收，不联网、不调用真实模型或外部数据库，不使用子代理。

## 2. 方案比较

| 方案 | 收益 | 风险或成本 | 结论 |
|---|---|---|---|
| 直接把 Intent 扁平化后调用旧默认 check_slots | 改动少 | 旧清单的全行/比较默认说明、相对时间示例与当前边界不一致；也不能处理抽取错误 | 不采用 |
| 无状态单轮衔接层 + 专用必需槽位规格 + 确定性追问模板 | 保留已有检查机制与严格抽取，失败分类明确，可离线测试 | 新增轻量结果结构与接线模块 | 用户已确认 |
| 提前建设完整多轮会话、补答合并与执行流水线 | 一次覆盖完整体验 | 混入 M3.3/M3.4，状态/预算/执行责任未独立设计 | 本轮不做 |

## 3. 模块与公共接口

新增 `app/engine/clarify/preparation.py`，集中放轻量结果结构、专用槽位规格与单轮衔接；
通过 `clarify/__init__.py` 导出，不把新职责塞进已包含旧预算状态的 slots.py。

公共入口：

```python
async prepare_question(question, catalog, *, llm) -> PreparationResult
```

入口只调用一次已有 extract_intent，不重建消息、不解析模型原文、不再调用模型生成追问。
缺信息与字段语义是两回事：槽位适配器只传 Intent.metric 和 Intent.time_range，
使用显式 `slots=` 调用已有 check_slots，不用 dataclasses.asdict 把 raw 或其他字段变为判断输入。

结果结构（frozen dataclass，自身新增的集合字段用 tuple）：

- PreparationResult
  - status：仅 `needs_clarification` 或 `slots_complete`。
  - extraction：原始 ExtractionResult，或 None。
  - missing_slots：必需槽位缺失名称元组，仅成功抽取后检查所得；顺序为 metric、time_range。
  - clarification：Clarification 或 None。
- Clarification
  - reason：仅 `missing_slot`、`unresolved` 或 `multi_metric`。
  - question：确定性生成的一个追问，不调用模型润色。
  - slot：metric/time_range 或 None；unresolved 不猜槽位，multi_metric 为 metric。
  - fragments：原抽取错误的完整片段元组；只对第一片段提问，其余保留诊断。
  - source_error_code/source_error_path：捕获的抽取错误代码及定位；普通缺槽位时为 None。

结果不附带 SQL、rows、Scope、授权结论或模型置信度判断。
字段不变量：

| 分支 | status | extraction | missing_slots | clarification |
|---|---|---|---|---|
| 成功抽取且槽位齐全 | slots_complete | 原结果 | () | None |
| 成功抽取但缺槽位 | needs_clarification | 原结果，允许缺失 | 有序非空元组 | missing_slot |
| 已验证的 unresolved | needs_clarification | None | ()，表示未运行检查而非槽位齐全 | unresolved |
| 多指标 | needs_clarification | None | ()，表示未运行检查而非槽位齐全 | multi_metric |

slots_complete **仅表示已配置的必需槽位不缺失**，不是 ready/可答/可编译/有权限/有数据。
调用方只有 status=slots_complete 且 extraction 非 None 时，才可尝试后续既有检查与编译；
仍必须处理 CompileError 并运行适用的独立 L4。needs_clarification 分支不继续编译。

## 4. 必需槽位与追问文案

新路径使用两项专用 SlotSpec，都是 required=True、on_missing=ask，顺序固定：

1. metric：您想查询哪个指标？
   - 示例只取当前目录指标业务名称，按稳定目录键排序后最多 3 个，不使用旧的硬编码核销金额等示例。
   - 空指标目录不附示例，不从其他目录或系统环境补指标。
   - 示例只是已有目录用语，不意味着该用户一定有权限，不能替代 Scope 检查。
2. time_range：请补充有明确年份的统计日期、月末时点或起止范围。
   - 只使用静态绝对时间示例，如 2026年4月末、2026年4月1日至2026年4月30日。
   - 不出现过去12个月等首版无法确定的相对时间示例，不读主机日期，不承诺补答一定可表达或可编译。

复用 SlotCheck 的 missing/next_question；一次只输出 missing[0] 对应的问题，
missing_slots 可以保留全部缺失项用于诊断，但不能将它们拼成多个追问。
目录字段/别名冲突已由同次 M3.1 调用在模型访问前验证，不额外走模糊查找或指标自动选择。

可选项缺省不参与必需槽位判断；不追问 org/dimensions/comparison，也不因此改写原 Intent。
不生成“按全行计算”“未做同比/环比”等可能与过滤/对比意图冲突的默认说明。
已存在的机构过滤、share、comparison、source_mode、top_n、raw 及 Completion 全部保持原对象/原值。

## 5. 抽取错误的选择性处理

只捕获两种已有 ExtractionError.code，其他异常使用原对象原样抛出：

| 情况 | 单轮衔接行为 |
|---|---|
| EXTRACTION_UNRESOLVED，且存在已验证原问题片段 | 返回 needs_clarification，reason=unresolved |
| EXTRACTION_MULTI_METRIC_UNSUPPORTED | 返回 needs_clarification，reason=multi_metric |
| INPUT/CONTEXT/JSON/SCHEMA/TIME_INVALID | 原错误透传，不向用户追问补槽位 |
| LLMTransportError / LLMProtocolError / ReplayMiss | 原错误透传，无修复、重试或网络兜底 |
| 未来新增且未显式登记的 ExtractionError | 原错误透传，不能默认转成澄清 |

unresolved 模板只围绕 fragments[0]：请明确「该片段」的具体含义，并补充完整问题后重新提交。
原片段只作为引用数据展示，不执行其中指令；保留全部 fragments，不能删除未问片段或把它们标记已解决。
如果错误契约异常（如 UNRESOLVED 没有片段），保留原错误而不制造泛化成功。

多指标模板：当前仅支持单指标，请指定本次要查询的一个指标。
不从错误文本/原响应重新挖取指标、不列猜测候选、不把多指标改成单指标 Intent。

这两种错误发生在 M3.1 返回 ExtractionResult 之前，所以 extraction=None，
不伪造部分 Intent、Completion 或 token 统计，不修改 M3.1 异常来泄露被拒绝的部分结果。
M3.1 原有错误优先级保持：JSON/schema/日期先于多指标，多指标先于 unresolved；
例如同时多指标及 unresolved 时，只按原先多指标错误处理，不跳过前面的校验。

## 6. 未知名称、歧义与能力边界

- 单个未知指标仍然是非空 metric；若时间完整，可得到 slots_complete，后续 L3 仍报 UNKNOWN_METRIC。
- 未知维度/机构也不删除或改名；后续沿用 UNKNOWN_DIMENSION/UNKNOWN_CODE 等既有结果。
- yoy/mom/rate、全局 Top N 等即使槽位齐全，也不因本轮接线获得新 SQL 能力。
- 真正的目录别名冲突仍是 CONTEXT_INVALID，不生成第一命中候选，也不开展目录歧义消解。
- ROADMAP 中“指标歧义”本轮仅按已有多指标/未解决信号请求明确，不宣称实现候选检索、排序或多轮选择。
- 结构检查不能证明模型没有漏提过滤或理解错日期，本轮不以槽位齐全掩盖这种语义不确定性。

## 7. 依赖、兼容与调用预算

依赖方向：clarify.preparation → understanding / clarify.slots / semantic / llm.protocol。
不引入 compiler、validation、coverage、factory、数据库客户端或环境访问；下游串接只在独立测试中进行。

旧 check_slots/INTENT_SLOTS/DEFAULT_SLOTS/ClarifyState/build_answer_note 保持行为兼容；
新路径不使用旧全行默认、不调用 build_answer_note、不实例化或修改 ClarifyState，
也不读 max_clarify_rounds。本轮没有会话/轮次；跨请求预算与追问选择留给 M3.3 单独设计。
可以聚焦修正文档字符串，将旧的“complete 等于可生成 SQL”“按最可能口径处理”说明标为旧接口语义，
明确新路径不依赖这种回退，不为通过新测试而修改旧预算行为或无关测试。

模型调用预算全部继承 M3.1：输入/目录错误 0 次，其余到达 complete 的路径最多 1 次；
temperature=0、max_tokens=2048、timeout_s=30，不改 prompt/协议/消息键和已有 20 份 cassette。
无二次分类、追问生成或解析修复调用；传输失败不再发请求。

## 8. 离线演示与测试数据

新增 `scripts/demo_prepare.py`：读取离线清单、注入 ReplayLLM(inner=None, strict=True)，
真正调用 prepare_question，展示 status、一个追问或抽取结果，明确不编译、不执行、不是可答性结论。
即使环境 real 也不构造真实客户端；未知回放/解析/传输错误可见。

CLI 退出码：slots_complete 和 needs_clarification 都是正常协议结果，返回 0；
未被转换的技术错误、回放未命中返回非零。旧 demo_extract 的多指标/unresolved 退出码仍为 1，契约不改。

新增 `tests/fixtures/clarification/cases.yaml`，独立手写 M3.2 的预期状态、reason、slot、追问/片段；
引用原有抽取 case ID 查找问题与固定 cassette，不重造消息键、不刷新旧响应或旧 expected。
至少覆盖原有 20 条回放；双缺失、多片段、格式错误等补充边界用 FakeLLM 手写响应测试即可。
样本仍标 handwritten_fixture；不冒充真实模型录制或准确率/成本/延迟评测。
旧 demo_extract/demo_ask/trace_compile 和 record_cassette 入口不改成准备层，不顺带改录制职责。

## 9. 验收矩阵

- 缺指标/缺时间/双缺失按固定优先级只问一个；目录示例稳定且不使用未登记指标，空目录无伪造示例。
- 完整 Intent 原样保留；机构过滤、分组、comparison、share、report/union、Top N、raw/Completion 不被默认值覆盖。
- unresolved 只问第一片段、保留完整片段、slot=None、extraction=None；不得按词语猜类别。
- 多指标不生成部分 Intent；混合坏字段/多指标/unresolved 延续原错误优先级。
- 所有非目标 ExtractionError、模型异常和 ReplayMiss 保留原对象，无二次调用。
- 输入/目录失败零调用；正常、缺槽位和目标语义错误单次调用；处理本身不生成模型消息。
- 未知指标/机构与受限形状可以槽位齐全，但独立集成测试仍得到旧 L3 拒绝，不当成可答。
- 外部 Scope 在有效样本下仍由下游注入并经 L4 验证；准备层不接收、改写或扩张权限。
- 20 条旧 cassette 全部严格命中；新增清单标准答案手写，禁止运行时刷新消息键。
- 新 CLI 的澄清是正常结果，技术错误非零；real 环境下依旧禁止真实调用。
- 1219 项旧测试全部回归；26/26 黄金和 8 条业务规则保持，G-001～G-026 SQL 与旧 AQ 文件不改。
- 全量 pytest、黄金门禁、评测纪律、Ruff、变更格式、diff 检查及旧演示冒烟通过，按实测记录新增数。

## 10. 文档与交付

实施前编写单独实施计划；writing-plans 如仍未安装，沿用手工等价计划，不联网安装技能。
实施后同步 README/CONTEXT/ARCHITECTURE/ROADMAP 与 ADR-0014，说明 slots_complete 的严格限定含义。
届时只标 M3.2 完成、M3 为 2/4；M3.3 状态、M3.4 执行、M4 与真实模型评测仍未完成。
M3.1 已披露的 prompt 长度与 ≤300 字目标未达不因本轮接线而变化，不宣称精简达标。
实现完成先汇报，不自动提交实现代码；Git 提交信息使用中文。

## 11. 设计工作检查清单

- [x] 检查提交状态、抽取接口/错误、旧槽位默认与追问预算、现有回放及里程碑。
- [x] 本轮为纯业务接线，无 UI/视觉选择，不需要可视化工具。
- [x] 比较直接复用旧默认、无状态衔接与完整会话，用户确认无状态单轮方案。
- [x] 用户确认一次一问、不写回默认、选择性处理错误，以及 unresolved 不猜分类。
- [x] 自查结果状态、默认值/权限边界、错误优先级、元数据缺失与旧接口兼容，无实现占位。
- [ ] 用户复核本书面规格。
- [ ] 编写实施计划，再编码、回归并同步进度。

本文是已获方向认可、待书面复核的设计，不是 M3.2 已实现或真实模型已验收的声明。
