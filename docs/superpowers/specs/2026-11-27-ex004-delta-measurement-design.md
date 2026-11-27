# EX-004 delta 时点顺序测量修正

日期：2026-11-27。设计方案与本文已由用户确认。实施步骤见 [配套 Plan](../plans/2026-11-27-ex004-delta-measurement-plan.md)，当前状态以 `docs/CONTEXT.md` 为准。

## 背景与复现证据

prompt-v2 设计核对发现，EX-004 的手写预期与既有 delta 契约相反：

- 题面：`2026年5月末相比2026年4月末贷款余额增量`。
- `tests/fixtures/intent_extraction/cases.yaml` 的预期和对应手写 cassette 都使用 `[2026-04-30, 2026-05-31]`。
- `app/engine/compiler/comparison.py` 按 `points[0]` 为当前、`points[1]` 为基准，计算当前减基准；不自动按日期排序。
- 本地编译复现：旧预期生成四月减五月的 SQL。
- 本地判定复现：旧顺序被 `judge_extraction` 判为通过；符合题意的 `[2026-05-31, 2026-04-30]` 被判为 `understanding / INTENT_MISMATCH`。

现有回放测试比较响应与预期，并检查 SQL 可编译、可通过 L4；两份手写数据同时写反时，这些检查不能证明差值方向正确。

这是人工预期的数据缺陷，不是要求编译器改变 delta 语义，也不是为了提高模型分数而迁就输出。用户已选择先单独修正测量口径，再恢复 prompt-v2。

## 目标与非目标

目标：修正 EX-004 的当前/基准顺序，用独立数值断言防止响应与预期再次一起写反，并明确跨轮比较边界。

非目标：

- 不修改题面、其他题目、黄金 SQL 或 AQ 结果预期。
- 不修改 extraction prompt、抽取 schema、语义层、编译器、校验器、执行器或判定器逻辑。
- 不更改权限、错误分类、评分规则或模型调用次数。
- 不调用真实模型，不重新录制 cassette，不覆盖历史真实运行产物。
- 不新增日期推断能力；无年份的“4月末”仍保留为 unresolved，不能补系统年份。

## 数据、接口与边界

仅人工修正以下两处既有数据：

1. `tests/fixtures/intent_extraction/cases.yaml`：EX-004 的 `expected.time.points` 改为 `[2026-05-31, 2026-04-30]`。
2. `tests/cassettes/intent_extraction/EX-004.json`：手写响应中的 `time.points` 改为同一顺序。

其余字段不变，包括 `comparison=delta`、题面、指标、来源标记、模型名和零 token 元数据。

请求消息、prompt、目录和协议版本均未改变，因此 cassette 的消息哈希键保持不变。不得从编译产物或真实模型响应自动生成预期；不得在测试运行时重造 cassette 键。

生产接口与数据流不变：问题 → 严格回放抽取 → Intent → L3 编译 → L4 校验。数值回归额外在测试内的隔离 SQLite 数据上执行 SQL；不修改共享业务 fixture。

权限仍由调用方 Scope 注入。新增数值测试使用纯合成数据，不接触外部数据库；现有权限、拒答与严格离线回放测试继续保留。

## 防反向测试

在现有测试文件中补充以下回归，不新增评测题：

### 预期与判定方向

在 `tests/engine/test_judging.py` 加载真实 EX-004 用例，并独立手写候选 Intent：

- 当前为五月末、基准为四月末：判定通过。
- 仅把两个时点倒置：判定失败，阶段为 `understanding`，错误码为 `INTENT_MISMATCH`。

候选时点必须独立写定，不能从 `case.expected` 复制，以免两份数据同时出错仍然通过。

### 回放到数值结果

在 `tests/engine/test_extraction_replay.py` 增加 EX-004 专项数值测试：

- 使用现有题面与严格 cassette 回放，禁止真实模型调用。
- 沿用演示语义层编译，并经过既有 L4 校验。
- 在测试专用的内存 SQLite 中写入四月末余额 100、五月末余额 130。
- 按列名独立断言 `cur_balance=130`、`base_balance=100`、`delta=30`；不能只断言首行某个单元格匹配。
- 倒置回放 Intent 的时点后，独立断言当前值 100、基准值 130、差值 -30，证明两个顺序不是等价表示。
- 数值预期使用手写常量，不能由被测编译器或 YAML 预期计算产生。

`tests/fixtures/clarification/cases.yaml` 已引用 EX-004，无需修改其槽位预期；运行准备层回放测试，确认引用链继续通过。

## 历史与后续比较

- 保留 round 1 的 run_id `20261127T064047Z-e239b55`、原始产物与当时口径下的汇总数字，不覆盖或追溯改写。
- 在当前状态中追加说明：EX-004 的旧“通过”依据有误，不能作为差值方向正确的证据；旧轮次无 compiler 阶段失败也不等于所有 SQL 业务语义正确。
- 修正后的 EX-004 仍参加 round 2，不标为 excluded。预计仍为 20 条抽取 + 9 条执行，各 3 次，共 87 次正式调用。
- 与 round 1 直接比较时，除 AQ-006/AQ-008 题面变化和 AQ-007 排除外，再排除 EX-004 的预期变化。
- 可比子集为 19 条抽取、7 条执行，分别汇总；完整轮次指标与可比子集指标分开说明，沿用既有 transport/未完成口径。
- 不修改运行器来自动重算旧报告；后续人工归因中标明题集摘要、代码版本与比较范围。

## 验证与验收标准

实施时先添加上述测试，确认修正前因顺序错误失败，再人工修正两处数据并验证转绿。测试失败时不得放宽断言。

相关检查：

```powershell
uv run pytest tests/engine/test_judging.py tests/engine/test_extraction_replay.py tests/engine/test_preparation_replay.py
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python -m scripts.check_golden
uv run python -m scripts.evaluate
```

验收要求：

- `[当前五月末, 基准四月末]` 通过判定，倒置时点被判为不匹配，且独立 SQLite 数值证明为五月减四月。
- 黄金集保持 30/30，离线评测保持 71/71；这些结果只证明离线契约，不代表真实模型准确率。
- 全量测试和静态检查通过，无放宽、静默跳过或新增网络调用。
- 数据变更仅包含 EX-004 的两处时点顺序；没有 prompt、运行逻辑、共享数据库 fixture 或黄金 SQL 改动。
- 历史真实报告不变，文档明确该题不可直接跨轮比较。

设计阶段已运行抽取调用、schema、严格回放三个测试文件，全部通过；该冒烟不是上述新增测试或全量验收的替代。

## 文档影响与后续

- 实施遵循配套 Plan，使用单独的修正分支，不混入 prompt-v2。
- `docs/CONTEXT.md`：记录发现、确认/实现状态与下一步，保留历史基线数字并追加有效性说明。
- `docs/EVALUATION.md`：补充 EX-004 预期修正后的跨轮比较边界，评分算法不变。
- `docs/ROADMAP.md`：实施完成后记录此测量修正切片。
- 完成本切片及相关检查后，恢复 prompt-v2 的方案确认；不提前声称 round 2 已运行或准确率提升。
