"""黄金集门禁：批量跑 ``eval/golden/cases.yaml`` 里的用例，输出差异。

一条命令回答：**引擎现在算得对不对。**

每条用例做三件事（见 :func:`scripts.golden.evaluate_case`）：

1. **编译** ``Intent`` → SQL（如果编译不过，直接记为失败并给 stage/code）
2. **比对** 与手写的黄金 SQL 是否结构等价（不通过时打印 unified diff）
3. **校验** 产出的 SQL 是否通过 L4（校验与比对互相独立，两个结论都要看）

故意**不提供** ``--update`` / ``--bless``：黄金 SQL 一旦能一键刷新，
这个门禁就只能证明"引擎没变"，不能证明"引擎算对了"。

用法::

    # 跑全部用例（人看）
    python -m scripts.check_golden

    # 与 CI 一致（ci.yml 的 golden job 就是这么调的）
    python -m scripts.check_golden \\
        --golden eval/golden --cases eval/cases --output eval/golden_report.json

    # 只跑一条
    python -m scripts.check_golden --only G-002

退出码：``0`` 全部通过；``1`` 有用例失败；``2`` 用例配置本身有问题。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from app.engine.semantic.bank_mini import semantic_catalog, validation_catalog
from scripts.golden import (
    DEFAULT_DIALECT,
    CaseOutcome,
    GoldenConfigError,
    SqlParseError,
    evaluate_case,
    load_cases,
    load_eval_case_ids,
)

DEFAULT_GOLDEN_DIR = "eval/golden"
DEFAULT_OUTPUT = "eval/golden_report.json"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="黄金集门禁")
    parser.add_argument("--golden", default=DEFAULT_GOLDEN_DIR, help="黄金集目录（含 cases.yaml）")
    parser.add_argument("--cases", default="", help="评测集目录，用于覆盖率交叉检查；可省略")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="JSON 报告路径")
    parser.add_argument("--summary", default="", help="Markdown 摘要路径（默认与 --output 同目录）")
    parser.add_argument("--dialect", default=DEFAULT_DIALECT)
    parser.add_argument("--only", action="append", default=[], help="只跑指定 id，可重复")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def render_console(outcomes: list[CaseOutcome]) -> str:
    """逐条打印结论；失败的把 diff 打出来。"""
    lines: list[str] = []
    for outcome in outcomes:
        case = outcome.case
        if outcome.ok:
            lines.append(f"  ✓ {case.id}  {case.shape}")
            continue

        lines.append(f"  ✗ {case.id}  {case.shape}  ← {outcome.failure_kind}")
        if outcome.compile_error:
            lines.append(f"      编译失败：{outcome.compile_error}")
        if outcome.comparison is not None and not outcome.matches:
            lines.extend(f"      {line}" for line in outcome.comparison.diff)
        if not outcome.validation_ok:
            lines.append(f"      L4 问题：{', '.join(outcome.validation_codes)}")
    return "\n".join(lines)


def render_summary(
    outcomes: list[CaseOutcome],
    *,
    golden_dir: str,
    eval_coverage: tuple[int, int, list[str]] | None,
    generated_at: str,
) -> str:
    """Markdown 摘要（CI 会把它当 artifact 上传，方便直接看）。"""
    passed = sum(1 for outcome in outcomes if outcome.ok)
    lines = [
        "# 黄金集门禁报告",
        "",
        f"- 生成时间：{generated_at}",
        f"- 用例目录：`{golden_dir}`",
        f"- 结果：**{passed} / {len(outcomes)} 通过**",
    ]
    if eval_coverage is not None:
        covered, total, missing = eval_coverage
        lines.append(f"- 评测集覆盖：{covered} / {total} 条评测用例已有黄金对照")
        if missing:
            lines.append(f"  - 尚无黄金对照：{', '.join(missing)}")
    lines.append("")

    if outcomes:
        lines.extend(
            [
                "| 用例 | 形状 | 结果 | 失败原因 | covers |",
                "|---|---|---|---|---|",
            ]
        )
        for outcome in outcomes:
            case = outcome.case
            mark = "✅" if outcome.ok else "❌"
            lines.append(
                f"| {case.id} | {case.shape} | {mark} | {outcome.failure_kind or '-'} "
                f"| {', '.join(case.covers) or '-'} |"
            )
        lines.append("")

    shapes = Counter(outcome.case.shape for outcome in outcomes)
    if shapes:
        lines.append("## 形状覆盖")
        lines.append("")
        lines.extend(f"- `{shape}`：{count} 条" for shape, count in sorted(shapes.items()))
        lines.append("")

    failures = [line for outcome in outcomes if not outcome.ok for line in _failure_rows(outcome)]
    if failures:
        lines.append("## 失败明细")
        lines.append("")
        lines.append("```diff")
        lines.extend(failures)
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def _failure_rows(outcome: CaseOutcome) -> list[str]:
    case = outcome.case
    rows = [f"# {case.id}  ← {outcome.failure_kind}"]
    if outcome.compile_error:
        rows.append(f"# 编译失败：{outcome.compile_error}")
    if outcome.comparison is not None and not outcome.matches:
        rows.extend(outcome.comparison.diff)
    if not outcome.validation_ok:
        rows.append(f"# L4：{', '.join(outcome.validation_codes)}")
    return rows


def render_report(
    outcomes: list[CaseOutcome],
    *,
    golden_dir: str,
    dialect: str,
    generated_at: str,
) -> dict[str, object]:
    """JSON 报告。结构稳定，M4 的评测报告可以直接读它。"""
    return {
        "generated_at": generated_at,
        "engine": "wenshu",
        "golden_dir": golden_dir,
        "dialect": dialect,
        "total": len(outcomes),
        "passed": sum(1 for outcome in outcomes if outcome.ok),
        "failed": sum(1 for outcome in outcomes if not outcome.ok),
        "cases": [
            {
                "id": outcome.case.id,
                "shape": outcome.case.shape,
                "ok": outcome.ok,
                "failure_kind": outcome.failure_kind,
                "matches": outcome.matches,
                "compile_error": outcome.compile_error,
                "validation_ok": outcome.validation_ok,
                "validation_codes": list(outcome.validation_codes),
                "covers": list(outcome.case.covers),
                "eval_case": outcome.case.eval_case,
                "golden": outcome.comparison.golden if outcome.comparison else "",
                "produced": outcome.comparison.produced if outcome.comparison else "",
                "diff": list(outcome.comparison.diff) if outcome.comparison else [],
            }
            for outcome in outcomes
        ],
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    generated_at = datetime.now(UTC).astimezone().isoformat(timespec="seconds")

    try:
        cases = load_cases(args.golden)
    except GoldenConfigError as exc:
        print(f"用例配置有误：{exc}", file=sys.stderr)
        return 2

    if args.only:
        wanted = {case_id.strip() for case_id in args.only}
        unknown = wanted - {case.id for case in cases}
        if unknown:
            print(f"--only 指定了不存在的用例：{', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        cases = [case for case in cases if case.id in wanted]

    semantic, catalog = semantic_catalog(), validation_catalog()

    print(f"黄金集：{args.golden}（{len(cases)} 条用例，方言 {args.dialect}）")
    outcomes: list[CaseOutcome] = []
    for case in cases:
        try:
            outcome = evaluate_case(case, semantic, catalog, dialect=args.dialect)
        except SqlParseError as exc:
            # 黄金 SQL 自己坏了：这是"用例配置有误"，不是"引擎算错了"。
            print(f"  ✗ {case.id}  黄金 SQL 无法解析：{exc}", file=sys.stderr)
            return 2
        outcomes.append(outcome)
        print(".", end="", flush=True)
    print()
    print(render_console(outcomes))

    # 交叉检查：评测集里哪些用例还没有黄金对照（M2/M4 用它盯进度）
    eval_coverage: tuple[int, int, list[str]] | None = None
    if args.cases:
        try:
            eval_ids = load_eval_case_ids(args.cases)
        except GoldenConfigError as exc:
            print(f"评测集读取失败：{exc}", file=sys.stderr)
            return 2
        covered_ids = {case.eval_case for case in cases if case.eval_case}
        eval_coverage = (
            len(covered_ids & set(eval_ids)),
            len(eval_ids),
            [eval_id for eval_id in eval_ids if eval_id not in covered_ids],
        )
        print(f"\n评测集覆盖：{eval_coverage[0]} / {eval_coverage[1]} 条评测用例已有黄金对照")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = render_report(outcomes, golden_dir=args.golden, dialect=args.dialect, generated_at=generated_at)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_path = Path(args.summary) if args.summary else output_path.with_name("golden_summary.md")
    summary_path.write_text(
        render_summary(
            outcomes,
            golden_dir=args.golden,
            eval_coverage=eval_coverage,
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )

    failed = [outcome for outcome in outcomes if not outcome.ok]
    print(f"\n结果：{len(outcomes) - len(failed)} / {len(outcomes)} 通过")
    print(f"报告：{output_path}")
    print(f"摘要：{summary_path}")

    if failed:
        print("\n黄金集门禁未通过。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
