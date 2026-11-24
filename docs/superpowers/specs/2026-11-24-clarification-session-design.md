# M3.3：跨请求澄清状态与受限补答合并设计

日期：2026-11-24

状态：2026-11-24 用户已复核并批准书面规格；M3.3已完成离线验收，未验真实模型。
基线：`0ab52b9`（含 CI 配置修复），M3.2 已提交；1314 项测试、26/26 黄金用例、8 条业务规则，M3 完成 2/4。

## 1. 目标与已确认边界

让第二次独立调用能读取第一轮已确认的条件，只补当前询问的槽位；
重建服务对象或重开 SQLite 连接后，仍能继续同一会话。
不引入 HTTP API、用户体系、Redis、外部数据库服务、真实模型调用或查询执行。

已确认的范围：

- 存储接口 + 本地 SQLite，纯内存实现用于快速测试；无外部服务依赖。
- 保存原问题、已接受 Intent、当前追问、已发追问次数和状态版本。
- 当前只支持补 metric/time_range，已确认的机构、过滤、分组、来源、排名、对比、占比不能被补答覆盖。
- 首轮最多一次模型调用，每次补答最多一次；不重新抽取完整原问题来填单个槽位。
- 默认最多发出两次澄清；预算耗尽且信息仍缺时明确停止，不猜值、不继续编译。
- 多指标/unresolved 没有安全的完整 Intent，要求重述完整问题，不能拼接猜测结果。
- 会话不保存 Scope、密钥、数据库连接或模型客户端；后续编译必须传入当次权限。
- 本轮离线验收，不联网、不使用子代理，不实现 M3.4 执行、M4 可答性或真实模型评测。

## 2. 方案比较

| 方案 | 收益 | 限制 | 结论 |
|---|---|---|---|
| 仅进程内字典 | 简单、单测快 | 重启丢失，不能验证持久化 | 只作为测试适配器 |
| 存储协议 + SQLite | 可验证跨请求/重启恢复、原子版本更新；无需服务 | 需要状态序列化和迁移版本边界 | 用户已确认 |
| Redis/外部数据库 | 可扩展到多实例 | 引入部署和运维，当前无需求 | 本轮不做 |

## 3. 分层与接口

新增聚焦的会话包，建议 `app/engine/clarify/session/`：

- models.py / errors.py：冻结状态、对外结果、稳定错误。
- codec.py：严格 JSON 状态序列化/恢复，不使用 pickle 或任意对象反序列化。
- store.py：SessionStore 协议和纯内存适配器。
- sqlite_store.py：本地 SQLite 存取，独立于业务查询执行。
- reply.py：专用补答消息、严格响应解析，复用既有日历校验规则。
- service.py：首轮、续轮、预算、受限合并和版本控制。

公开服务由调用方显式构造并注入 store/llm/max_rounds，提供：

```python
await service.start(question, catalog, *, owner_key, catalog_revision)
await service.reply(session_id, answer, catalog, *, owner_key, catalog_revision, expected_version)
```

owner_key 由可信调用方提供，非空字符串；会话 ID 是服务端生成的 UUID，不是权限凭据。
存储按 owner_key + session_id 定位，owner 不匹配与不存在统一报 SESSION_NOT_FOUND。
这里只隔离会话记录，不实现登录、RBAC 或租户服务；客户端不能自行声明 owner_key 获得授权。
CLI 使用固定本地演示 owner，文档不把它当生产身份方案。

catalog_revision 是可信调用方维护的非空版本字符串；相关语义配置改变时必须更新。
另保存最小业务目录 JSON 的稳定摘要，在补答前同时比对版本与摘要；变化时要求重新发起问题，零模型调用。
摘要只能检查发给模型的目录变化，不能自动发现调用方漏更新版本的所有物理语义变化。
每次还按 M3.1 规则校验当前目录；目录不是会话存储或模型输出授权的。

## 4. 状态与返回结果

持久记录包括：格式版本、owner_key、session_id、递增 version、catalog_revision/业务目录摘要、
原问题、当前 accepted_question、可安全保留的已接受 Intent、当前 Clarification/待补槽位、rounds_used、max_rounds 和 status。
只存继续会话所需的数据，不存完整聊天历史、原始 Completion 文本、连接、Scope 或客户端。
Intent.raw 保留为受限 JSON 诊断数据，不决定槽位、状态或权限；新补答诊断与旧 raw 分开记录，不冒充原模型响应。

状态：

