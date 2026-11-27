# 项目上下文（新会话请先读这份）

> **用途**：换会话也不丢背景的交接文档。这里只保留**当前状态、下一步和已知坑**；
> 里程碑历史与落地细节在 `docs/ROADMAP.md`，架构在 `docs/ARCHITECTURE.md`，决策在 `docs/DECISIONS.md`。
>
> 最后更新：2026-11-27（M5.1 真实模型基线已运行；run_id 20261127T064047Z-e239b55）。

---

## 一、这个项目是什么（以及不是什么）

**wenshu = 一个银行问数引擎的「腰部」实现 + 一次可验证的架构对比。**

一条主链路：`一句话问题 → Intent → 编译器展开 SQL → 校验 → 出数`

### ✅ 是

- 一个**学习与工程实践**项目
- 用来回答 4 个有数据支撑的问题（见第七节）
- 核心是把「写 SQL」从模型手里拿走，换成确定性编译器

### ❌ 不是

- 不是产品，没有前端、没有用户体系、没有管理后台
- **不是 askdb 的重写，也不是它的分支**。零行代码复制，只借鉴设计（见 `ATTRIBUTION.md`）
- 不追求覆盖 askdb 的全部功能 —— 见第三节的节点对照表

---

## 二、参考的两个项目（只读，不复制）

| 项目 | 路径 | 它是什么 | 我们借什么 |
|---|---|---|---|
| **askdb** | 工作笔记本 `D:\projecct\askdb`；家里电脑路径不同 | 固定 24 节点 DAG + 参考资产（报表/相似问/SQL模板）+ 5,500 字 prompt 让模型写 SQL。188 py。**本机跑不起来**（缺 MySQL/千帆 KB/LLM 端点） | 语义层的数据形态、三层权限纵深、业务规则清单、**8 条踩坑清单** |
| **auto-ontology** | 工作笔记本 `D:\projecct\auto-ontology`；家里电脑 `E:\MyPythonProject\auto-ontology` | ReAct + 自动 ontology 编译 + pgvector RAG。470 py。NVIDIA 开源 | ontology 自动编译的思路、可答性门禁、确定性工具校验、**「prompt 里的规则块会掉准确率」的实测结论** |

**阅读地图**：`docs/REFERENCES.md`（按"值得读 / 只读结论 / 不必读"分好了类）

---

## 三、★ 我们在做 askdb 的哪一步（关键：不是全流程）

**askdb 有 24 个节点；我们不做全流程，只替代其中一段，并且不复用任何代码。**

| askdb 节点 | 它干什么 | wenshu 里的对应 | 状态 |
|---|---|---|---|
| 01 入口判断 / 场景选择 | LLM 分诊 | ❌ **不做**（调用方直接进入抽取或传 Intent 编译） | — |
| 03 追问问题重写 | LLM | ❌ **不做** | — |
| 05 场景路由 | 代码 | ❌ **不做**（wenshu 没有"场景"概念） | — |
| 09 问题结构化拆解 | LLM | 🟡 换成 **`question → Intent`** | ✅ M3.1 离线契约 |
| 09' 早期校验（只查时间） | 代码 | ✅ 变成编译器的 `intent` 类错误 | ✅ |
| 09-2 机构类型与口径判断 | LLM | ✅ 变成 `DimensionDef.levels` **字典查询** | ✅ |
| 10 参考知识检索 | LLM + 千帆 KB | ❌ **不做**（不靠相似问/SQL模板） | — |
| 12 选表 | LLM | 🟡 部分：指标来源表反查 + `Scope` 限定 | ✅ 部分 |
| 13 问题清晰度校验 | LLM + 代码 | 🟡 部分：`clarify/preparation.py` 单轮接线 + 显式指标/时间槽位检查 | ✅ M3.2～M3.4（追问、跨请求会话、执行） |
| **19 SQL 生成与校验** | **LLM 自由写 + 9 层事后校验** | ✅ **★ 核心替代**：编译器 + `validation/` | ✅ **M1 完成** |
| 21 / 22 指标卡 / 结果列命名 | LLM | ❌ **不做**（下游展示） | — |
| 50a 执行查询 | 代码 | SQLite 只读执行、行数上限及查询预算 | ✅ M3.4 |
| 50b-2 结果总结 | LLM | ❌ **不做** | — |

