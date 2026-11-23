# 项目上下文（新会话请先读这份）

> **用途**：这是一份「换会话也不会丢背景」的交接文档。
> 新会话/新同事进来，读这一份就能知道：我们在做什么、做到哪了、怎么跑、下一步做什么。
>
> 最后更新：2026-11-23，M3.2 单轮澄清接线离线验收完成时。

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
| **askdb** | `D:\projecct\askdb` | 固定 24 节点 DAG + 参考资产（报表/相似问/SQL模板）+ 5,500 字 prompt 让模型写 SQL。188 py。**本机跑不起来**（缺 MySQL/千帆 KB/LLM 端点） | 语义层的数据形态、三层权限纵深、业务规则清单、**8 条踩坑清单** |
| **auto-ontology** | `D:\projecct\auto-ontology` | ReAct + 自动 ontology 编译 + pgvector RAG。470 py。NVIDIA 开源 | ontology 自动编译的思路、可答性门禁、确定性工具校验、**「prompt 里的规则块会掉准确率」的实测结论** |

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
| 13 问题清晰度校验 | LLM + 代码 | 🟡 部分：`clarify/preparation.py` 单轮接线 + 显式指标/时间槽位检查 | ✅ M3.2（不含 M4） |
| **19 SQL 生成与校验** | **LLM 自由写 + 9 层事后校验** | ✅ **★ 核心替代**：编译器 + `validation/` | ✅ **M1 完成** |
| 21 / 22 指标卡 / 结果列命名 | LLM | ❌ **不做**（下游展示） | — |
| 50a 执行查询 | 代码 | ⬜ 只读执行 + 行数上限 | ⬜ M3.4 |
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
| **M3** | `question → Intent`（单次注入调用）+ 澄清接线 + 只读执行 | 🟡 2/4：M3.1/M3.2 完成；未验真实模型 |
| **M4** | 可答性门禁 + 黄金集扩到 30 条 + 评测报告 + 失败归因 | ⬜ |

**现在能做什么**：给定一个结构化 `Intent`，产出**通过适用业务规则校验（共 8 条）**的 SQL，**0 次模型调用**；
并且有一条命令能回答"引擎现在算得对不对"（黄金集门禁，26 条用例 = 26/26，覆盖 25 种形状标签；1314 项测试通过）。
能出的形状：月末/日/区间 单时点、维度分组、业务过滤、行级权限（含 fail-closed）、**双时点 delta 对比**、**每月 Top N**、**受限双表 INNER/LEFT JOIN**、**固定粒度单月报表与多月 UNION ALL**、**单表月末单维度的整体占比**。
双表首批只做事实表指标 + 唯一维表维度/过滤，不与 delta/Top N 混用。

**现在还能做什么**：单轮问题经注入模型调用、严格结构验证后得到 Intent；`demo_extract` 默认严格离线回放。
20 条手写 synthetic cassette 已验证契约，有效结果在集成测试中接既有 L3/L4；不是任意自然语言出数能力或真实模型质量证明。
**现在还能做什么（M3.2）**：一次抽取后，按指标/时间必需槽位给一个确定性追问，
或返回 slots_complete；多指标和 unresolved 选择性转澄清，技术错误不吞掉。
slots_complete 不等于可答/可编译/有权限；准备层不导入编译/校验/执行层，不写回默认值。
**现在还不能做什么**：跨请求记住上一轮与合并补答、查询执行、M4 可答性决策。
`demo_ask` 仍是固定问题 → 固定 Intent 临时桩；没有偷偷换为模型入口。

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

详细：`docs/ARCHITECTURE.md`；决策与翻案条件：`docs/DECISIONS.md`；路线图：`docs/ROADMAP.md`。

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

---

## 七、这个项目要回答的四个问题

