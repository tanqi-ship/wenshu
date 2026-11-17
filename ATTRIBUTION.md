# 代码来源说明

> **当前状态（第一步，已核实）：本仓库 51 个文件，零行代码复制自其他项目。**
>
> 全部由本项目作者重新编写。`pyproject.toml` 里的 16 个依赖包名转录自
> askdb 的 `requirements.txt`（这是依赖清单，不是代码），其余为原创。
>
> 一条可复核的验证方式：本仓库不含任何 `SPDX-FileCopyrightText: ... NVIDIA`
> 版权头，也不含 askdb 的任何源文件。

---

## 一、为什么现在没有可抄的代码

设计上的选择：**先建干净骨架，再按需搬入**，而不是 fork 一个 9 万行的仓库。

理由见 `MIGRATION.md` 第一节。核心是：需要改的正是架构本身，
先 fork 会让人不断纠结"这段还要不要"，而删代码比搬代码难得多。

## 二、Step 0 搬入时，必须回来更新这一节

如果后续真的复制了 askdb 的代码（`MIGRATION.md` 里"✅ 抄"那一批），
**那时**才产生实际的归属义务。届时请在本节登记：

| 源文件 | 目标路径 | 复制日期 | 授权依据 |
|---|---|---|---|
| （示例）`services/connector_manager.py` | `app/legacy/services/` | 2026-11-xx | 作者本人授权，非商业用途 |

同时注意：askdb **没有 LICENSE 文件**，默认"保留所有权利"。
本项目对它的使用依赖作者本人的明确授权，而不是开源许可。
一旦本项目要商用、开源、或移交其他主体，**必须先解决这个授权问题**。

## 三、设计参考（未复制代码，无法律义务，但有工程价值）

以下项目只借鉴了设计思想与实测结论。记录在这里的目的不是归属声明，
而是**防止后来人把已经验证过的结论重新推翻**。

### NVIDIA Auto Ontology（Apache-2.0）

| 参考了什么 | 位置 | 影响到了本项目哪里 |
|---|---|---|
| **核心信条**："写在 prompt 里的 SQL 规则块，每次实测都让准确率下降" | `retrieval/text_to_sql/react/prompts.py` 开头的 docstring | 整个 `app/engine/validation/rules.py` 的存在理由 |
| `tables_not_shown`：用 sqlglot AST 强制"模型只能使用检索到的表"，而不是靠 prompt 约束 | `retrieval/text_to_sql/react/run_sql.py` | `app/engine/validation/names.py` 的 `TABLE_NOT_IN_SCOPE` |
| 覆盖率 = 命中实体数 / 问题实体数，把"可答性"做成一等公民 | `retrieval/entity_coverage/` | 尚未实现（Step 2 计划） |
| CI 结构：分圈 lint、pgvector service、**skip guard**（测试静默跳过 = 失败） | `.github/workflows/ci-python.yml` | `.github/workflows/ci.yml` |
| fixture 哲学："*Deliberately small: 重点是有真实外键的第二个 schema，不是更多数据*" | `dev_tools/fixtures/sql/pagila_analytics.sql` | `fixtures/sql/bank_fixture.sql` 的 D-01~D-11 设计 |

**说明**：思想借鉴不产生许可义务。如果后续直接复制其中代码，
请在那些文件头部保留 SPDX 声明（Apache-2.0 要求保留版权与许可声明）。

### askdb（内部项目）

| 参考了什么 | 影响到了本项目哪里 |
|---|---|
| 语义层的数据形态：`db_column_meta` 的 label / synonyms / unit / code_mapping / column_role / semantic_dimension_code | `app/engine/semantic/models.py` 的 `ColumnDef` |
| 指标来源表映射（指标 → 物理表）的思路 | `MetricDef.source_tables` |
| 三层权限纵深：表过滤 → 行级 WHERE 注入 → 列白名单 | 尚未搬入（Step 0 计划） |
| 业务规则清单（机构口径、时点对比、百分比、名称字段、报表禁 JOIN） | `app/engine/validation/rules.py` 的 6 条规则 |
| 回归 fixture 的字段命名与业务语义（`fiv_lev_cla_flag`、`current_balnc_cny` 等） | `fixtures/sql/bank_fixture.sql` |

注意：**业务规则本身是业务知识，不是代码**，不产生代码归属问题。

## 四、本项目自身

版权归项目作者。**许可证待定** —— 在 askdb 授权问题明确之前，
不建议声明为任何开源许可。
