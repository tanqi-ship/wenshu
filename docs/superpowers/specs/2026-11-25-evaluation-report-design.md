# M4.3/M4.4：离线评测报告与失败归因

日期：2026-11-25。本文定义统一离线评测的统计和归因契约。

## 目标

用一条命令汇总现有手写 fixture 的抽取、澄清、黄金编译/校验和一个本地 SQLite 端到端执行样本，生成机器可读 JSON 与人可读 Markdown。每个被执行的样本记录模型回放命中数、耗时、最终状态和失败归因；未纳入本轮的评测行为只记录覆盖状态，不伪造准确率。

## 套件与口径

- `extraction`：20 条 `intent_extraction` 样本。成功 Intent、预期抽取错误和预期编译错误均按各自手写期望判断；错误码或结构不一致才失败。
- `clarification`：20 条单轮准备样本。比较状态、是否保留抽取、缺失槽位和完整澄清对象；模型回放只允许命中固定 cassette。
- `golden`：30 条黄金用例，直接复用黄金门禁，包括 G-030 的预期编译拒绝。
- `execution`：使用临时本地业务 SQLite 执行 AQ-001，验证固定回放、编译、L4 和 L5 的最终结果。不会读取或写入外部数据库。
- `coverage`：列出 10 条 AQ 评测中有黄金对照的 8 条以及仍需行为评测的 AQ-007/AQ-009/AQ-010，不把未执行项计为失败。

每套报告包含 `total`、`passed`、`failed`、`accuracy`、`model_calls`、`elapsed_ms` 和 `records`。`accuracy` 是“符合该套件手写预期的记录数 / 已执行记录数”，不是自然语言真实准确率。报告顶层固定 `model_mode=replay`、`real_model=false`、`external_database=false`。

## 失败归因

按确定性责任链取第一处失败：`understanding` → `clarification` → `semantic` → `scope` → `compiler` → `validation` → `execution` → `data` → `expected_behavior`。每条失败包含 `stage`、稳定 `code`、简短消息和原始样本 ID；不把内部 SQL、密钥或绝对路径写入报告。

- 抽取异常归 `understanding`；Intent 与预期不一致也归该阶段。
- 澄清状态或追问对象不一致归 `clarification`。
- 未知指标/维度/码值归 `semantic`；Scope 错误归 `scope`。
- 其他 `CompileError` 归 `compiler`，L4 问题归 `validation`，SQLite 错误归 `execution`。
- 数据快照缺失或无数据语义属于 `data`；AQ-009/AQ-010 的“应披露/应拒答”在本轮仅列为 `coverage`，待行为评测实现后再归 `expected_behavior`。

## 输出与验收

命令：`python -m scripts.evaluate`；默认写入 `eval/report.json` 和 `eval/report.md`，支持 `--json`、`--markdown`、`--suite`。输出确定性排序；时间戳只放报告元数据，不参与快照比较。

验收：所有四套已执行样本通过，黄金 30/30；报告能按归因阶段汇总，单测覆盖错误码、失败阶段、回放调用数和 Markdown 内容；全量测试与 Ruff 通过。报告不宣称真实模型质量、生产延迟或外部数据库性能。