**一句话**：askdb 的核心痛点集中在「节点 19：让模型自由写 SQL，再用 prompt 约束」。
**wenshu 只深挖这一段**，把它换成"确定性编译器 + 独立校验器"，其余上下游只做最小接线。

**所以：场景识别、追问重写、场景路由、参考检索、澄清编排、结果总结——这些我们都不做，也不是"复用"，而是"不存在"。**
如果将来需要，是"新做"，不是"搬过来"。

---

## 四、当前进度

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **M0** | 骨架 + LLM 边界（fake/replay/real）+ 校验层 6 条规则 + 澄清槽位 + fixture + 评测集 | ✅ 完成 |
| **M1** | 语义层（指标/维度/码值/关系）+ **编译器最小闭环** + `Scope` 权限注入 | ✅ 完成 |
| **M1.5** | 黄金集门禁：`scripts/golden.py`（归一化/比对）+ `scripts/check_golden.py`（CLI）+ `eval/golden/cases.yaml` + pytest 接线 | ✅ 完成 |
| **M2** | 形状扩展：**2.1 双时点 `delta` ✅** / **2.2 每月 Top N ✅** / **2.3 双表 JOIN ✅** / **2.4 报表 UNION ALL ✅** / **2.5 整体占比 ✅** / **2.6 机构精确匹配 ✅** | ✅ 完成（6/6） |
| **M3** | `question → Intent`（单次注入调用）+ 澄清接线 + 只读执行 | ✅ 4/4：离线闭环完成；未验真实模型 |
| **M4** | 可答性门禁 + 黄金集扩到 30 条 + 评测报告 + 失败归因 | ✅ 4/4：离线评测完成；未验真实模型 |
| **M5.1** | 真实模型基线评测（DeepSeek `deepseek-flash`，关闭思考） | ✅ 基线已运行（run `20261127T064047Z-e239b55`）；结果见第十一节 |

**现在能做什么**

- 给定结构化 `Intent`，产出通过 8 条业务规则校验的 SQL，**0 次模型调用**；黄金集 30/30（28 种形状标签），全量 1582 项测试通过。
- 支持的形状：月末/日/区间单时点、维度分组、业务过滤、行级权限（含 fail-closed）、双时点 `delta`、每月 Top N、受限双表 INNER/LEFT JOIN、固定粒度单月报表与多月 UNION ALL、单表月末单维度的整体占比。双表只做事实表指标 + 唯一维表维度/过滤，不与 delta/Top N 混用。
- 单轮 `question → Intent`：一次注入模型调用 + 严格结构验证；20 条手写 cassette 严格离线回放。`demo_extract` 默认严格离线，不是任意自然语言出数能力或真实模型质量证明。
- 澄清：缺指标/时间时确定性追问一个；多指标/unresolved 完整重述；`slots_complete` 不等于可答/可编译/有权限，准备层不导入编译/校验/执行层，不写回默认值。
- 跨请求会话：显式注入的 `ClarificationService` + SQLite 文件恢复；只补缺失槽位，默认最多两次追问，CAS 只允许一个并发提交；目录版本/摘要变化要求重启，owner 不匹配不可读写；技术失败不写回。
- 执行：`engine/query.py::QueryService` 在槽位补齐后，用调用方当次传入的 `Scope` 编译并独立校验，只有 L4 无问题才交给 SQLite 只读执行（默认最多 100 行、查询预算 2 秒）。
- 拒绝：未登记指标 → `UNANSWERABLE`；指标/表/列无权限 → `OUT_OF_SCOPE`；两类拒绝都不进入 L4/L5，不携带 SQL、物理表列名或内部错误。
- 评测：`scripts/evaluate` 一次跑完抽取 20 条、澄清 20 条、黄金 30 条和 AQ-001 端到端 1 条，共 71 条固定回放，失败按阶段归因；报告明确 `replay`、无真实模型、无外部数据库。
- 真实模型基线：`scripts/evaluate_real` 已用真实密钥运行一次（run `20261127T064047Z-e239b55`），指标与归因见第十一节。

**现在还不能做什么**

