"""只读与副作用校验。

一个 SELECT 根节点**不足以**说明查询是只读的 —— 所以整棵树都要走一遍。
这是第一道防线，不是唯一一道：真正执行时还应该在只读事务里跑
（Oracle ``SET TRANSACTION READ ONLY`` / PostgreSQL ``default_transaction_read_only``）。
"""

from __future__ import annotations

from sqlglot import exp
from sqlglot import parse as sqlglot_parse
from sqlglot.errors import ParseError

from app.engine.validation.policy import ValidationPolicy
from app.engine.validation.problems import Problem


def _nodes(*names: str) -> tuple[type[exp.Expression], ...]:
    """按名字取节点类型，缺失的跳过。

    sqlglot 的小版本之间节点类会增删，硬写 ``exp.TruncateTable`` 之类
    会让升级变成运行时报错。宁可少检查一个节点，也不要 import 失败。
    """
    resolved: list[type[exp.Expression]] = []
    for name in names:
        node = getattr(exp, name, None)
        if isinstance(node, type) and issubclass(node, exp.Expression):
            resolved.append(node)
    return tuple(resolved)


#: 有写副作用或改变会话状态的语句节点。
_WRITING_NODES = _nodes(
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Drop",
    "Alter",
    "TruncateTable",
    "Command",
    "Lock",
    "Transaction",
    "Commit",
    "Rollback",
)


def first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def parse_single_statement(
    sql: str,
    dialect: str | None,
) -> tuple[exp.Expression | None, list[Problem]]:
    """解析出唯一一条只读查询。

    返回 ``(statement, problems)``。``problems`` 非空时 ``statement`` 可能仍是
    可用的（例如"解析成功但不是 Query"这种），调用方据此决定是否继续深入检查。
    """
    try:
        statements = [s for s in sqlglot_parse(sql, read=dialect or None) if s is not None]
    except ParseError as exc:
        return None, [
            Problem(
                code="SQL_PARSE_ERROR",
                message=f"SQL 无法解析：{first_line(str(exc))}",
                severity="fatal",
            )
        ]

    if not statements:
        return None, [
            Problem(code="SQL_EMPTY", message="没有拿到任何 SQL 语句。", severity="fatal")
        ]

    if len(statements) > 1:
        return None, [
            Problem(
                code="SQL_MULTI_STATEMENT",
                message=f"一次只能提交一条语句，当前有 {len(statements)} 条。",
                severity="fatal",
            )
        ]

    statement = statements[0]
    if not isinstance(statement, exp.Query):
        return statement, [
            Problem(
                code="SQL_NOT_READ_QUERY",
                message=f"只允许只读查询（SELECT 或 WITH … SELECT），当前是 {statement.key.upper()}。",
                severity="fatal",
            )
        ]
    return statement, []


def find_side_effects(
    statement: exp.Expression,
    policy: ValidationPolicy,
) -> list[Problem]:
    """整棵树里的写操作、加锁、以及有副作用的内置函数。"""
    problems: list[Problem] = []

    for node in statement.find_all(*_WRITING_NODES):
        problems.append(
            Problem(
                code="SQL_SIDE_EFFECT",
                message=f"只读查询里不允许出现 {node.key.upper()}。",
                severity="fatal",
            )
        )

    forbidden = policy.forbidden_functions | policy.forbidden_sequence_functions
    for func in statement.find_all(exp.Func):
        name = _function_name(func)
        if name and name in forbidden:
            problems.append(
                Problem(
                    code="SQL_FORBIDDEN_FUNCTION",
                    message=f"只读查询里不允许调用函数 {name}()。",
                    severity="fatal",
                )
            )

    return _dedupe(problems)


def _function_name(func: exp.Func) -> str:
    if isinstance(func, exp.Anonymous):
        return str(func.this or "").strip().lower()
    try:
        return (func.sql_name() or "").strip().lower()
    except (AttributeError, ValueError):
        return ""


def _dedupe(problems: list[Problem]) -> list[Problem]:
    seen: set[tuple[str, str]] = set()
    unique: list[Problem] = []
    for problem in problems:
        key = (problem.code, problem.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(problem)
    return unique
