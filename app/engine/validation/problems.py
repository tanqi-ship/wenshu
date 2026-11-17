"""校验结果的结构。

每种问题都有一个**稳定的机器码**（``code``）。这一点很关键：

* 评测报告可以按 code 聚合，直接回答"哪一类规则在退化"
* 修复提示能精确定位，而不是"SQL 生成失败"这种没法排查的糊话
* 新增规则不必改动上报逻辑
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from sqlglot import exp

Severity = Literal["fatal", "error", "warning"]

#: 只有这两种 severity 会阻止 SQL 被执行。
BLOCKING: frozenset[Severity] = frozenset({"fatal", "error"})


@dataclass(frozen=True, slots=True)
class Problem:
    code: str
    message: str
    #: 触发此问题的业务规则编号，对应《信贷问数业务规则汇编》的 C-xx。
    rule: str = ""
    severity: Severity = "error"

    def render(self) -> str:
        tag = f"[{self.rule}] " if self.rule else ""
        return f"- {tag}{self.message}（{self.code}）"


@dataclass(slots=True)
class ValidationResult:
    sql: str
    problems: list[Problem] = field(default_factory=list)
    statement: exp.Expression | None = None

    @property
    def ok(self) -> bool:
        return not any(p.severity in BLOCKING for p in self.problems)

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.severity in BLOCKING]

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == "warning"]

    @property
    def codes(self) -> list[str]:
        return [p.code for p in self.problems]

    def extend(self, problems: list[Problem]) -> None:
        self.problems.extend(problems)

    def feedback(self) -> str:
        """回灌给模型的修复提示。

        只列问题、不列原 SQL —— 原 SQL 由重试构造器负责附上。
        这两件事分开是刻意的：上一次 askdb 的重试只传了错误信息、没传原 SQL，
        等于让模型"修复"一个它看不见的东西，重试实际退化成重新抽签。
        """
        if not self.problems:
            return ""
        lines = ["上述 SQL 存在以下问题，请只修复这些问题："]
        lines.extend(problem.render() for problem in self.problems)
        return "\n".join(lines)