| 问题 | 什么时候能答 |
|---|---|
| 把 SQL 从模型手里拿走，准确率能提高多少？ | M4（黄金集对照） |
| 哪些业务规则可以代码化，哪些只能留 prompt？ | M2（8 条规则的边界就是答案） |
| 问数到底需要几次模型调用？ | M3（编译路径 0 次，理解路径 1 次） |
| "答不了"和"没权限"能不能分开告诉用户？ | M4（`Scope` + 可解释拒答） |

回答不出来的部分，如实写"未验证"，不当结论。

---

## 八、怎么跑起来

```bash
# 1) 环境（见第九节：需要先建 venv）
uv sync --group dev            # 或 pip install -e . + dev 依赖

# 2) 测试（含 SQLite 内存执行，不需要外部数据库/模型/网络）
pytest

# 3) lint
ruff check app scripts tests
```

**可运行的验证点**（三层，缺一层都不算守住）：

| 层 | 文件 | 验证什么 |
|---|---|---|
| 单测 | `tests/engine/test_compiler_minimal.py`（14）、`test_business_filters.py`（6） | 规则对不对 |
| 归一化 | `tests/engine/test_golden_compare.py`（21） | 比对器"该忽略什么、不该忽略什么" |
| 黄金集门禁 | `tests/engine/test_golden_gate.py`（随用例参数化扩展） | 算得对不对 + **门禁自己会不会红** |

另外 `scripts/trace_compile.py` 是学习工具：把编译器五步的中间产物逐个打出来
（`uv run python -m scripts.trace_compile --trap` 还能现场演示 `from/from_` 那个坑）。

---

## 九、环境与依赖（已就绪）

| 项 | 状态 |
|---|---|
| Python | ✅ **3.12.16**，由 `uv` 管理的独立副本（`C:\Users\EDY\AppData\Roaming\uv\python\...`），不动系统 Python |
| 项目 venv | ✅ `.venv/` 已建立（`uv sync --group dev`），`uv.lock` 已生成 |
| 数据库 | **不需要外部服务**。测试已含 SQLite 内存结果验证；L5 执行链仍待 M3.4 |
| LLM | **不需要真实模型**。编译路径 0 次；M3.1 抽取路径 1 次注入调用，CI 用 fake/strict replay |

### 怎么跑

```bash
uv sync --group dev      # 建/更新 venv
uv run pytest            # 1314 passed（含 SQLite 内存测试，不需要外部数据库/模型/网络）
uv run ruff check .      # All checks passed
uv run ruff format --check .
uv run python -m scripts.demo_ask       # 端到端演示（见下）
uv run python -m scripts.check_golden   # 黄金集门禁（26/26 通过）
```

### 为什么用 uv 而不是手工 venv

本机原本只有 Python 3.14 和 3.11，而 `pyproject.toml` 要求 `>=3.12,<3.14`。
`uv python install 3.12` 会下载一份独立的 3.12，**不动系统 Python、不需要管理员权限**，
且 `uv sync` 自动生成 `uv.lock` 保证可复现。CI 也用 uv。

### ruff 的一条刻意妥协

`pyproject.toml` 里关掉了 `RUF001/RUF002/RUF003`（"歧义 unicode 字符"）。
原因是中文全角标点会被判为歧义，一开就是 **860+ 条误报**，会把真问题淹掉。
这是**中文代码库的正当配置**，不是偷懒。

### Docker：装了，但暂时用不上（已实测）

| 项 | 状态 |
|---|---|
| Docker Desktop / daemon | ✅ 29.4.0 在运行，Compose v5.1.1 |
| **拉取新镜像** | ❌ **不通**（`docker.io` 连接超时，IPv6 路由失败，代理未生效） |
| 本地已缓存镜像 | `mysql:8.0`、`python:3.11-slim`、`nginx:alpine`、`node:18-alpine`（**没有 postgres、没有 python:3.12**） |

**结论：不用 Docker 做开发环境。**
Python 版本已由 `uv` 解决（`uv python install 3.12`，不动系统 Python）；
M1~M3 不需要外部数据库服务；结果验证使用 SQLite 内存库，L5 执行链仍在 M3.4。

