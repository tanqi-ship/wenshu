# 开发规范

本文件定义 wenshu 的开发流程、双机切换方式和完成标准。这是一个单人学习项目，流程以够用为准。

## 1. 基本原则

- 说清楚再动手：开始改之前，用几句话确认要改什么、怎么验证。小改动不需要文档。
- 默认在 `main` 上工作：不开分支、不开 PR，除非用户要求。
- 小步交付：一次只完成一个可独立验证的改动。
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
- 任务未完成要换电脑时，把未完成的改动作为 WIP 提交并推送到 `main`，并在 `CONTEXT.md` 写明状态。
- 不在两台电脑上同时修改同一个任务。
- 开始工作前先 `git pull --ff-only`，有冲突先解决再继续。
- 不把一台电脑的 `.venv`、`.env`、缓存或数据库文件复制到另一台。

## 4. 改动流程

### 4.1 默认流程

绝大多数改动都走这一条：

1. 确认要改什么、怎么验证。有需要用户决定的点，先问。
2. 在 `main` 上改。
3. 跑第 6 节里与改动相关的检查。
4. 更新 `docs/CONTEXT.md`。
5. commit 并 push。

适用于改提示词、改评测数据、改文档、补测试、修 bug 和加一个小功能。

### 4.2 什么时候先停下来问

- 要改架构不变量、权限语义或评测口径。
- 要新增模型调用、接入外部服务或真实数据。
- 测试失败原因不明确，或需要修改黄金答案。
- 发现文档与代码严重不一致。
- 任务范围明显超出用户交代的范围。

问的方式：用几句话说清问题和可选做法，等用户回答。不需要为此写文档。

### 4.3 什么时候写 Spec、开分支或 PR

只有用户明确要求时才做。AI 不主动提议，也不因为别的项目这样做就照搬。

- Spec：用户想留一份设计记录时，写到 `docs/superpowers/specs/`，写清背景、目标、非目标、验收标准和测试方式，篇幅与风险相称。
- 分支或 PR：用户想在合并前看 CI 结果或改动说明时。

没有任何一类任务“必须”写 Spec 或走 PR。

### 4.4 实现时的要求

- 每完成一步就跑对应测试。
- 不顺手重构无关代码。
- 发现与现实冲突时停下来说明，而不是静默改方向。
- 不把“能跑”当成“正确”。

## 5. Git 约定

### 5.1 分支

默认直接在 `main` 上提交。用户要求时才使用短分支（`feat/`、`fix/`、`docs/`、`eval/` 前缀）和 PR。

分支和 PR 用完就删，不长期保留。还没合并的分支在 `CONTEXT.md` 里写明状态。

### 5.2 提交信息

使用 Conventional Commits：

```text
feat(execution): 增加 PostgreSQL 只读执行器
fix(ci): 修复 workflow 配置
docs(evaluation): 明确真实模型评测边界
test(golden): 补充报表分表负例
```

一个提交只做一件事。不要把格式化、依赖升级和功能混在一起。

### 5.3 CI

推送到 `main` 和 PR 都会触发 CI。Ruff、Pytest（in-memory）和 Golden-set gate 三个 job 应该通过。
`Pytest (postgres dialect)` 是可选的非阻塞 job（`continue-on-error: true`，目前没有带 `db` 标记的用例）：
它红了不阻塞，但也不能据此声称已验证 PostgreSQL 方言。

直接推送到 `main` 时，CI 在推送之后才运行。红了要马上修，不能放着。

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
| 行为变化 | 相关测试、`CONTEXT.md` |
| 架构变化 | `ARCHITECTURE.md`、`DECISIONS.md` |
| 决策变化 | `DECISIONS.md` 的 ADR |
| 开发流程变化 | `AGENTS.md`、本文件、`DECISIONS.md` |
| 评测变化 | `EVALUATION.md`、`eval/` 用例 |
| 里程碑变化 | `ROADMAP.md`、`CONTEXT.md` |
| 快速开始变化 | `README.md` |
| 新术语 | `GLOSSARY.md` |
| 当前状态变化 | `CONTEXT.md` |

不要在 README、CONTEXT、ROADMAP 中重复维护同一份状态。README 只保留稳定概述和入口。

文档更新发生在任务边界：开始前确认要改哪些文档，结束时同步，不要求每次编辑都全量重写。
用增量修改；`CONTEXT.md` 保持在一页左右，里程碑历史和落地细节写入 `ROADMAP.md`。
`docs/superpowers/` 是历史记录，新任务不要求更新它。

## 8. AI 协作规则

- AI 产出的代码必须有人类 owner。
- AI 不替用户决定流程：不主动新建分支、worktree、PR 或 Spec，不自行合并 PR。
- 需要用户决定的事当面问，不用长文档代替提问。
- 模型输出不是事实来源；仓库文档、测试和评测结果才是。
- Prompt 变更视为代码变更，必须评测。
- 不自动刷新黄金 SQL 或评测预期。
- 不把真实密钥、生产连接串或敏感数据写入仓库。
- 每次会话结束必须留下可继续的状态。
- 新会话先读 `AGENTS.md` 和 `docs/CONTEXT.md`，不要依赖聊天记录。

## 9. Definition of Done

一个任务只有同时满足以下条件才算完成：

- 用户要的结果达到了，并且验证过。
- 代码改动有对应的测试，相关测试通过，没有静默跳过。
- 没有修改黄金答案来迁就实现。
- 涉及权限或失败路径时有测试覆盖。
- 文档和状态已同步。
- 没有未说明的生成物或临时文件。
- 用户要求提交时，已 commit 并 push。
- 留下了可继续的下一步。

## 10. 常见错误

- 只跑局部测试就声称全部通过。
- 修改黄金 SQL 让门禁变绿。
- 把 replay 的 100% 当成真实模型准确率。
- 在两台电脑上同时改同一个任务。
- 用 `git stash` 代替提交。
- 提交 `.env`、`.venv`、数据库或密钥。
- 文档写“已完成”，但代码或测试没有证据。
- 为一个小改动先写长 Spec 或长 Plan、新开分支，改动本身却还没开始。
- AI 会话自作主张新建分支或 worktree。
- 新电脑没装 pre-commit 就提交，或用 `--no-verify` 绕过钩子。
- 把评测报告 `eval/report.*` 提交进仓库。
- 把其他项目的调研笔记或对照表写进本项目文档。