| status | 含义 | 是否接受 reply |
|---|---|---|
| awaiting_slot | 已有严格抽取 Intent，待补一个必需槽位 | 是，只补 pending_slot |
| awaiting_question | 原抽取为多指标/unresolved，要求完整重述 | 是，answer 作为全新完整问题 |
| slots_complete | 必需槽位不缺，仍不证明可答或授权 | 否 |
| budget_exhausted | 信息仍不足且不能再发追问 | 否 |
| restart_required | 本会话不能安全继续，如补答要求改变既有条件或目录已变化 | 否 |

返回 SessionResult：session_id、version、status、rounds_used/max_rounds、当前 clarification 或停止说明、
可编译候选 intent（仅 slots_complete 时提供），以及本次真实取得的 Completion 或 None。
中间 Intent 保存在内部状态中，不把它标为可编译结果；终止状态不附带 SQL、rows 或授权结论。
Completion 只代表本次调用，不伪造历史 token 统计；恢复会话不恢复一份虚构的 Completion。

首轮调用一次 prepare_question：成功则存 slots_complete；缺槽位存 awaiting_slot；
多指标/unresolved 存 awaiting_question 且 accepted_intent=None。存储成功才向调用方返回成功结果。

## 5. 预算的精确定义

max_rounds 由构造服务时显式传入，默认 2，严格非负整数（bool/字符串/浮点拒绝），会话创建后固定。
rounds_used 表示已向调用方发出的追问次数，不是模型调用次数或收到的补答数量。

- 首轮需要询问且 max_rounds>0：保存第一次追问，rounds_used=1。
- 用户回答最后一次允许的追问后，仍可解析并合并；若补齐，正常 slots_complete。
- 如果仍缺信息，只有 rounds_used<max_rounds 才发下一个追问并加一，否则 budget_exhausted。
- max_rounds=0：完整问题仍可正常完成；不完整时直接 budget_exhausted，不发追问。
- 技术错误（输入、模型传输、非法响应、存储失败）不算发出新追问，不推进状态版本或次数。
- 已终止会话再次 reply 零模型调用，报 SESSION_CLOSED，不重新开启预算。

不复用旧 ClarifyState 的 resolved/retried 或“按最可能口径处理”回退。
本轮以总追问次数限制循环；不另加关键词分类、无效回答置信度或自动修复调用。

## 6. 补答协议与受限合并

awaiting_slot 使用独立的 slot-reply-v1 协议，与 M3.1 消息/响应协议分离。
消息只含原问题、当前追问、当前待补槽位、最小业务目录、补答文本及必要的业务字段概况，
不包含 raw、物理表列、公式、code_map、Scope、owner、会话数据库内容或连接。
已接受 Intent 只能转成白名单业务信息供判断是否改变条件，不能直接序列化整个内部对象进入消息。

模型仅提取当前补答，禁止补全其他槽位，若有新增/更改条件、多指标或无法单槽表达的请求，明确要求重述。
固定调用 temperature=0、max_tokens=2048、timeout_s=30；最多一次，不修复、不重试。
问题/补答非空、UTF-8、最多4096字符，目录/响应各最多32768字符；补答检查不读取当前日期。

响应根四字段全部必填且仅允许：

```json
{
  "slot": "time_range",
  "status": "filled",
  "value": {"grain": "month_end", "points": ["2026-04-30"]},
  "unresolved": []
}
```

- slot 必须严格等于保存的 pending_slot（metric/time_range）；不能让模型切换询问对象。
- status 仅 filled/unclear/restart_required；不是模型给整个会话的可答性判断。
- filled：unresolved 必须为空。metric 的 value 是非空字符串数组，逐项非空、trim 后去完全相同重复；
  恰一项可合并，多个不同项转 restart_required，绝不取第一项；未知指标原文保留给下游。
  time_range 的 value 为 M3.1 同等严格的 time 对象，复用枚举/真实日期/月末/区间互斥验证。
- unclear：value 必须为 null，unresolved 必须是非空补答原文片段数组，全部能在 answer 中找到；
  不能把“格式不合法”当成 unclear。保持原 Intent，按预算重问当前槽位。
- restart_required：value 必须为 null，unresolved 允许为空或补答原文片段数组；不合并任何内容，终止并要求重提完整问题。
- 所有层未知字段、重复键、非有限常量、非对象根、围栏、多对象、超长或深度异常拒绝；不转换类型。
- 相对/无年份/含糊时间应 unclear；如“2026年4月末，改查江北”应 restart_required，不能只拿日期忽略改机构。

