# M5.2 评测口径修正（round 2 前置）

日期：2026-11-27。状态：**已确认（2026-11-27，用户选择“先修测量口径”）**。

## 背景

正式基线 `20261127T064047Z-e239b55` 显示，AQ-006/AQ-007/AQ-008 的失败混入了题面、prompt 和语义层口径问题：

- AQ-006 题面没有说“报表”，但 extraction prompt 规定“不因目录配置自动切 report”；预期却要求 `report_hit`。
- AQ-007 的核销本金/已清收金额不在演示语义层，单 Intent 也无法产出两列结果。
- AQ-008 题面说“2026年每个月”，没有起止；预期/golden 使用 1–4 月范围。

如果直接据此调 prompt，模型会被要求猜题面和 prompt 本身不该要求的东西。

## 目标

把这三条变成**可测量的用例**，让下一轮 prompt 迭代测到模型行为，而不是 fixture/题面冲突：

1. AQ-006 题面改为显式报表请求，并使用已登记指标名“不良贷款率”；预期不变。
2. AQ-008 题面显式给出 2026 年 1–4 月范围；预期不变。
3. AQ-007 增加 `measurement.status=excluded` 和原因；运行器跳过并单列，不计入准确率分母。

## 非目标

- 不改 extraction prompt、编译器、校验器或语义层。
- 不新增核销指标，不扩展“单 Intent 多指标”。
- 不重跑真实模型；本轮只修测量口径。
- 不改 AQ-006/AQ-008 的 `standard_sql` 与 `expected.result`。
- 不修改其他 AQ 用例。

## 数据结构

评测用例新增可选字段：

```yaml
measurement:
  status: excluded
  reason: "演示语义层没有核销指标，单 Intent 无法产出两列结果"
```

只接受空、`excluded`；其他状态或缺少 reason 必须显式报错，不能静默跳过。

## 运行器行为

- 执行套件遇到 `measurement.status=excluded`：不发起模型调用，记入 `excluded_cases`。
- `report.json` / `report.md` / `review.md` 单列排除题及原因；准确率分母只含实际测量题。
- 退出码与“全部成功”保持一致：只选了排除题且无失败时不应报成样本失败。
- 黄金对照、`calls.jsonl`、预算逻辑不变。

## 失败方式

- 未知 `measurement.status` 或空 reason：加载/运行前置阶段报错，不进入模型调用。
- 排除题不得出现在 `attempts_detail`，也不得被静默计入失败。
- AQ-006 新题面必须能在 SQLite fixture 上执行；若报表表缺失，测试应失败而不是回退。

## 测试策略

- `scripts.validate_cases` 通过。
- 运行器：排除题不计入分母；AQ-006 新题面 + SQLite 报表表能通过；AQ-008 新题面 + 明确范围能通过。
- 全量 pytest、Ruff、格式；`scripts.evaluate` 仍 71/71。

## 文档影响

- 新增本 Spec 与配套 Plan。
- 合并后更新 `docs/CONTEXT.md` / `ROADMAP.md`：说明 round 1 基线使用旧题面，AQ-006/007/008 不可直接与 round 2 比较。
- `docs/EVALUATION.md` 的评测集纪律在新增用例字段后保持；如有新术语再登记。
