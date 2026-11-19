# 代码来源与设计参考

> **当前状态（已核实）：本仓库零行代码复制自其他项目。**
>
> 全部由本项目作者重新编写。`pyproject.toml` 的依赖清单参考了 askdb 的
> `requirements.txt`（依赖名不是代码），其余为原创。
>
> 可复核的验证方式：本仓库不含任何 `SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA` 版权头，
> 也不含 askdb 的任何源文件。

---

## 一、为什么不复制参考项目的代码

理由见 `docs/DECISIONS.md` ADR-0003，核心三条：

1. **要改的正是架构本身**，复制进来就要不断纠结"这段还要不要"；
2. **参考项目在本机跑不起来**（缺 `.env` / 业务库 / 知识库 / 模型端点），复制了也无法验证；
3. askdb **没有 LICENSE 文件**，默认"保留所有权利"。不复制即不产生授权义务。

因此本项目只做两件事：**读它的设计**（`docs/REFERENCES.md` 是阅读地图），和**在自己的 fixture 上重新写**。

---

## 二、设计参考登记

以下项目**只借鉴了设计思想**，未复制代码，不产生许可义务。记录的目的是防止后来人把已验证过的结论重新推翻。

### NVIDIA Auto-Ontology（Apache-2.0）

| 参考了什么 | 精确出处 | 影响到本项目哪里 |
|---|---|---|
| 「写在 writer prompt 里的规则块，每次实测都让准确率下降」 | `auto_ontology/retrieval/text_to_sql/react/prompts.py:7-10` | `app/engine/validation/rules.py` 存在的理由 |
| 「模型只能用检索到的表」，由 AST 强制而非 prompt 约束 | `retrieval/text_to_sql/react/run_sql.py` | `validation/names.py` 的 `TABLE_NOT_IN_SCOPE`（M1） |
| 「可答性」做成独立、可单独调用的接口 | `retrieval/entity_coverage/` | `engine/coverage/`（M4）。**但不采用其距离阈值判据**，见 ADR-0002 |
| 推断出的语义外键必须"join 返回行"才允许存 | `semantic/semantic_fk.py` | 未来若做 FK 推断，采用同一原则 |
| ontology 数据模型：`term` / `column_attribute` / `sql_attribute`(含 `expression` + `source`) | `dal/schema.py` | `MetricDef` / `ColumnDef` 的字段设计；`source` 字段的启发 |
| CI 结构：分圈 lint + **skip guard**（测试静默跳过 = 失败） | `.github/workflows/ci-python.yml` | `.github/workflows/ci.yml` |
| fixture 哲学：「刻意做小，重点是有真实外键的第二个 schema」 | `dev_tools/fixtures/sql/pagila_analytics.sql` | `fixtures/sql/bank_fixture.sql` 的 D-01~D-11 设计 |

#### ⚠️ 引用边界（重要）

上面第一条的**原始语境**是 auto-ontology 的 ReAct 写 SQL 提示词，原句完整为：

> "Rules about how SQL should look belong in code — the checks `run_sql` and `submit` apply —
> not here: rule blocks in the writer prompt lost accuracy every time they were measured,
> and **a policy being trained should learn conventions from reward, not from a list**."

后半句表明该结论与"策略训练"语境有关。因此：

- **本项目把它当作待验证假设，不当作已证结论。**
- 验证方式是 `eval/cases/` 黄金集对照（M4.3）。
- 若将来评测显示某条规则放 prompt 效果更好，应如实记录并修订，而不是维护这条引用。

**如果后续复制 auto-ontology 的任何代码**，必须在文件头保留 SPDX 声明（Apache-2.0 要求保留版权与许可声明）。

### askdb（内部项目，无 LICENSE）

| 参考了什么 | 影响到本项目哪里 |
|---|---|
| 语义层原料的数据形态：`db_column_meta` 的 `label` / `synonyms` / `unit` / `code_mapping` / `column_role` / `semantic_dimension_code` | `semantic/models.py` 的 `ColumnDef` |
| 指标 → 物理表依赖的思路 | `MetricDef.source_tables` |
| 三层权限纵深：表过滤 → 行级 WHERE 注入 → 列白名单 | `Scope` 的三个注入点（ADR-0004） |
| 业务规则清单（机构口径 / 时点对比 / 百分比 / 名称字段 / 报表禁 JOIN） | `validation/rules.py` 的 6 条规则 |
| AST 只读校验的实现结构 | `validation/safety.py`（自行重写） |
| 回归 fixture 的字段命名与业务语义（`fiv_lev_cla_flag`、`current_balnc_cny` 等） | `fixtures/sql/bank_fixture.sql` |
| **8 条踩坑清单** | `docs/REFERENCES.md` 第三节（这是最有价值的部分） |

**注意**：业务规则本身是业务知识，不是代码，不产生代码归属问题。

---

## 三、本项目自身

版权归项目作者。**许可证待定**——在明确是否需要与 askdb 交换代码之前，不建议声明为任何开源许可。

依赖清单（`pyproject.toml` 的 16 个包）参考了 askdb 的 `requirements.txt`，这些是公开包，各自遵循其自身许可。
