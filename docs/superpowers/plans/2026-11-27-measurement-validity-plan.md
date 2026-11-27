# M5.2 评测口径修正实施计划

日期：2026-11-27。依据：`../specs/2026-11-27-measurement-validity-design.md`。
分支：`feat/measurement-validity`。本节不调用真实模型，不进入 CI 的真实密钥步骤。

## 1. 题库与 fixture

- [x] `eval/cases/asset_quality.yaml`：
  - AQ-006 question 改为“从报表查询2026年4月各产品的不良贷款率是多少？”；`standard_sql` / `expected` 不动。
  - AQ-008 question 改为“2026年1月到4月，每个月贷款余额最高的支行是哪个、余额多少？”；预期不动。
  - AQ-007 增加 `measurement: {status: excluded, reason: ...}`；预期不动。
- [x] `fixtures/sql/bank_sqlite_minimal.sql`：增加 `rpt_npl_by_product_202604` 表及 fixture 中的 4 行标准口径数据；只补执行套件需要的 202604。
- 验证：`uv run python -m scripts.validate_cases eval/cases`。

## 2. 运行器排除与报告

- [x] `scripts/evaluate_real.py`：
  - 新增 `measurement.status` 解析；未知状态或空 reason 显式报错。
  - 执行套件跳过 excluded 题，记入 `excluded_cases`。
  - `summarize_suite` / `report.json` / `report.md` / `review.md` 单列排除题，准确率分母只含实测题。
  - 只选排除题且无失败时退出码为 0。
- [x] 按行键匹配器允许键列不在第一列（AQ-006 报表含 `stat_month` 列）；补测试。
- [x] 补测试：排除题不计入分母、单列原因；AQ-006 报表执行；AQ-008 明确范围执行。
- 验证：`uv run pytest tests/engine/test_evaluate_real.py`。

## 3. 检查与合并

- [x] `uv run ruff check .`、`uv run ruff format --check .`、`uv run pytest`（1587 passed）、`uv run python -m scripts.evaluate`（71/71）。
- [x] 更新 `docs/CONTEXT.md` / `docs/ROADMAP.md`：记录测量口径修正，说明 round 1 与 round 2 的 AQ-006/007/008 不可直接比较；**不**记录新的模型准确率。
- [ ] 推送分支，开 PR，必需检查通过后 squash 合并，拉回 `main`。

## 停下来的条件

- AQ-006 新题面仍无法在 SQLite 上执行，或需要改 `expected.result` 才能继续。
- AQ-007 需要扩语义层/多指标能力才能表达。
- 任何测试失败的原因不明确。
