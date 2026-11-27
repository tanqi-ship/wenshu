# EX-004 delta 时点顺序测量修正实施计划

日期：2026-11-27。依据：[已确认的 Spec](../specs/2026-11-27-ex004-delta-measurement-design.md)。
实施分支：`fix/ex004-delta-order`。本地实施与离线验证记录见下方；当前状态以 `docs/CONTEXT.md` 为准，已提交、推送并创建 PR，等待 CI 与合并。

## 目标与边界

修正 EX-004 的人工预期及手写响应，并用独立数值证明题意是五月减四月。执行顺序为：补测试 → 旧数据红灯 → 两处数据修正 → 转绿 → 全量回归 → 文档同步。

- 不改题面、其他题目、prompt、schema、运行器、判定器、语义层、编译器、校验器或执行器。
- 不改黄金 SQL、共享数据库 fixture、权限及评分算法；不新增评测题，不调用真实模型。
- 不重录 cassette，不生成标准答案，不修改历史真实报告。
- Spec 确认不等于提交、推送、PR 或合并授权；外部交付另行取得明确授权。

## T0：实施入口与工作区保护

- [x] 确认开始实施；重新核对 `git status`、HEAD 和差异，计划编写时基线为 `main@17e6b51`。
- [x] 保留本任务已有的 `docs/CONTEXT.md` 修改、Spec 和本 Plan，不清理、不覆盖、不回退。
- [x] 检查同名分支不存在后，从 main 保留这些文档修改创建 `fix/ex004-delta-order`；若分支已存在或基线变化，先确认如何处理。
- [x] 在仓库根运行相关冒烟，记录结果：

```powershell
uv run pytest tests/engine/test_judging.py tests/engine/test_extraction_replay.py tests/engine/test_preparation_replay.py
```

## T1：独立判定方向测试

修改 `tests/engine/test_judging.py` 的抽取判定分区，不重构既有 AQ 用例加载与执行判定测试。

- [x] 增加小型抽取用例加载辅助函数：按文件位置定位仓库根，UTF-8 读取 `tests/fixtures/intent_extraction/cases.yaml` 的 `cases` 列表，按 ID 获取 EX-004。
- [x] 新增 `test_ex004_current_then_base_passes`：独立手写候选映射，使用 `intent_from_mapping` 构造 Intent，将完整真实 case 交给 `judge_extraction` 并断言通过。
  - 指标固定为“贷款余额”，`comparison="delta"`、`grain="month_end"`、points 为 `["2026-05-31", "2026-04-30"]`。
- [x] 新增 `test_ex004_reversed_points_mismatch`：独立写定反序日期 `["2026-04-30", "2026-05-31"]`，其余候选字段不变。
  - 断言判定失败，且阶段/错误码为 `understanding / INTENT_MISMATCH`。
- [x] 候选不得从 `case.expected` 复制、替换或派生；不修改加载的 case，保留其 `compile: true`。

## T2：严格回放到 SQLite 数值测试

修改 `tests/engine/test_extraction_replay.py`，保留已有固定回放、消息漂移与权限测试。

- [x] 新增参数化异步测试 `test_ex004_replay_delta_values`，参数 ID 为 `current_then_base` 和 `reversed_points`。
- [x] 从现有 `CASES` 按 ID 获取 EX-004，用原题面和 `ReplayLLM(inner=None, strict=True)` 回放；monkeypatch 真实客户端 `complete`，误触立即失败。
- [x] 每个参数独立回放一次，断言命中/未命中为 `(1, 0)`，模型仍为手写 fixture、token 为零、元数据为回放。
- [x] 正向直接使用回放 Intent；反向仅用嵌套 `dataclasses.replace` 倒置其 `time_range.points`，不得从 YAML 预期构造 Intent。
- [x] 两个方向均使用演示语义层、`Scope.of([TABLE])` 和 SQLite 方言编译；将 SQL 交给既有 `validate_sql`，使用名字目录、对应 Intent 和同一 Scope，校验通过后才执行。
- [x] 用 `contextlib.closing(sqlite3.connect(":memory:"))` 管理隔离数据库；测试中手写最小表 `qa_loan_acc_detail(etl_dt TEXT, current_balnc_cny REAL)`。
- [x] 仅插入 `("2026-04-30", 100)`、`("2026-05-31", 130)` 两行合成数据，不读取或修改共享业务 fixture。
- [x] 使用 `sqlite3.Row`，断言恰好一行，按列名比较完整结果字典：
  - 正向：`{"cur_balance": 130, "base_balance": 100, "delta": 30}`。
  - 反向：`{"cur_balance": 100, "base_balance": 130, "delta": -30}`。
- [x] 列名及数值独立写定，不能从 YAML、编译器常量或另一方向结果计算；失败时也必须关闭连接。

## T3：红灯确认与两处数据修正

- [x] 保持原数据不变，先运行新增测试，记录真实失败证据：

```powershell
uv run pytest tests/engine/test_judging.py tests/engine/test_extraction_replay.py -k ex004 -vv
```

