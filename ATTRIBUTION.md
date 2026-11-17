# 代码来源与许可

## askdb

本项目初始骨架中的周边代码来自 **askdb** 项目（内部项目，未开源）。

* 所有者：项目作者本人
* 使用许可：经作者本人明确同意，用于本项目，**不用于商业用途**
* 保留的部分：数据模型、连接器、权限、前端、周边服务（清单见 `MIGRATION.md`）
* 重写的部分：编排引擎、SQL 生成、检索、分诊、澄清

askdb 本身**没有 LICENSE 文件**。这意味着默认状态下保留所有权利。
本项目对它的使用依赖于作者的口头/书面授权，而不是开源许可。

> ⚠️ 后续注意：如果本项目将来要商用、要开源、或要交给其他主体，
> 必须先解决 askdb 代码的授权问题。**当前状态只适用于作者授权的非商业用途。**

## 参考设计（未复制代码）

以下项目只借鉴了设计思想，没有复制代码：

### NVIDIA Auto Ontology（Apache-2.0）

借鉴了四处设计：

| 位置 | 借鉴了什么 |
|---|---|
| `retrieval/text_to_sql/react/run_sql.py` | `tables_not_shown` 的思路：用 sqlglot AST 强制"模型只能使用检索到的表"，而不是靠 prompt 约束 |
| `retrieval/text_to_sql/react/prompts.py` | 核心信条：*"rule blocks in the writer prompt lost accuracy every time they were measured"* —— 写在 prompt 里的 SQL 规则块，每次实测都让准确率下降 |
| `retrieval/entity_coverage/` | 覆盖率 = 命中实体数 / 问题实体数，作为"可答性"的一等公民 |
| `.github/workflows/ci-python.yml` | CI 结构：分圈 lint、pgvector service、**skip guard**（测试静默跳过 = 失败） |
| `dev_tools/fixtures/sql/pagila_analytics.sql` | fixture 设计哲学："*Deliberately small: the point is a second schema that has real foreign keys back into public, not more data*" |

这些是思想借鉴，实现是重写的。若后续直接复制其中代码，请在该文件头部
保留 SPDX 声明（Apache-2.0 要求保留版权与许可声明）。

### 业务规则

`eval/cases/*.yaml` 中引用的 `C-xx` 编号对应《信贷问数业务规则汇编》
（内部文档，作者：项目组）。规则本身是业务知识，不是代码。

## 本项目

版权归项目作者所有。许可证待定 —— 在 askdb 授权问题解决前，
不建议声明为开源许可。
