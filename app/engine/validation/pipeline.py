"""校验流水线实现。

单独成模块而不是写进 ``__init__``，是为了让包的导入保持轻量：
``Catalog`` / ``Scope`` / ``ValidationPolicy`` 这些纯数据结构不需要 sqlglot，
而单元测试和配置代码只想 import 它们。

如果 sqlglot 的导入放在包的 ``__init__`` 里，那么"想拿一个 Catalog"就会
连带拉起整个 SQL 解析器 —— 依赖方向就反了。
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlglot import exp

from app.engine.semantic.models import Intent
from app.engine.validation.catalog import Catalog
from app.engine.validation.names import check_columns, check_tables
from app.engine.validation.policy import Scope, ValidationPolicy
from app.engine.validation.problems import Problem, ValidationResult
from app.engine.validation.rules import (
    comparison_uses_subquery,
    join_not_allowed,
    monthly_topn_without_window,
    name_field_in_group_by,
    percentage_metric_scaled,
    table_count_exceeded,
)
from app.engine.validation.safety import find_side_effects, parse_single_statement

_SEVERITY_ORDER = {"fatal": 0, "error": 1, "warning": 2}


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
    """完整校验一条只读 SQL。

    顺序是刻意的：

    1. **解析** —— 连 SQL 都解析不出来就别往下走
    2. **副作用** —— 只读是硬红线，先于一切业务规则
    3. **名字** —— 表和列是否存在、表是否在检索范围内
    4. **业务规则** —— C-04 / C-05 / C-06 / C-07 / C-11 / C-12

    副作用排在业务规则前面的理由：一个带 DELETE 的 SQL，讨论它 GROUP BY
    对不对没有意义，而且给模型的修复提示会跑偏。
    """
    policy = policy or ValidationPolicy()
    result = ValidationResult(sql=sql)

    statement, parse_problems = parse_single_statement(sql, dialect)
    result.statement = statement
    result.extend(parse_problems)
    if statement is None or parse_problems:
        return _capped(result, policy)

    result.extend(find_side_effects(statement, policy))
    result.extend(check_tables(statement, catalog, scope=scope, policy=policy))
    result.extend(check_columns(statement, catalog))

    aliases = {alias for alias in percentage_aliases if alias}
    if not aliases and intent is not None and intent.metric:
        aliases = {intent.metric}

    result.extend(percentage_metric_scaled(statement, aliases))
    result.extend(name_field_in_group_by(statement, policy))
    result.extend(comparison_uses_subquery(statement, policy))
    result.extend(monthly_topn_without_window(statement, intent))
    result.extend(table_count_exceeded(statement, intent, policy))
    result.extend(join_not_allowed(statement, intent, policy))

    return _capped(result, policy)


def _capped(result: ValidationResult, policy: ValidationPolicy) -> ValidationResult:
    """限制问题条数，但 fatal 优先保留。

    否则一条致命错误被后面十条 warning 挤掉，模型会去修 warning
    而放着真正致命的错误不管。
    """
    if len(result.problems) <= policy.max_problems:
        return result
    ordered = sorted(result.problems, key=lambda p: _SEVERITY_ORDER[p.severity])
    kept = ordered[: policy.max_problems]
    dropped = len(result.problems) - len(kept)
    if dropped > 0:
        kept.append(
            Problem(
                code="PROBLEMS_TRUNCATED",
                message=f"还有 {dropped} 个问题未列出。",
                severity="warning",
            )
        )
    result.problems = kept
    return result


def select_output_aliases(statement: exp.Expression) -> list[str]:
    """最外层 SELECT 的输出别名。"""
    outer = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
    if outer is None:
        return []
    return [
        projection.alias_or_name
        for projection in outer.expressions
        if projection.alias_or_name
    ]
