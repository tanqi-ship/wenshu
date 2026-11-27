# 开发规范

本文件定义 wenshu 的日常开发流程、双机协作方式和完成标准。目标是让同一项目在两台电脑、多个 AI 会话之间可重复、可验证地推进。

## 1. 基本原则

- 小步交付：一次只完成一个可独立验证的纵向切片。
- 先定义后实现：非平凡改动先有 Spec 和 Plan。
- 测试是证据：没有测试结果的“完成”不算完成。
- 单一事实来源：当前状态只在 `docs/CONTEXT.md`，不重复维护。
- 不静默扩范围：重构、依赖升级、格式化和功能分开提交。
- 不修改黄金答案来迁就实现。
- 不依赖聊天记录或模型记忆保存项目状态。

## 2. 环境

每台电脑独立安装 uv 和项目虚拟环境：

```powershell
uv sync --locked --group dev
uv run pre-commit install    # 每台电脑装一次
```

提交钩子（ruff、行尾空白、密钥检测等）只在装过 pre-commit 的电脑上生效，所以每台电脑都要执行一次
`uv run pre-commit install`。装好后的第一次提交会联网下载钩子环境，可能要等几分钟，属于正常现象；
此时按 Ctrl-C 会中止本次提交，而不是跳过检查。不要用 `git commit --no-verify` 绕过钩子。

约定：

- `.venv/` 不提交，不同步。
- `.env` 不提交，不同步；只提交 `.env.example`。
- API Key、数据库密码、生产连接串不得进入仓库。
- `uv.lock` 必须提交，依赖变更后运行 `uv lock`。
- Python、pytest、ruff 和项目脚本优先使用 `uv run`。
- `build/`、缓存、数据库文件和评测报告（`eval/report.*`）不提交。

## 3. 双机切换协议

开始工作前：

```powershell
git status
git pull --ff-only
uv sync --locked --group dev
uv run pytest
```

结束工作前，先更新 `docs/CONTEXT.md`，写清：

- 当前做到哪一步。
- 本次改了什么。
- 测试证据是什么。
- 下一步做什么。
- 是否有阻塞或未完成项。

状态更新必须在 commit 之前，否则另一台电脑拉到的是旧状态。更新完再提交并推送：

```powershell
git status
git add <本次相关文件>
git commit -m "<type>(<scope>): <具体改动>"
git push
```

硬规则：

- 切换电脑前必须 commit 并 push。
- 不依赖 `git stash`；stash 不会同步到另一台电脑。
- 任务未完成要换电脑时，推送 WIP 分支，并在 `CONTEXT.md` 或 Issue 中写明状态。
- 不在两台电脑上同时修改同一个任务。
- 开始工作前先 `git pull --ff-only`，有冲突先解决再继续。
- 不把一台电脑的 `.venv`、`.env`、缓存或数据库文件复制到另一台。

## 4. 任务生命周期

### 4.1 任务入口

每个非平凡任务都要有明确入口，二选一：

- GitHub Issue。
- `docs/superpowers/plans/` 中的任务清单。

任务必须写清：

- 目标。
- 非目标。
- 验收标准。
- 涉及模块。
- 风险和测试方式。

没有验收标准的任务不进入开发。

### 4.2 设计

以下情况必须先写 Spec：

- 新模块或新数据流。
- 权限、执行、模型调用边界变化。
- 语义层格式变化。
- 路由策略变化。
- 评测口径变化。
- 数据库或迁移变化。

Spec 至少包含：背景、目标、非目标、接口、数据结构、失败方式、权限边界、测试策略、文档影响。

简单 typo、测试补充、文档修正可以直接提交。

### 4.3 计划

Plan 拆成可独立验证的小任务。每个任务同时写：

- 要改什么。
- 怎么证明没改坏。
- 需要更新哪些文档。

测试任务应先于或伴随实现任务，而不是最后补。

### 4.4 实现

- 一次只做一个纵向切片。
- 每完成一个切片就跑对应测试。
- 不顺手重构无关代码。
- 发现设计与现实冲突时停下，更新 Spec，而不是静默改方向。
- 不把“能跑”当成“正确”；必须对照验收标准。

## 5. Git 约定

### 5.1 分支

推荐：

```text
feat/<short-name>
fix/<short-name>
docs/<short-name>
eval/<short-name>
```

