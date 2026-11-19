"""名字校验：表/列是否存在，以及表是否在本次检索范围内。

两种问题必须分开报，因为它们要模型做的事完全不同：

* ``UNKNOWN_TABLE`` / ``UNKNOWN_COLUMN`` —— 名字打错了。提示"是否想写 X"。
* ``TABLE_NOT_IN_SCOPE`` —— 表是真的，但检索没给过它。提示"先去检索"。

混在一起报会让模型去改一个并不存在的拼写错误，或者反过来对一个正确的表名
反复纠结 —— 这正是 auto-ontology 在 ``run_sql.py`` 的注释里点名要避免的：
"a false 'unknown column' sends the model after a mistake it did not make"。
"""

from __future__ import annotations

import difflib

from sqlglot import exp

from app.engine.validation.catalog import Catalog
from app.engine.validation.policy import Scope, ValidationPolicy
from app.engine.validation.problems import Problem


def _derived_names(statement: exp.Expression) -> set[str]:
    """不是物理表的表名：CTE 名与带别名的子查询。"""
    names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    names |= {sub.alias.lower() for sub in statement.find_all(exp.Subquery) if sub.alias}
    return names


def _suggest(name: str, options: list[str] | set[str]) -> str:
    candidates = [str(option).lower() for option in options if option]
    close = difflib.get_close_matches(name.lower(), candidates, n=3)
    return f"（是否想写：{', '.join(close)}？）" if close else ""


def _iter_physical_tables(statement: exp.Expression):
    """产出 (table_node, bare_name, schema|None)，跳过 CTE / 子查询别名。"""
    derived = _derived_names(statement)
    for table in statement.find_all(exp.Table):
        name = table.name
        if not name or name.lower() in derived:
            continue
        yield table, name, (table.db or None)


def check_tables(
    statement: exp.Expression,
    catalog: Catalog,
    *,
    scope: Scope | None = None,
    policy: ValidationPolicy | None = None,
) -> list[Problem]:
    policy = policy or ValidationPolicy()
    problems: list[Problem] = []

    for _table, name, schema in _iter_physical_tables(statement):
        owners = catalog.schemas_with(name, schema)

        if not owners:
            problems.append(
                Problem(
                    code="UNKNOWN_TABLE",
                    message=f"表不存在：{'.'.join(p for p in (schema, name) if p)}{_suggest(name, {t for _s, t in catalog.all_tables()})}",
                )
            )
            continue

        if not schema and len(owners) > 1:
            problems.append(
                Problem(
                    code="AMBIGUOUS_TABLE",
                    message=f"表名 {name} 存在于多个 schema（{', '.join(sorted(owners))}），请写全限定名。",
                )
            )
            continue

        if scope is not None and scope and not scope.allows(name, schema):
            problems.append(
                Problem(
                    code="TABLE_NOT_IN_SCOPE",
                    message=(
                        f"表 {'.'.join(p for p in (schema, name) if p)} 不在本次检索范围内。"
                        "请先检索它（用它的准确名称即可）再使用。"
                    ),
                )
            )

    return _dedupe(problems)


def check_columns(
    statement: exp.Expression,
    catalog: Catalog,
) -> list[Problem]:
    """列名校验。

    两种情形刻意不做判定：

    1. 未限定的列名，且查询里有 CTE / 子查询 —— 这个名字可能来自内层查询
       的输出，误报会让模型去改一个它没犯的错。
    2. 字段名就是某个 SELECT 别名 —— 外层可以合法引用它。
    """
    derived = _derived_names(statement)
    output_aliases = {alias.alias.lower() for alias in statement.find_all(exp.Alias) if alias.alias}

    table_columns: dict[str, set[str] | None] = {}
    for _table, name, schema in _iter_physical_tables(statement):
        columns = catalog.columns(name, schema)
        alias_name = _table.alias_or_name.lower()
        table_columns[alias_name] = columns
        table_columns.setdefault(name.lower(), columns)

    problems: list[Problem] = []

    for column in statement.find_all(exp.Column):
        name = column.name
        if not name or isinstance(column.this, exp.Star):
            continue

        qualifier = (column.table or "").lower()
        if qualifier:
            if qualifier in derived or qualifier not in table_columns:
                continue
            columns = table_columns[qualifier]
            if columns is not None and name.lower() not in columns:
                problems.append(
                    Problem(
                        code="UNKNOWN_COLUMN",
                        message=f"列不存在：{column.table}.{name}{_suggest(name, columns)}",
                    )
                )
            continue

        if derived or name.lower() in output_aliases:
            continue
        known = {col for cols in table_columns.values() if cols for col in cols}
        if (
            table_columns
            and all(cols is not None for cols in table_columns.values())
            and name.lower() not in known
        ):
            problems.append(
                Problem(
                    code="UNKNOWN_COLUMN",
                    message=f"列不存在：{name}{_suggest(name, known)}",
                )
            )

    return _dedupe(problems)


def count_distinct_tables(statement: exp.Expression) -> int:
    return len({name.lower() for _t, name, _s in _iter_physical_tables(statement)})


def uses_join(statement: exp.Expression) -> bool:
    return any(True for _ in statement.find_all(exp.Join))


def _dedupe(problems: list[Problem]) -> list[Problem]:
    seen: set[str] = set()
    unique: list[Problem] = []
    for problem in problems:
        if problem.message in seen:
            continue
        seen.add(problem.message)
        unique.append(problem)
    return unique
