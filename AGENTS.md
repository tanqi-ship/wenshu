# AGENTS.md

本文件是 AI 编码代理和新会话进入仓库的统一入口。目标是让任何模型在不依赖聊天记录的情况下，快速恢复正确上下文并按同一边界工作。

> 最后更新：2026-11-27

## 1. 读取顺序

新会话必读：

1. `AGENTS.md`（本文件）：硬边界与会话协议。
2. `docs/CONTEXT.md`：当前状态、能做什么、下一步、已知坑。
3. `git status` 和最近提交。

按任务需要再读：

- 改开发流程、分支、PR、提交 → `docs/DEVELOPMENT.md`
- 改评测、黄金集、报告、真实模型验收 → `docs/EVALUATION.md`
- 功能任务 → 当前任务对应的 `docs/superpowers/specs/`（设计加末尾执行清单）；`docs/superpowers/plans/` 只有历史记录和少数大型任务的独立 Plan

不要依赖聊天记录、模型记忆或 README 中的测试数量推断当前状态。当前状态只以 `docs/CONTEXT.md` 为准。

## 2. 项目一句话

`wenshu` 是一个银行问数引擎的学习与工程实践项目：

```text
问题 -> Intent -> 确定性编译 SQL -> 独立校验 -> 只读执行
```

核心目标是验证：把 SQL 生成从模型手里拿走，能否提高准确率、权限可控性和可解释性。

## 3. 硬边界

- 只有 L1 理解层允许调用模型。L3 编译、L4 校验、L5 执行必须是确定性的。
- SQL 只在 L3 一处生成，权限只通过 `Scope` 注入。
- L4 只接收 SQL 文本，不读取 L3 的中间产物。
- 不复制 askdb、auto-ontology 或其他项目的代码；只借鉴设计。
- 不做前端、登录、RBAC 管理后台、场景管理、知识库后台和多数据源连接器。
- 不把真实生产数据库、真实密钥、敏感数据或公共模型密钥提交到仓库。
- 不修改黄金 SQL 来迁就实现。黄金答案只能人工评审后修改。
- 不把 replay/fake 的通过率说成真实模型准确率。
- 不静默扩大任务范围。发现需要改架构、权限、评测口径时，先停下并更新设计。
- 不在未验证的情况下声称“测试通过”“准确率提升”“可以上生产”。

## 4. 开发流程

非平凡改动必须遵循：

```text
Issue -> Spec（含执行清单）-> 人工确认 -> 分支 -> 小步实现 -> 测试 -> PR -> CI -> 自审 -> 合并 -> 更新状态
```

单人项目可以不建 Issue，直接用 Spec 作为任务入口，见 `docs/DEVELOPMENT.md` 4.1 和 4.3。

不单独写 Plan 文档：执行步骤写成 Spec 末尾的短清单，随 Spec 一起确认。只有实施顺序本身就是设计的任务（新模块、拆多个 PR、跨多次会话）才另写 Plan。

简单 typo、文档修正、测试补充可以直接提交，但仍要跑相关检查。

一次只做一个可独立验证的纵向切片。不要把配置化、真实模型、数据库、本体和 ReAct 混在一次改动里。

## 5. 常用命令

```powershell
uv sync --locked --group dev
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python -m scripts.check_golden
uv run python -m scripts.evaluate
uv run python -m scripts.evaluate_real   # 真实模型基线：人工步骤，需要密钥，不在 CI
```

日常 Python、pytest、ruff 和项目脚本优先使用 `uv run`。不要提交 `.venv`、`.env`、`build/`、缓存或数据库文件。

## 6. 验证门禁

改动完成后至少运行与改动相关的检查：

- Python 代码：`uv run ruff check .`、`uv run ruff format --check .`、`uv run pytest`
- SQL 编译/校验：`uv run python -m scripts.check_golden`
- 评测/报告：`uv run python -m scripts.evaluate`
- 真实模型基线：`uv run python -m scripts.evaluate_real`（人工步骤，需要密钥，不在 CI；输出 `eval/real/` 不提交）
- 端到端：运行相关 `demo_query` 或专项测试
- 文档：检查链接、状态和术语是否一致

测试失败时修代码，不修改预期答案来让测试通过。任何跳过、屏蔽或放宽检查都必须在 PR 中说明理由。

## 7. 文档规则

- 当前状态只写 `docs/CONTEXT.md`。
- 计划和历史写 `docs/ROADMAP.md`。
- 稳定架构写 `docs/ARCHITECTURE.md`。
- 重要决策写 `docs/DECISIONS.md` 的 ADR。
- 功能设计和执行清单写 `docs/superpowers/specs/`。
- 独立 Plan 只在 `docs/DEVELOPMENT.md` 4.3 列出的大型任务中才写，放 `docs/superpowers/plans/`；其中已有文件是历史记录，不迁移、不删除。
- 术语写 `docs/GLOSSARY.md`。
- README 只保留稳定概述、快速开始和文档索引，不维护易变的测试数量和进度。

## 8. 会话协议

开始工作前：

```text
1. 读本文件和 docs/CONTEXT.md
2. 确认当前任务、验收标准和非目标
3. git status；必要时 git pull --ff-only
4. 跑最小冒烟测试
5. 复述任务和验证方式后再改代码
```

结束工作前：

```text
1. 跑相关测试和静态检查
2. 更新 Spec 末尾执行清单的勾选状态
3. 更新 docs/CONTEXT.md 的当前状态和下一步
4. 检查 git diff 只包含本任务
5. commit 并 push
6. 输出改动文件、验证命令、测试结果、未解决问题和下一步
```

## 9. 完成标准

一个任务只有同时满足以下条件才算完成：

- 验收标准满足。
- 新增或更新了测试。
- 相关测试通过，没有静默跳过。
- 没有修改黄金答案来迁就实现。
- 权限和失败路径有覆盖。
- 文档和状态已同步。
- 没有未说明的生成物、临时文件或密钥。
- 已 commit 并 push。
- 留下了可继续的下一步。

详细规则见 `docs/DEVELOPMENT.md`。

## 10. 必须停下来问的情况

- 需求与当前 Spec 冲突。
- 需要改变架构不变量、权限语义或评测口径。
- 需要引入新的模型调用、外部服务或生产数据。
- 测试失败原因不明确，或需要修改黄金答案。
- 发现现有文档与代码严重不一致。
- 任务范围明显超出当前 Issue 或已确认的 Spec。

先说明冲突和可选方案，不要自行猜测并继续。