- HTTP 接口、用户认证、真实模型准确率证明。
- 真实模型基线只覆盖 30 条规范问法、单模型单版本；不能当作真实用户准确率或生产可用。
- 语义层仍只有 `bank_mini.py` 一份演示数据，没有从配置或数据库加载的实现。
- `retrieval/` 与 `coverage/` 两个目录只有说明文档，没有代码（已实现的可答性分类在 `engine/query.py`）。
- `demo_ask` 仍是固定问题 → 固定 Intent 临时桩；没有偷偷换为模型入口。

真实模型基线评测（M5.1）已运行一次（run `20261127T064047Z-e239b55`），结果见第十一节。

---

## 五、架构一页纸

```
L0 输入   语义层（知识：指标/维度/码值/表关系） + Scope（权限：表/列/行策略）
L1 理解   question ──LLM──> Intent                  ← 唯一允许不确定的地方（M3）
L2 门禁   槽位能否在语义层落地？→ 编译 / 澄清 / 拒答
L3 编译   Intent + 语义层 + Scope → AST → SQL        ← 0 次模型（M1 已实现）
L4 校验   SQL 文本 → Problem[]                      ← 0 次模型，且不看 L3 中间产物
L5 执行   SQL → rows                               ← M3.4
```

**三条不变式**：
1. 只有 L1 不确定；L3/L4 是纯函数。
2. SQL 只在 L3 一处生成 → 权限只在一处注入、校验只需一处复核。
3. L4 只接收 SQL **文本** → 否则编译器的 bug 会被自己的校验放过。

详细：`docs/ARCHITECTURE.md`；决策与翻案条件：`docs/DECISIONS.md`；路线图与历史：`docs/ROADMAP.md`。
开发流程与完成标准：`AGENTS.md`、`docs/DEVELOPMENT.md`；评测口径：`docs/EVALUATION.md`；完整术语：`docs/GLOSSARY.md`。

---

## 六、术语速查（每个词一行）

| 词 | 一句话 |
|---|---|
| **语义层** | 「业务词 ↔ 物理列」的登记表。是**给代码查的数据**，不是给模型读的散文 |
| **Intent** | 模型唯一输出物：指标 / 时间 / 维度 / 过滤 / 对比。**不含任何 SQL 语法** |
| **编译器** | 查语义层 + 拼 AST 的确定性代码，0 次模型调用 |
| **AST** | SQL 解析成的语法树。在树上判断"有没有写操作""用了哪些表"，比字符串匹配可靠 |
| **AST 强校验** | 在语法树上做检查（不是正则）。能保证"形状对"，保证不了"口径对" |
| **Scope** | 本次提问的可见范围（表 / 列 / 行级策略）。**是编译器的第三个输入参数，不是权限系统** |
| **覆盖率门禁** | 编译之前判断"语义层能支撑多少"，决定 编译 / 澄清 / 拒答 |
| **黄金集** | 手写的标准 SQL。引擎产出必须与它结构等价。**不是引擎生成的**，否则只证明"没变" |
| **fail-closed** | 出错时默认拒绝。例如行策略没有取值 → 生成恒假条件，而不是跳过 |

完整术语表在 `docs/GLOSSARY.md`，新术语登记在那里；这里只留最常用的几个。

---

## 七、这个项目要回答的四个问题

| 问题 | 现在能答到哪一步 |
|---|---|
| 把 SQL 从模型手里拿走，准确率能提高多少？ | M4 离线部分 71/71 + 黄金 30/30；M5.1 基线（30 条规范问法、单模型单版本）抽取全对 30%、执行全对 20%；**和真实模型自由写 SQL 的对照仍未做** |
| 哪些业务规则可以代码化，哪些只能留 prompt？ | M2（8 条规则的边界就是答案） |
| 问数到底需要几次模型调用？ | M3（编译路径 0 次，理解路径 1 次） |
| "答不了"和"没权限"能不能分开告诉用户？ | M4.1（`UNANSWERABLE` 与 `OUT_OF_SCOPE`，离线已验证） |

回答不出来的部分，如实写"未验证"，不当结论。

---

## 八、怎么跑起来