合并由代码决定：只有 awaiting_slot + 严格合法的 filled 单值能更新，且原目标槽位必须确实缺失。
使用 dataclasses.replace 等等价受限替换，只设置 metric 或 time_range；其他字段逐项不变。
raw/诊断信息不作授权，不把模型提供的 session/status/Scope 写回状态。
合并后执行同等 M3.2 必需槽位判断，不再调用 prepare_question 或完整抽取，不重算其他业务字段。
可抽出 M3.2 已有确定性检查供共享，但原 prepare_question 的行为、公开结果、提示词和 cassette 不变。

结构限制能阻止响应直接覆盖机构等字段，不能证明模型没有漏报用户新增条件或选错日期。
手写“改机构”样本只验证信号处理，不宣称解决语义忠实性；真实模型评测仍需独立执行。

## 7. 完整重述路径

awaiting_question 没有可合并 Intent；将 answer 作为新的完整问题调用一次 prepare_question，
不把原问题和补答拼成新的猜测问题，不沿用被拒绝的部分结果。
重述完整则 slots_complete；若只缺槽位转 awaiting_slot；仍为多指标/unresolved 则按总预算决定再次追问或停止。
原问题保留作诊断，当前完整问题记录为新的 accepted_question；后续待补上下文使用后者。

若 awaiting_slot 中模型报告需要改变条件，本轮返回 restart_required，让调用方用 start 发起新问题；
不在同次请求额外完整抽取，不默默开启新预算，也不自动撤销/覆盖旧条件。

## 8. 存储、序列化与并发

SessionStore 提供 create/get/compare_and_swap；纯内存与 SQLite 行为一致。
SQLite 路径由调用方显式传入，默认演示在忽略目录 build/ 下；不使用业务查询数据库或全局连接。
只维护会话表，固定 SQL 和绑定参数；不让模型控制表名/列名/路径，不引入 ORM/新依赖。

格式版本明确为 v1。JSON 只存支持的基本类型；日期为严格 ISO，tuple 显式恢复。
恢复时校验所有结构、状态/计数/待补槽位关系及 Intent 的抽取边界，不信任宽松配置加载器直接强转。
awaiting_slot 必须有已接受 Intent、有效待补槽位和对应缺失；awaiting_question 不得有已接受 Intent；
slots_complete 必须有已接受 Intent 且两项必需槽位齐全。计数不得超过固定预算，预算/版本必须严格整数。
原问题与 accepted_question/补答诊断仍受各自字符上限约束，不能借持久化绕过入口限制。
用显式字段编码已接受 Intent，不从 raw 重建权威字段；没有部分 Intent 的状态不得反序列化出一个默认 Intent。
未知版本、损坏 JSON、非法类型/日期、超出单记录262144字符限制均报 SESSION_STATE_INVALID，不能重置为空会话继续。
最大记录长度在写入前和读取解析前检查；拒绝超限，不截断。

单次 start/create 原子写入 version=1。reply 必须提供严格正整数 expected_version（bool/字符串/浮点拒绝）；
读取后先验证 owner、版本、状态、当前目录，再允许模型调用，旧版本请求零模型调用。
读状态后不持有数据库事务等待模型。完成响应处理后以旧版本执行 CAS，成功则 version+1；
并发修改导致 CAS 失败抛 SESSION_VERSION_CONFLICT，不能自动重新调用模型或覆盖新状态。

并发相同版本的请求可能均已调用一次模型，但只能一个提交状态；
本轮不承诺模型调用 exactly-once，不实现分布式请求幂等表/锁服务。
顺序重复提交旧版本必须零调用拒绝，调用方发生冲突后读取当前状态再决定下一步。
模型/解析失败保持持久状态原样；写入失败不得返回虚假完成。

本地 SQLite 只是应用状态存储，不能因此标记 L5 业务只读执行完成。
本轮不增加自动过期、后台清理或删除命令；数据库权限和生命周期由本地调用方管理，不承诺无限保留适合生产。

## 9. 错误与状态边界

新增 SessionError，提供 code/message/定位，不复用 CompileError：

- SESSION_INPUT_INVALID：owner/ID/版本/预算/补答等接口参数非法。
- SESSION_NOT_FOUND：会话不存在或 owner 不匹配，不向其他 owner 泄露记录。
- SESSION_VERSION_CONFLICT：过期版本或 CAS 冲突。
- SESSION_CLOSED：终止状态不接受补答。
- SESSION_STATE_INVALID：存储格式版本或数据损坏/超限。
- SESSION_STORAGE_ERROR：SQLite 操作失败，保留异常 cause，不吞掉失败。
- SESSION_REPLY_JSON_INVALID / SESSION_REPLY_SCHEMA_INVALID / SESSION_REPLY_TIME_INVALID：补答技术边界失败。