AI 会话创建的分支可以使用 `codex/<type>-<short-name>`。

### 5.2 提交信息

使用 Conventional Commits：

```text
feat(execution): 增加 PostgreSQL 只读执行器
fix(ci): 修复 workflow 配置
docs(evaluation): 明确真实模型评测边界
test(golden): 补充报表分表负例
```

一个提交只做一件事。不要把格式化、依赖升级和功能混在一起。

### 5.3 PR

单人项目也建议使用短分支 + PR。PR 的作用不是等待别人审批，而是：

- 合并前强制跑 CI。
- 留下改动说明和测试证据。
- 防止两台电脑同时改同一任务。
- 给 AI 一个明确的任务边界。

PR 描述至少包含：

- 目标和验收标准。
- 改了什么。
- 没改什么。
- 测试命令和结果。
- 风险和未解决问题。
- 文档更新情况。

自己检查后合并。Ruff、Pytest（in-memory）和 Golden-set gate 三个 job 必须通过才能合并。
`Pytest (postgres dialect)` 是可选的非阻塞 job（`continue-on-error: true`，目前没有带 `db` 标记的用例）：
它红了不阻塞合并，但也不能据此声称已验证 PostgreSQL 方言。

## 6. 测试门禁

按改动范围运行：

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python -m scripts.check_golden
uv run python -m scripts.evaluate
```

要求：

- 不允许静默跳过测试。
- 新增行为必须有正例和必要的负例。
- 权限、拒答、只读、超时、行数上限等失败路径要有覆盖。
- 修改 SQL 编译或校验时，必须跑黄金集。
- 修改评测或报告时，必须跑 `scripts.evaluate`。
- 修改文档时，检查链接、术语和状态一致性。

## 7. 文档更新矩阵

| 改动类型 | 必须更新 |
|---|---|
| 行为变化 | 相关测试、`CONTEXT.md`、必要的 Spec |
| 架构变化 | `ARCHITECTURE.md`、`DECISIONS.md` |
| 决策变化 | `DECISIONS.md` 的 ADR |
| 评测变化 | `EVALUATION.md`、`eval/` 用例 |
| 里程碑变化 | `ROADMAP.md`、`CONTEXT.md` |
| 快速开始变化 | `README.md` |
| 新术语 | `GLOSSARY.md` |
| 当前状态变化 | `CONTEXT.md` |

不要在 README、CONTEXT、ROADMAP 中重复维护同一份状态。README 只保留稳定概述和入口。

文档更新发生在任务边界：开始前确认要改哪些文档，结束时同步，不要求每次编辑都全量重写。
用增量修改；`CONTEXT.md` 保持在一页左右，里程碑历史和落地细节写入 `ROADMAP.md`。

## 8. AI 协作规则

- AI 产出的代码必须有人类 owner。
- AI 不能自行批准 Spec 或绕过 CI。
- 模型输出不是事实来源；仓库文档、测试和评测结果才是。
- Prompt 变更视为代码变更，必须评测。
- 不自动刷新黄金 SQL 或评测预期。
- 不把真实密钥、生产连接串或敏感数据写入仓库。
- 每次会话结束必须留下可继续的状态。
- 新会话先读 `AGENTS.md` 和 `docs/CONTEXT.md`，不要依赖聊天记录。

## 9. Definition of Done

一个任务只有同时满足以下条件才算完成：

- 验收标准满足。
- 新增或更新测试。
- 相关测试通过，没有静默跳过。
- 没有修改黄金答案来迁就实现。
- 权限和失败路径有覆盖。
- 文档和状态已同步。
- 没有未说明的生成物或临时文件。
- 已 commit 并 push。
- 留下了可继续的下一步。

## 10. 常见错误

- 只跑局部测试就声称全部通过。
- 修改黄金 SQL 让门禁变绿。
- 把 replay 的 100% 当成真实模型准确率。
- 在两台电脑上同时改同一个任务。
- 用 `git stash` 代替推送 WIP 分支。
- 提交 `.env`、`.venv`、数据库或密钥。
- 文档写“已完成”，但代码或测试没有证据。
- 一个 PR 混入功能、重构、依赖升级和格式化。
- 新电脑没装 pre-commit 就提交，或用 `--no-verify` 绕过钩子。
- 把评测报告 `eval/report.*` 提交进仓库。