`docker-compose.yml` 已按 ADR-0006 改好：**默认不启动任何服务**，
`--profile pg` / `--profile oracle` 才按需启动。文件本身正确，只是当前拉不到镜像。

真要启用时，先解决网络（Docker Desktop → Settings → Docker Engine 里加
`registry-mirrors`，或修复系统代理），再 `docker compose --profile pg up -d`。

**★ 如果将来在容器里跑引擎，必须显式设 `TZ=Asia/Shanghai`。**
容器默认 UTC，"截至今天""本月"这类相对时间在北京时间 00:00~08:00 之间会整整差一天。

---

## 十、已踩过的坑（写进测试/配置防复发）

| 坑 | 后果 | 防复发 |
|---|---|---|
| `sqlglot` 的 `Select` arg key 是 **`from_`**（带下划线），写成 `from` 不报错 | 静默丢掉整个 FROM 子句，产出一条残废 SQL | `compiler/__init__.py` 结构自检 + `test_from_clause_present` |
| 该 bug 连 6 条规则校验都抓不到（`SELECT 1` 也是合法 SQL） | 校验全绿但 SQL 是错的 | 黄金集比对（`scripts/golden.py`，已实现）+ 结构断言 |
| `exp.to_table("表名 别名")` 不解析别名 | 同上 | 用 `exp.alias_(exp.to_table(表), 别名)` |
| 位置参数构造 `RowPolicy(...)` 顺序容易搞错 | 取到的值变成字符串的字符 | 测试中一律用关键字传参 |
| `app/llm/real.py` 用了 `Sequence` 却没 import（F821） | 类型注解求值时 NameError | 2026 首轮 `ruff check` 发现并修复；现已全绿 |
| 仓库此前**从未跑过 ruff**（README 的"CI 全绿"未经核实） | 873 条 lint 错误（其中 862 条中文误报、11 条真问题） | 现在 `ruff check` + `ruff format --check` 都是绿的，应纳入提交前检查 |
| `conditions.py` 先拿**整个列表**去查码值表，`operator="in"` 分支成了死代码；且那条分支用 `code(...) or str(value)` 静默透传 | 配了码值表时多机构过滤永远报 `UNKNOWN_CODE`；没配时查不到却不报错 | `_stored_value()` 统一 fail-closed + `tests/engine/test_business_filters.py`（M1.5 期间才发现，`filters` 此前**零测试**） |
| `intent_from_mapping` 之类配置加载器若静默忽略未知键 | YAML 里 `dimension` 手滑写成 `dimensinos`，门禁照样"通过"，但测的东西变了 | 加载器一律 `_reject_unknown_keys`；`test_intent_loading.py` 12 个手滑反例 |
| 黄金 SQL 若允许"一键用引擎产出刷新" | 门禁退化成"引擎没变"的自证 | `check_golden` **不提供** `--update/--bless` |
| `GoldenCase` 起初**没有 `scope` 字段**，`evaluate_case` 也不传 scope | 权限形状（行级策略 / fail-closed）无法进黄金集 —— 三个注入点里唯一"错了就是事故"的那个，只有单测没有端到端对照 | `app/engine/scope.py::scope_from_mapping` + `GoldenCase.scope` + `G-006/G-007`；`test_scope_is_threaded_into_compilation` 做**双向**证明（有 scope 必一致、无 scope 必不一致） |
| 权限类配置（`row_policies`）若把"没配策略"和"策略没取值"当成一回事 | 前者是不限制，后者是"什么都看不到"；混成前者 = 越权 | 加载器保留空 `values`；`test_row_policy_without_values_is_kept_not_dropped` |
| SQL **不允许同一层 SELECT 引用本层别名** | `cur_balance - base_balance` 在任何数据库都不合法；下意识写法 | 差值重复写两遍完整表达式（ADR-0007）；黄金集 `G-008.sql` |
| 时点条件若套在聚合**外面**（`CASE WHEN etl_dt=? THEN SUM(x) ELSE 0 END`） | 语法合法、能跑，但 SUM 先在整张表聚合，结果只有一行，不报错不告警 | `test_point_condition_is_inside_aggregate`：每个 `SUM` 的参数必须是 `CASE`，且没有任何 `CASE` 里装着 `SUM` |
| 两个时点却没给 `comparison` 时"按单时点处理" | 产出的 SQL 是**把两个时点的余额加在一起** —— 跑得通、数字还挺像 | `MULTI_POINT_WITHOUT_COMPARISON` 显式报错 + 单测 |

