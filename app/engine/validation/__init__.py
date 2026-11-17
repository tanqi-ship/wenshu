"""校验层：把"靠模型自觉"的规则换成"不可能违反"的检查。

这个包的 ``__init__`` 刻意**不**在顶层导入 sqlglot：

* ``Catalog`` / ``Scope`` / ``ValidationPolicy`` / ``Problem`` 是纯数据结构，
  测试和配置代码只想 import 它们，不该因此拉起整个 SQL 解析器。
* ``validate_sql`` 需要 sqlglot，所以它在被调用时才导入实现模块。

依赖方向保持单向：数据结构 -> 解析器，而不是反过来。

返回的 :class:`ValidationResult` 有两个用途：

* ``ok`` 决定能否执行
* ``feedback()`` 生成回灌给模型的修复提示 —— **必须配合原 SQL 一起回灌**，
  否则模型没法"修复"，只能重新抽签。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from app.engine.validation.catalog import Catalog
from app.engine.validation.policy import Scope, ValidationPolicy
from app.engine.validation.problems import Problem, Severity, ValidationResult

if TYPE_CHECKING:
    from app.engine.semantic.models import Intent

__all__ = [
    "Catalog",
    "Problem",
    "Scope",
    "Severity",
    "ValidationPolicy",
    "ValidationResult",
    "validate_sql",
]


def validate_sql(
    sql: str,
    *,
    catalog: Catalog,
    dialect: str | None = None,
    scope: Scope | None = None,
    intent: Intent | None = None,
    policy: ValidationPolicy | None = None,
    percentage_aliases: Iterable[str] = (),
) -> ValidationResult:
    """校验一条只读 SQL。实现见 :mod:`app.engine.validation.pipeline`。"""
    from app.engine.validation.pipeline import validate_sql as _validate_sql

    return _validate_sql(
        sql,
        catalog=catalog,
        dialect=dialect,
        scope=scope,
        intent=intent,
        policy=policy,
        percentage_aliases=percentage_aliases,
    )
