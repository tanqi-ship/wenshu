"""指标公式展开：``MetricDef.formula`` -> AST 片段。

公式在语义层里是**字符串**，这是唯一允许的"文本知识"入口。两种写法都支持：

1. **带别名占位符**（推荐，无歧义）::

       SUM(CASE WHEN {tbl}.fiv_lev_cla_flag IN ('30','40','50')
                THEN {tbl}.current_balnc_cny ELSE 0 END)

2. **不带占位符**（历史数据常见）::

       SUM(current_balnc_cny)

   这种写法由 :func:`qualify_columns` 把**未限定**的列补上表别名。
   解析时保留显式限定名；双表路径随后只接受主表物理名或本次主表别名。

两条纪律：

* 公式**必须能解析成恰好一个表达式**，否则 ``FORMULA_INVALID``。
  不允许"猜一下模型想写什么"——那是把不确定性又请回来。
* 不在这里检查列是否存在。列存在性属于 L4（``validation.names``），
  因为编译器和校验器必须互相独立（ADR-0005）。
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from app.engine.compiler.errors import FORMULA_INVALID, JOIN_FORMULA_UNSUPPORTED, CompileError
from app.engine.semantic.models import MetricDef

#: 公式里可以用这些占位符表示"当前表别名"。
ALIAS_PLACEHOLDERS: tuple[str, ...] = ("{tbl}", "{t}")


def parse_metric_formula(
    metric: MetricDef,
    *,
    alias: str,
    dialect: str = "sqlite",
) -> exp.Expression:
    """把指标公式展开成可以直接放进 SELECT 投影的表达式。"""
    raw = str(metric.formula or "").strip()
    if not raw:
        raise CompileError(
            "symbol_table",
            FORMULA_INVALID,
            f"指标 {metric.code} 没有配置计算公式。",
        )

    text = raw
    for placeholder in ALIAS_PLACEHOLDERS:
        if placeholder in text:
            text = text.replace(placeholder, alias)

    projection = _parse_projection(text, metric=metric, dialect=dialect)
    # 即使公式混用占位符与裸列，也要把所有裸列绑定到主表。
    return qualify_columns(projection, alias)


def _parse_projection(text: str, *, metric: MetricDef, dialect: str) -> exp.Expression:
    try:
        statements = [node for node in sqlglot.parse(f"SELECT {text}", read=dialect) if node is not None]
        parsed = statements[0] if len(statements) == 1 else None
    except Exception as exc:  # sqlglot 的异常类型随版本变动，统一收口
        raise CompileError(
            "symbol_table",
            FORMULA_INVALID,
            f"指标 {metric.code} 的公式无法解析：{exc}",
        ) from exc

    if not isinstance(parsed, exp.Select) or any(
        value for key, value in parsed.args.items() if key != "expressions"
    ):
        raise CompileError(
            "symbol_table",
            FORMULA_INVALID,
            f"指标 {metric.code} 的公式不是单个表达式。",
        )
    expressions = parsed.expressions
    if len(expressions) != 1:
        raise CompileError(
            "symbol_table",
            FORMULA_INVALID,
            f"指标 {metric.code} 的公式包含 {len(expressions)} 个表达式，期望 1 个。",
        )
    projection = expressions[0]
    # 公式里自带的 AS 别名由编译器统一覆盖，这里剥掉避免出现嵌套别名。
    if isinstance(projection, exp.Alias):
        projection = projection.this
    return projection.copy()


def qualify_columns(expression: exp.Expression, alias: str) -> exp.Expression:
    """给所有**未限定**的列补上表别名。

    已限定的列（``x.col``）不动：那表示公式作者有意引用别的表。
    """

    def _qualify(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and not node.table and node.name:
            return exp.column(node.name, table=alias)
        return node

    return expression.copy().transform(_qualify)


def bind_join_metric(
    expression: exp.Expression, *, primary: str, secondary: str, alias: str
) -> exp.Expression:
    """双表路径仅允许主表指标，显式物理表名规范化到本次别名。

    关联表度量即使来自唯一维表，也可能按事实记录数重复累计，因此本轮拒绝。
    """
    if alias.casefold() == secondary.casefold():
        raise CompileError(
            "type_check", JOIN_FORMULA_UNSUPPORTED, "主表别名与关联表物理名冲突，无法无歧义绑定公式。"
        )
    if expression.find(exp.Query, exp.Window) is not None:
        raise CompileError("type_check", JOIN_FORMULA_UNSUPPORTED, "双表指标公式不支持子查询或窗口函数。")
    result = expression.copy()
    for column in result.find_all(exp.Column):
        qualifier = ".".join(part for part in (column.catalog, column.db, column.table) if part).casefold()
        if qualifier and qualifier not in {primary.casefold(), alias.casefold()}:
            raise CompileError(
                "type_check", JOIN_FORMULA_UNSUPPORTED, f"双表指标不能引用非主表列 {column.sql()}。"
            )
        column.set("table", exp.to_identifier(alias))
        column.set("db", None)
        column.set("catalog", None)
    return result