**一条反直觉的结论（值得记住）**：**L4 对编译器产出几乎永远不会红。**
不是因为规则没用，而是编译器在设计上就写不出违规形状（如 `_dimension_items` 让名称列进不了 GROUP BY）。
所以 L4 在编译路径上是**兜底防编译器 bug**，不是日常防线；真正会红的是黄金集。
要故意让 L4 红，可让**语义层的公式或报表字段绑定**引用独立名字目录中不存在的列。
L4 不负责从同名 npl_rate 自动判别业务口径，正确来源还由显式知识和独立黄金/结果测试守住。

---

## 十一、下一步（不要跳步）

1. ~~**M2.1**：双时点对比（`R-COMP-001`）~~ ✅ **已完成**：`compiler/comparison.py`
   + `eval/golden/G-008/G-009.sql` + `tests/engine/test_compiler_comparison.py`（17 条）
   + `DECISIONS.md` ADR-0007。
2. ~~**M2.2**：每月 Top N（`R-TOPN-001`）~~ ✅ **已完成**：`compiler/topn.py`
   + `eval/golden/G-010.sql` + `tests/engine/test_compiler_topn.py` + ADR-0008。
   月末聚合 → `ROW_NUMBER` 按月排名 → 外层取前 N；并列按主体编号升序，权限在聚合前注入。
3. ~~**M2.3**：受限双表 JOIN~~ ✅ **已完成**：`compiler/sources.py` + `compiler/permissions.py`
   + `G-011~G-014.sql` + `test_compiler_join.py`（92 项）+ ADR-0009。
   `LevelDef.table` 明确维度来源，`RelationDef.cardinality` 声明基数；关系缺失/歧义/不安全即拒绝。
   Scope 的表、列、行策略均生效，空 Scope 拒绝；LEFT 右表权限留在 WHERE，避免无权金额进入 NULL 分组。
   AQ-007 保留单表核销语义，不硬绑定 JOIN。
4. ~~**M2.4**：显式报表与 UNION ALL~~ ✅ **已完成**：`semantic/report.py` + `compiler/reports.py` + `compiler/report_time.py`
   + `G-015~G-018.sql` + 元数据/月份/编译正反例（152 项）+ ADR-0010。
   `ReportDef` 不覆盖明细公式，固定粒度直接投影；report/union 均按月份数选 SELECT/UNION ALL。
   缺任一分区或表列权限整条拒绝，空行策略只令自身分支恒假；各分支独立别名、独立 L4 列目录。
   样例登记产品不良率 202604~202606；真实口径与唯一粒度由配置维护者和源表约束负责，不自动发现报表。
5. ~~**M2.5**：同口径整体占比~~ ✅ **已完成**：`compiler/share.py` + `validation/ratio.py` + `ShareSpec` 严格加载
   + `G-019~G-022.sql` + 178 项专项测试（意图 17、编译/SQLite 104、独立校验 57）+ ADR-0011。
   单明细表、单个月末、单维度和可加 SUM；三层为“分组金额 → 整体空窗口 → 展示与除法”。
   非拆分上下文保留，拆分业务过滤外移，全部行策略仍在聚合前；整数除法安全，零整体为 NULL。
   不与报表/JOIN/Top N/时点对比混用，不给已有比例再次求占比；非空 org 须规范到 filters，不能忽略。