旧 M3.1 ExtractionError、LLMError/ReplayMiss 仍保留原异常，不改成空 Intent 或正常澄清。
目录版本/摘要变化属于 restart_required（当前会话版本按 CAS 推进，零模型调用），
不是“当前用户无权限”或“指标不存在”的结论；未通过当前目录格式校验则原 CONTEXT_INVALID，不推进状态。

## 10. 离线演示与验收

新增 demo_session：start/reply/show 子命令，共享显式 --db 路径，不启动 HTTP 服务。
start/reply 使用明确指定的离线样本会话/补答响应与 strict ReplayLLM，不读取 real 环境构造真实客户端；
show 只读取存储，零模型调用。reply 使用 --session-id 与 --expected-version，拒绝隐式取最新版后盲写。
演示允许两次独立进程运行 start 与 reply，真正证明 SQLite 重开恢复；不执行 SQL。
协议状态结果正常退出0，技术/存储/版本错误非零；输出展示 status/version/追问/最终 Intent，不伪造 token 统计。

新增 tests/fixtures/clarification_session/ 的问题、补答、独立预期，以及新协议专属 cassette。
响应和预期分别手写，来源 handwritten_fixture、synthetic/handwritten-fixture、tokens=0；
消息键仅初建时机械计算，测试不刷新。旧 EX-001～020 响应及消息键、黄金 SQL/AQ 原文件不改。

至少覆盖：

- 缺时间补日期、缺指标补业务名、双缺失依次补齐；机构/过滤/分组/来源/比较/排名/占比均保持。
- 第二轮只走补答协议与确定性合并，不完整重抽、不调用 L3/L4；独立集成测试才串下游。
- 多指标/unresolved 要求完整重述，无部分结果可合并；重述后可转单槽补答，预算不重置。
- 默认两次追问、零预算、最后一次补答成功、无效补答耗尽后停止；技术失败不推进。
- 正确转 unclear/restart_required、多指标不取首项；非法 JSON/类型/日期/错槽位/额外字段均拒绝。
- 原问题/补答中指令只当数据，模型看不到物理结构、码表、权限或身份字段；不宣称语义注入完全解决。
- 进程/服务/连接重建后继续；不同owner不可读写；未知/终止会话、过期版本、目录变化零模型调用。
- 两个连接同版本CAS仅一方成功；无自动模型重试；状态损坏/格式版本/超限拒绝；SQLite失败可见。
- slots_complete 仍不越过未知名称、不支持能力和空Scope的旧编译拒绝，具体机构与新传入Scope交集保持。
- 全量1314项旧测试、26/26黄金、10条评测纪律、8条规则回归；Ruff/变更格式/diff和新旧演示通过。

## 11. 文档与交付

书面规格批准后编写实施计划；writing-plans 如仍缺失则按既有约定手工写等价计划，不联网安装。
同步 README/CONTEXT/ARCHITECTURE/ROADMAP 与 ADR-0015，记录实测测试数与SQLite演示命令。
只标 M3.3 完成、M3为3/4；M3.4执行、M4和真实模型验收仍未完成。
M3.1 prompt ≤300字目标未达仍如实披露；新补答协议长度单独实测，不伪称整个系统提示词达标。
实现完成先汇报，不自动提交实现代码；Git提交信息使用中文。

## 12. 设计工作检查清单

- [x] 检查M3.2接口、严格抽取/日历、旧预算、配置、代码布局及里程碑。
- [x] 无UI/视觉选择，不需要可视化工具。
- [x] 比较内存、存储协议+SQLite、外部服务，用户确认第二种。
- [x] 用户确认只补当前槽位、两轮预算、不覆盖条件、无安全部分结果须完整重述。
- [x] 自查预算计数、状态转换、CAS/并发承诺、目录/owner边界、序列化权威来源与范围一致性。
- [x] 用户复核本书面规格。
- [x] 编写实施计划，再编码、回归并同步进度。

本文为已获书面复核批准的设计；设计本身不代替实现验收，不代表真实模型已验收。

实施结果：见同日实施计划、ROADMAP M3.3落地记录及ADR-0015。新增161项，全量1475 tests、26/26黄金通过；M3为3/4，不含业务查询执行。