```bash
uv sync --locked --group dev   # 建/更新 venv
uv run pre-commit install      # 每台电脑装一次；首次会联网下载钩子环境
uv run pytest                  # 不需要外部数据库/模型/网络
uv run ruff check .
uv run ruff format --check .
uv run python -m scripts.demo_ask
uv run python -m scripts.check_golden
uv run python -m scripts.evaluate       # eval/report.* 是机器产物，不提交
uv run python -m scripts.evaluate_real --case EX-001 --repeats 1   # 人工，需要真实密钥；eval/real/ 不提交
```

三层验证（缺一层都不算守住）：单测（规则对不对）→ 归一化比对（该忽略什么、不该忽略什么）→ 黄金集门禁（算得对不对 + 门禁自己会不会红）。

`scripts/trace_compile.py` 是学习工具：把编译器五步的中间产物逐个打出来（`uv run python -m scripts.trace_compile --trap` 还能现场演示 `from/from_` 那个坑）。

---

## 九、环境说明

| 项 | 状态 |
|---|---|
| Python | ✅ **3.12.16**，由 `uv` 管理的独立副本（`uv python find 3.12` 可查），不动系统 Python |
| 项目 venv | ✅ `.venv/` 已建立（`uv sync --locked --group dev`），`uv.lock` 已提交 |
| 数据库 | **不需要外部服务**。L5 使用独立的本地 SQLite 业务库；会话存单独的 SQLite |
| LLM | **不需要真实模型**。编译路径 0 次；抽取路径 1 次注入调用，CI 用 fake/strict replay |

**为什么用 uv**：本机系统 Python 是 3.14/3.11，而 `pyproject.toml` 要求 `>=3.12,<3.14`。
`uv python install 3.12` 会下载一份独立的 3.12，不动系统 Python、不需要管理员权限；`uv sync` 自动生成 `uv.lock` 保证可复现，CI 也用 uv。

**ruff 的一条刻意妥协**：`pyproject.toml` 关掉 `RUF001/RUF002/RUF003`（"歧义 unicode 字符"）。
中文全角标点会被判为歧义，一开就是 860+ 条误报，会把真问题淹掉。这是中文代码库的正当配置，不是偷懒。

**Docker**：已装但拉取新镜像不通（`docker.io` 超时，代理未生效），不用它做开发环境。
`docker-compose.yml` 默认不启动任何服务，`--profile pg` / `--profile oracle` 按需启动。
将来若在容器里跑引擎，必须显式设 `TZ=Asia/Shanghai`，否则北京时间 00:00~08:00 的"截至今天""本月"会差一天。

---

## 十、已踩过的坑（写进测试/配置防复发）

| 坑 | 后果 | 防复发 |
|---|---|---|
| `sqlglot` 的 `Select` arg key 是 **`from_`**（带下划线）；`exp.to_table("表名 别名")` 不解析别名 | 静默丢掉 FROM 或别名，产出残废 SQL | 结构自检 + `test_from_clause_present`；用 `exp.alias_` |
| `SELECT 1` 也是合法 SQL，L4 抓不到"缺 FROM" | 校验全绿但 SQL 是错的 | 黄金集结构比对 + 结构断言兜底 |
| 位置参数构造 `RowPolicy(...)` | 取到的值变成字符串的字符 | 测试中一律用关键字传参 |
| 仓库曾**从未跑过 ruff**（README 的"CI 全绿"未经核实） | 873 条 lint 错误，其中 11 条真问题 | `ruff check` + `format --check` 纳入提交前检查 |
| `conditions.py` 拿**整个列表**查码值表，`operator="in"` 成死代码；`code(...) or str(value)` 静默透传 | 配了码值表时多机构过滤永远 `UNKNOWN_CODE`；没配时查不到却不报错 | `_stored_value()` 统一 fail-closed + `test_business_filters.py` |
| 配置加载器静默忽略未知键 | `dimensinos` 手滑仍"通过"，但测的东西变了 | `_reject_unknown_keys` + `test_intent_loading.py` 12 个反例 |
| 黄金 SQL 允许"一键用引擎产出刷新" | 门禁退化成"引擎没变"的自证 | `check_golden` **不提供** `--update/--bless` |
| `GoldenCase` 起初**没有 `scope` 字段** | 权限形状（行级策略/fail-closed）只有单测，没有端到端对照 | `scope_from_mapping` + `GoldenCase.scope` + G-006/G-007 + 双向证明 |
| 权限配置把"没配策略"和"策略没取值"当一回事 | 前者是不限制，后者是什么都看不到；混成前者 = 越权 | 加载器保留空 `values` + `test_row_policy_without_values_is_kept_not_dropped` |
| SQL **不允许同一层 SELECT 引用本层别名** | `cur_balance - base_balance` 在任何数据库都不合法 | 差值重复写两遍完整表达式（ADR-0007） |
| 时点条件套在聚合**外面** | SUM 先在整表聚合，结果只有一行，不报错不告警 | `test_point_condition_is_inside_aggregate` |
| 两个时点却没给 `comparison` 时"按单时点处理" | 产出的 SQL 把两个时点的余额加在一起 —— 跑得通、数字还挺像 | `MULTI_POINT_WITHOUT_COMPARISON` 显式报错 + 单测 |