6. ~~**M2.6**：机构值精确解析与独立规则~~ ✅ **已完成**：`semantic/exact_values.py` +
   `validation/org.py` / `org_sources.py` + G-023~G-026 + ADR-0012。
   严格机构维度仅登记全称/简称/编码，只有两端 trim；未登记、歧义、缺配置不透传。
   别名和编码共享候选空间，不能用“键优先”掩盖冲突；coded 旧行为保留。
   公用条件接口只允许严格 eq/in；Scope 值不重译，普通/报表的非空 Intent.org 明确拒绝，share 保留原错误。
   L4 按独立 schema.table/列策略与查询作用域追踪保护，拦截模糊、变形、否定和来源不明的机构匹配；
   不误伤 JOIN、名称展示、Top N 排名和数值统计。991 项测试、26/26 黄金用例通过，M2 为 6/6。
7. ~~**M3.1**：严格单轮 `question → Intent`~~ ✅ 完成：`understanding/`、20 条独立手写固定回放、`demo_extract`；
   录制脚本接到同一入口，finally 保存坏响应，录制与手写样本分目录；本轮未真实录制。
   新增 228 项测试，全量 1219 项、26/26 黄金集、8 条业务规则保持；旧黄金 SQL/AQ 文件不变。
   静态 system 指令 1093 字符、业务目录 JSON 373 字符，20 个样本消息内容合计 1541～1558 字符（EX-001 为 1550），
   未达到 prompt ≤300 字精简目标，不是 token 数；不声称实际模型质量/成本/延迟。
8. ~~**M3.2**：单轮澄清接线~~ ✅ 完成：`clarify/preparation.py`、独立手写准备层预期与 `demo_prepare`；
   缺指标/时间只问一个，多指标要求指定一个，unresolved 只问首片段、不猜分类、不返回部分 Intent。
   原始成功抽取的 Intent/Completion 保持；JSON/schema/时间/输入/目录/模型异常原样传播。
   新增 95 项测试（核心 37、回放/集成 29、CLI 29），全量 1314、黄金 26/26；8 条业务规则保持。
   旧 M3.1 prompt/协议/20 份 cassette、G-001～G-026 SQL 和 AQ 原文件未改；无真实模型、网络或外部数据库调用。
   `demo_prepare --case EX-008` 询问时间、EX-018 询问指标、EX-010 请求单指标、EX-011 引用未解决片段；这些是正常结果，退出 0。
   旧 `demo_extract` 对 EX-010/EX-011 仍报错退出 1，入口契约不改。
9. **下一步 M3.3**：设计跨请求会话、补答合并、轮次预算与拒绝边界；当前每次调用独立，不会记住上一轮。
   随后 M3.4 SQLite 只读执行。M4、任意自然语言自动出数仍未完成。
10. **M2 期间顺手补的欠账**（不阻塞，但别忘）：
   - `MetricDef.is_percentage` 已用于拒绝对现有比率再次计算占比；但 `unit="%"` 仍未统一自动传给所有路径的 `R-PCT-001` 别名输入
   - `tests` job 里的 `scripts.seed_fixture` **已从 CI 移除**（它只支持 PostgreSQL，而 CI 给的是 `sqlite:///`）。
     脚本本身仍然只支持 PostgreSQL —— 等 M4 真的要验方言时再按 profile 处理（ADR-0006 未决项）
   - `comparison` 的另外三个值（`yoy` / `mom` / `rate`）在 `Intent` 里是合法字段，
     但编译器会明确报 `COMPARISON_UNSUPPORTED`。其中 `yoy` / `mom` 本质是"自动推算两个时点"，
     做起来比 `delta` 便宜；`rate` 要先定义清楚"百分点 vs 相对变化"（见 ADR-0007 的翻案条件）

**每加一种形状必须同时交**：黄金 SQL + 单测 + `covers` 标注（对应哪条业务规则编号）。
