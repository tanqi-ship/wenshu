"""双引擎对照门禁。

这是本项目最重要的 CI 能力，也是"基于 askdb 起步"的核心价值：
**有了可跑的旧引擎做 baseline，重构才可能被证明没有变差。**

规则（按严重程度）：

    REGRESSION   旧引擎对、新引擎错          -> 失败（绝对红线）
    DROP         总体准确率下降超过 2 个百分点 -> 失败
    COST_UP      LLM 调用数上升超过 10%       -> 警告
    SLOWER       平均耗时上升超过 50%         -> 警告
    IMPROVEMENT  旧错、新对                  -> 记录（这是重构的意义）

报告格式（baseline 与 current 相同）::

    {
      "generated_at": "2026-11-20T10:00:00+08:00",
      "engine": "legacy" | "wenshu",
      "cases": {
        "AQ-001": {
          "correct": true,
          "answer_path": "etl_calculated",
          "duration_ms": 1200,
          "llm_calls": 3
        }
      }
    }

用法::

    python -m scripts.check_regression \\
        --baseline eval/baseline_report.json \\
        --current  eval/current_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ACCURACY_DROP_LIMIT_PP = 2.0
COST_UP_LIMIT_PCT = 10.0
SLOWER_LIMIT_PCT = 50.0


@dataclass
class CaseOutcome:
    case_id: str
    correct: bool
    answer_path: str = ""
    duration_ms: int = 0
    llm_calls: int = 0


@dataclass
class Finding:
    kind: str
    case_id: str
    detail: str


@dataclass
class Report:
    engine: str = ""
    cases: dict[str, CaseOutcome] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Report:
        raw = json.loads(path.read_text(encoding="utf-8"))
        report = cls(engine=str(raw.get("engine") or ""))
        for case_id, payload in (raw.get("cases") or {}).items():
            report.cases[case_id] = CaseOutcome(
                case_id=case_id,
                correct=bool(payload.get("correct")),
                answer_path=str(payload.get("answer_path") or ""),
                duration_ms=int(payload.get("duration_ms") or 0),
                llm_calls=int(payload.get("llm_calls") or 0),
            )
        return report

    @property
    def accuracy(self) -> float:
        if not self.cases:
            return 0.0
        return 100.0 * sum(1 for case in self.cases.values() if case.correct) / len(self.cases)

    @property
    def total_llm_calls(self) -> int:
        return sum(case.llm_calls for case in self.cases.values())

    @property
    def avg_duration_ms(self) -> float:
        if not self.cases:
            return 0.0
        return sum(case.duration_ms for case in self.cases.values()) / len(self.cases)


def compare(baseline: Report, current: Report) -> list[Finding]:
    findings: list[Finding] = []

    for case_id, old in baseline.cases.items():
        new = current.cases.get(case_id)
        if new is None:
            findings.append(Finding("MISSING", case_id, "新引擎没有跑这条用例"))
            continue
        if old.correct and not new.correct:
            findings.append(
                Finding(
                    "REGRESSION",
                    case_id,
                    f"旧引擎答对（{old.answer_path}），新引擎答错（{new.answer_path}）",
                )
            )
        elif not old.correct and new.correct:
            findings.append(
                Finding(
                    "IMPROVEMENT",
                    case_id,
                    f"旧引擎答错（{old.answer_path}），新引擎答对（{new.answer_path}）",
                )
            )

    drop = baseline.accuracy - current.accuracy
    if drop > ACCURACY_DROP_LIMIT_PP:
        findings.append(
            Finding(
                "DROP",
                "-",
                f"准确率从 {baseline.accuracy:.1f}% 降到 {current.accuracy:.1f}%（-{drop:.1f}pp，"
                f"上限 {ACCURACY_DROP_LIMIT_PP}pp）",
            )
        )

    if baseline.total_llm_calls:
        growth = 100.0 * (current.total_llm_calls - baseline.total_llm_calls) / baseline.total_llm_calls
        if growth > COST_UP_LIMIT_PCT:
            findings.append(
                Finding(
                    "COST_UP",
                    "-",
                    f"LLM 调用数从 {baseline.total_llm_calls} 升到 {current.total_llm_calls}"
                    f"（+{growth:.1f}%，上限 {COST_UP_LIMIT_PCT}%）",
                )
            )

    if baseline.avg_duration_ms:
        growth = 100.0 * (current.avg_duration_ms - baseline.avg_duration_ms) / baseline.avg_duration_ms
        if growth > SLOWER_LIMIT_PCT:
            findings.append(
                Finding(
                    "SLOWER",
                    "-",
                    f"平均耗时从 {baseline.avg_duration_ms:.0f}ms 升到 "
                    f"{current.avg_duration_ms:.0f}ms（+{growth:.1f}%）",
                )
            )

    return findings


def has_blocking(findings: list[Finding]) -> bool:
    """``REGRESSION`` 与 ``DROP`` 是红线，其余只警告。"""
    return any(finding.kind in {"REGRESSION", "DROP", "MISSING"} for finding in findings)


def render_summary(
    baseline: Report,
    current: Report,
    findings: list[Finding],
) -> str:
    lines = [
        "# 双引擎对照报告",
        "",
        f"| 指标 | 旧引擎（{baseline.engine or '?'}） | 新引擎（{current.engine or '?'}） |",
        "|---|---:|---:|",
        f"| 用例数 | {len(baseline.cases)} | {len(current.cases)} |",
        f"| 准确率 | {baseline.accuracy:.1f}% | {current.accuracy:.1f}% |",
        f"| LLM 调用总数 | {baseline.total_llm_calls} | {current.total_llm_calls} |",
        f"| 平均耗时 | {baseline.avg_duration_ms:.0f} ms | {current.avg_duration_ms:.0f} ms |",
        "",
    ]

    if not findings:
        lines.append("无差异。")
        return "\n".join(lines) + "\n"

    blocking = [f for f in findings if f.kind in {"REGRESSION", "DROP", "MISSING"}]
    others = [f for f in findings if f not in blocking]

    lines.append(f"## 阻断项（{len(blocking)}）")
    lines.append("")
    if blocking:
        lines.append("| 类型 | 用例 | 说明 |")
        lines.append("|---|---|---|")
        lines.extend(f"| {f.kind} | {f.case_id} | {f.detail} |" for f in blocking)
    else:
        lines.append("无。")
    lines.append("")

    lines.append(f"## 其它（{len(others)}）")
    lines.append("")
    if others:
        lines.append("| 类型 | 用例 | 说明 |")
        lines.append("|---|---|---|")
        lines.extend(f"| {f.kind} | {f.case_id} | {f.detail} |" for f in others)
    else:
        lines.append("无。")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="双引擎回归门禁")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--current", required=True)
    parser.add_argument("--summary", default="eval/regression_summary.md")
    args = parser.parse_args(argv)

    baseline = Report.load(Path(args.baseline))
    current = Report.load(Path(args.current))

    findings = compare(baseline, current)
    summary = render_summary(baseline, current, findings)

    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(summary, encoding="utf-8")
    print(summary)

    if has_blocking(findings):
        print("回归门禁未通过。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