- [x] 确认红灯来自目标缺陷：正确候选被拒、反序候选被接受；回放当前值/基准值/差值与独立常量相反。收集失败、缺表、回放未命中或 L4 失败不是目标红灯。
- [x] 仅修改 `tests/fixtures/intent_extraction/cases.yaml` 中 EX-004 的 `expected.time.points` 为 `[2026-05-31, 2026-04-30]`。
- [x] 仅修改 `tests/cassettes/intent_extraction/EX-004.json` 的响应 `time.points` 为同一顺序。
- [x] 保留消息哈希键 `be5140918cc79ad9`，以及题面、指标、delta、来源、模型名、零 token 元数据和其他字段；保持原文件格式。
- [x] 原样重跑上述命令，确认转绿。红绿之间不改测试断言或生产逻辑，不使用 skip、xfail 或重录。
- [x] 运行关联回归；澄清 fixture 的 EX-004 仍引用原抽取用例，不改槽位预期：

```powershell
uv run pytest tests/engine/test_judging.py tests/engine/test_extraction_replay.py tests/engine/test_preparation_replay.py tests/engine/test_compiler_comparison.py
```

## T4：全量离线门禁

- [x] 逐条运行，逐条检查退出码并记录结果；测试数量以实测为准，不预告新增总数：

```powershell
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python -m scripts.check_golden
uv run python -m scripts.evaluate
```

- [x] 黄金集保持 30/30，离线评测保持 71/71；不放宽门禁，不对无关文件做格式化。
- [x] 生成的离线报告不提交，不触碰 `eval/real/` 中任何历史产物。

## T5：文档同步与交付检查

- [x] `docs/CONTEXT.md`：更新实际实施状态、红绿及门禁证据和下一步；保留历史基线数字及 EX-004 有效性说明。
- [x] `docs/EVALUATION.md`：补充跨轮比较边界，不改评分算法或 transport/未完成定义。
  - EX-004 修正后仍参加 round 2，不标 excluded；正式调用预期仍为 20 条抽取 + 9 条执行，各 3 次，共 87 次。
  - 旧新直接比较排除 EX-004 的预期变化、AQ-006/AQ-008 的题面变化及 AQ-007 的排除变化。
  - 可比子集为 19 条抽取、7 条执行，分别汇总；完整指标与可比指标分开说明，记录代码版本、题集摘要与比较范围。
- [x] `docs/ROADMAP.md`：验收后追加测量修正历史，不重写既有 M5.1/M5.2 数字。
- [x] 明确 EX-004 的旧通过不能证明差值方向正确；无 compiler 阶段失败也不证明所有 SQL 业务语义正确。
- [x] 更新本 Plan 勾选；保留旧 run_id `20261127T064047Z-e239b55` 与原始报告，不宣称 round 2 已运行或真实准确率提升。
- [x] 核查差异仅包含两处数据、两个测试文件、本任务 Spec/Plan 和三份相关文档；检查文档链接及 `git diff --check`。
- [x] 记录验证结果、遗留项和后续交付步骤；本地实现已验证，未将其表述为远端交付完成。
- [ ] 获得对应授权后提交、推送、创建 PR 并通过 CI/审查后合并。完成此切片交付后恢复 prompt-v2 的方案确认。

## 本地验证记录（2026-11-27）

| 检查 | 实测结果 |
|---|---|
| 改动前关联冒烟 | 80 passed |
| EX-004 新测试，旧数据 | 4 failed：判定方向与三列数值相反 |
| 仅修两处时点后的同组测试 | 4 passed；未放宽断言 |
| 关联回归（T3 所列四个测试文件） | 101 passed |
| `uv run pytest` | 1591 passed；项目默认参数，保留 `--strict-markers` |
| `uv run ruff check .` | 通过 |
| `uv run ruff format --check .` | 165 files already formatted |
| `uv run python -m scripts.check_golden` | 30/30 |
| `uv run python -m scripts.evaluate` | 71/71，0 失败 |

早期诊断命令曾用 `-o addopts=` 覆盖默认参数以显示摘要；该结果不替代最终门禁。关联与全量测试已使用上表原始命令重跑，保留默认 `--strict-markers`；没有修改项目配置。首次格式检查指出新增测试两处换行，已仅格式化该测试文件后重新通过检查。

未调用真实模型，未重算或覆盖 round 1 报告；已提交、推送并创建 PR，等待 CI 与合并。

提交前独立复跑上述全部门禁，结果与上表一致。

## 停下条件

- 分支/基线异常、文档冲突或出现来源不明的额外修改。
- 新测试在旧数据上意外全绿，或失败并非目标时点顺序问题。
- cassette 未命中、消息键改变、真实客户端被触达，或需要修改被排除的代码/数据才能通过。
- 两处数据修正后仍失败、黄金或离线评测结果变化、门禁失败原因不明确。
- 需要新增模型调用、外部服务或扩大测量修正范围；先更新设计并确认，不静默继续。