**一条反直觉的结论**：**L4 对编译器产出几乎永远不会红。**
不是因为规则没用，而是编译器在设计上写不出违规形状（如 `_dimension_items` 让名称列进不了 GROUP BY）。
所以 L4 在编译路径上是**兜底防编译器 bug**，不是日常防线；真正会红的是黄金集。
要故意让 L4 红，可让语义层的公式或报表字段绑定引用独立名字目录中不存在的列。
L4 不负责从同名 `npl_rate` 自动判别业务口径，正确来源还由显式知识和独立黄金/结果测试守住。

---

## 十一、M5.1 真实模型基线结果与下一步

**已运行（2026-11-27）**

- run_id：`20261127T064047Z-e239b55`；代码 `e239b55`；运行电脑：当前工作机（`D:\projecct\wenshu`）。
- 口径：DeepSeek `deepseek-flash`、关闭思考、20 条抽取 + 10 条 AQ、每题 3 次；90 次调用、67,764 token；无 transport/未完成。
- 指标：抽取全对 30%（6/20）、多数 35%（7/20）、尝试 35%（21/60），不稳定 EX-007/EX-010；执行全对 20%（2/10）、多数 30%（3/10）、尝试 26.67%（8/30），不稳定 AQ-005。
- 失败归因：61 次失败全部在抽取/澄清；42 次 `unresolved` 片段带解释导致 `EXTRACTION_SCHEMA_INVALID`，19 次时间/指标缺失或转澄清；没有 compiler/validation/execution/transport 失败。
- 主要问题：`2026年4月末` 常被模型当成含糊时点；unresolved 未逐字引用原问题；未知维度/机构未按契约保留；AQ-006 的报表预期与 prompt 的“不自动切报表”冲突；AQ-007 核销指标不在演示语义层；AQ-008 的月份范围未在题面给出。
- 评测修正：T6 暴露两个判定 bug，已由 PR #2（`e239b55`）修正——忽略嵌套 `raw`、执行套件 `ExtractionError` 归为 `understanding`；原始输出不提交，只留在本机 `eval/real/20261127T064047Z-e239b55/`。
- 结论边界：30 条规范问法、单模型单版本、temperature 0 不保证逐字复现；不代表真实用户问法或生产可用。
- 早先无效 key/中止轮次不纳入基线；正式基线仅指上表 run。

**下一步均未设计、未排期**：prompt 迭代（unresolved 逐字与月末归一化）、可配置语义层加载、PostgreSQL 闭环、本体与 ReAct。

**M2 期间顺手补的欠账（不阻塞，但别忘）**

- `MetricDef.is_percentage` 已用于拒绝对现有比率再次计算占比；但 `unit="%"` 仍未统一自动传给所有路径的 `R-PCT-001` 别名输入。
- `tests` job 里的 `scripts.seed_fixture` 已从 CI 移除（它只支持 PostgreSQL，而 CI 给的是 `sqlite:///`）。脚本本身仍只支持 PostgreSQL，等真的要验方言时再按 profile 处理（ADR-0006 未决项）。
- `comparison` 的 `yoy` / `mom` / `rate` 在 `Intent` 里是合法字段，但编译器明确报 `COMPARISON_UNSUPPORTED`；`yoy` / `mom` 本质是自动推算两个时点，`rate` 要先定义清楚"百分点 vs 相对变化"（见 ADR-0007 的翻案条件）。

**每加一种形状必须同时交**：黄金 SQL + 单测 + `covers` 标注（对应哪条业务规则编号）。
