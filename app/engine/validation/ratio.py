"""R-RATIO-001：独立识别规范占比数据流，不调用或读取 L3 中间产物。"""

from __future__ import annotations

from calendar import monthrange
from decimal import Decimal, InvalidOperation

from sqlglot import exp

from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.models import Intent, ShareSpec
from app.engine.validation.policy import ValidationPolicy
from app.engine.validation.problems import Problem


class _ShapeError(ValueError):
    pass


def share_of_total_invalid(
    statement: exp.Expression,
    intent: Intent | None,
    scope: Scope | None,
    policy: ValidationPolicy,
) -> list[Problem]:
    if intent is None or intent.share is None:
        return []
    try:
        _require(isinstance(intent.share, ShareSpec), "占比意图类型无效。")
        return _check(statement, intent, scope, policy)
    except _ShapeError as exc:
        return [_problem("RATIO_SHAPE_INVALID", str(exc))]


def _check(statement, intent, scope, policy) -> list[Problem]:
    _require(isinstance(statement, exp.Select), "占比根必须是 SELECT。")
    _require(len(list(statement.find_all(exp.Select))) == 3, "占比须为分组、整体窗口、展示三层。")
    _require(len(list(statement.find_all(exp.Table))) == 1, "占比须只读取一张物理表一次。")
    _require(len(list(statement.find_all(exp.Subquery))) == 2, "占比不能夹带其他子查询。")
    _require(
        statement.find(exp.Join, exp.Union, exp.CTE, exp.Distinct, exp.Star) is None,
        "占比不允许 JOIN/UNION/CTE/DISTINCT/通配符。",
    )
    _select_args(statement, {"expressions", "from_", "where", "order"})
    totals_source = _from(statement)
    _require(isinstance(totals_source, exp.Subquery), "展示层必须读取整体层。")
    totals = totals_source.this
    _require(isinstance(totals, exp.Select) and bool(totals_source.alias), "整体层必须是有别名的 SELECT。")
    _select_args(totals, {"expressions", "from_"})
    groups_source = _from(totals)
    _require(isinstance(groups_source, exp.Subquery), "整体层必须读取完整分组。")
    groups = groups_source.this
    _require(isinstance(groups, exp.Select) and bool(groups_source.alias), "分组层必须是有别名的 SELECT。")
    _select_args(groups, {"expressions", "from_", "where", "group"})
    table = _from(groups)
    _require(
        isinstance(table, exp.Table) and isinstance(table.this, exp.Identifier) and bool(table.name),
        "分组层必须直接读取具名物理表，不能是表函数。",
    )
    _require(
        not any(value for key, value in table.args.items() if key not in {"this", "db", "catalog", "alias"}),
        "物理来源不能携带采样或其他限定。",
    )
    aliases = [table.alias_or_name.casefold(), groups_source.alias.casefold(), totals_source.alias.casefold()]
    _require(len(set(aliases)) == 3, "物理表和派生表别名必须独立。")
    physical, grouped_alias, totals_alias = aliases
    group = groups.args.get("group")
    _require(isinstance(group, exp.Group) and len(group.expressions) == 1, "占比必须按单个编号列分组。")
    _require(
        not any(value for key, value in group.args.items() if key != "expressions"), "不能使用扩展分组。"
    )
    key = group.expressions[0]
    _require(isinstance(key, exp.Column) and _qualifier(key) == physical, "分组必须绑定唯一物理表编号。")
    outputs = groups.expressions
    _require(
        len(outputs) in {2, 3} and all(isinstance(p, exp.Alias) for p in outputs),
        "分组投影必须明确编号、可选名称和金额别名。",
    )
    names = [p.alias for p in outputs]
    _require(
        len({name.casefold() for name in (*names, "total_value", "share")}) == len(names) + 2,
        "占比输出别名冲突。",
    )
    _require(
        _column(outputs[0].this, physical, key.name) and names[0].casefold() == key.name.casefold(),
        "编号投影与分组不一致。",
    )
    if len(outputs) == 3:
        display = outputs[1].this
        _require(isinstance(display, exp.Max) and isinstance(display.this, exp.Column), "名称只能 MAX 展示。")
        _require(
            _qualifier(display.this) == physical and display.this.name.casefold() == names[1].casefold(),
            "名称来源或别名不一致。",
        )
    measure = outputs[-1].this
    _require(
        isinstance(measure, exp.Sum) and len(list(measure.find_all(exp.AggFunc))) == 1,
        "分组金额必须是普通 SUM，不能嵌套聚合。",
    )
    _require(
        all(_qualifier(c) == physical for c in groups.find_all(exp.Column)), "底层所有列须指向唯一主表别名。"
    )
    _require(len(totals.expressions) == len(names) + 1, "整体层不能丢失或增加分组输出。")
    _passthrough(totals.expressions[:-1], grouped_alias, names)
    total = totals.expressions[-1]
    _require(isinstance(total, exp.Alias) and total.alias.casefold() == "total_value", "缺少整体输出列。")
    window = total.this
    _require(
        isinstance(window, exp.Window) and len(list(statement.find_all(exp.Window))) == 1,
        "整体必须由唯一窗口计算。",
    )
    _require(
        not any(value for name, value in window.args.items() if name not in {"this", "over"}),
        "整体窗口不能有 PARTITION/ORDER/frame。",
    )
    _require(
        isinstance(window.this, exp.Sum) and _column(window.this.this, grouped_alias, names[-1]),
        "整体必须对原分组金额求和。",
    )
    _require(len(statement.expressions) == len(names) + 2, "展示层须输出编号、名称、金额、整体和占比。")
    _passthrough(statement.expressions[:-1], totals_alias, [*names, "total_value"])
    ratio = statement.expressions[-1]
    _require(isinstance(ratio, exp.Alias) and ratio.alias.casefold() == "share", "缺少占比输出。")
    _ratio(ratio.this, totals_alias, names[-1])
    for condition in _terms(statement.args.get("where")):
        _require(
            _false(condition) or _predicate_column(condition, totals_alias, key.name),
            "展示层只能筛选占比编号，不能改变整体。",
        )
    order = statement.args.get("order")
    _require(isinstance(order, exp.Order) and len(order.expressions) == 1, "占比必须按编号稳定排序。")
    ordered = order.expressions[0]
    _require(
        isinstance(ordered, exp.Ordered)
        and not ordered.args.get("desc")
        and _column(ordered.this, totals_alias, key.name),
        "最终排序必须是占比编号升序。",
    )
    time = intent.time_range
    _require(
        time is not None
        and time.grain == "month_end"
        and time.start is None
        and time.end is None
        and len(set(time.points)) == 1,
        "占比意图需要单个月末时点。",
    )
    point = time.points[0]
    _require(point.day == monthrange(point.year, point.month)[1], "占比时点不是月末。")
    expected_time = exp.EQ(
        this=exp.column(policy.snapshot_column, table=physical),
        expression=exp.Literal.string(point.isoformat()),
    )
    predicates = _terms(groups.args.get("where"))
    keys = {_canonical(p) for p in predicates}
    _require(_canonical(expected_time) in keys, "指定时点必须位于物理表 WHERE。")
    source_name = ".".join(part.name for part in table.parts).casefold()
    policies = (
        []
        if scope is None
        else [_policy(p, physical) for p in scope.row_policies if p.table.casefold() == source_name]
    )
    policy_keys = {_canonical(p) for p in policies}
    problems = []
    if not policy_keys.issubset(keys):
        problems.append(_problem("RATIO_SCOPE_POLICY_MISSING", "行权限必须完整保留在分组前。"))
    allowed_key_filters = policy_keys | {_canonical(expected_time)}
    for predicate in predicates:
        _require(
            _false(predicate) or _predicate_column(predicate, physical), "底层条件必须是规范的 AND 等值/IN。"
        )
        referenced = {c.name.casefold() for c in predicate.find_all(exp.Column)}
        if key.name.casefold() in referenced and _canonical(predicate) not in allowed_key_filters:
            problems.append(
                _problem("RATIO_DENOMINATOR_FILTERED", "占比展示过滤提前进入分组，缩小了整体分母。")
            )
        if policy.snapshot_column.casefold() in referenced:
            _require(_canonical(predicate) in allowed_key_filters, "不能改变已指定的统计时点。")
    return problems


def _ratio(expression, alias, measure):
    expression = _unwrap(expression)
    _require(isinstance(expression, exp.Div), "占比必须是分组金额除以整体，不能缩放或补零。")
    denominator = _unwrap(expression.expression)
    _require(
        isinstance(denominator, exp.Nullif)
        and _column(denominator.this, alias, "total_value")
        and _number(denominator.expression, 0),
        "分母须为 NULLIF(整体金额, 0)。",
    )
    numerator = _unwrap(expression.this)
    if isinstance(numerator, exp.Mul):
        left, right = _unwrap(numerator.this), _unwrap(numerator.expression)
        decimal = left if isinstance(left, exp.Literal) else right
        column = right if decimal is left else left
        _require(
            _number(decimal, 1)
            and any(c in decimal.this.lower() for c in (".", "e"))
            and _column(column, alias, measure),
            "分子只允许必要的浮点提升，禁止乘 100 或截断。",
        )
    elif isinstance(numerator, exp.Cast):
        kind = numerator.args.get("to")
        _require(
            isinstance(kind, exp.DataType)
            and kind.this in {exp.DataType.Type.DOUBLE, exp.DataType.Type.FLOAT}
            and _column(numerator.this, alias, measure)
            and not kind.expressions,
            "只允许无精度截断的浮点转换。",
        )
    else:
        raise _ShapeError("分子缺少安全的小数除法提升。")


def _policy(policy: RowPolicy, alias: str) -> exp.Expression:
    # 根据独立 Scope 重新表达规范权限，不共享编译器谓词或任何中间产物。
    values = [exp.Literal.string(str(v)) for v in policy.values if str(v)]
    if not values:
        return exp.EQ(this=exp.Literal.number(1), expression=exp.Literal.number(0))
    column = exp.column(policy.column, table=alias)
    if policy.operator == "in" or len(values) > 1:
        return exp.In(this=column, expressions=values)
    return exp.EQ(this=column, expression=values[0])


def _select_args(select, allowed):
    _require(
        not any(value for key, value in select.args.items() if key not in allowed),
        "占比查询层包含未支持的子句。",
    )


def _from(select):
    source = select.args.get("from_")
    _require(
        isinstance(source, exp.From) and source.this is not None and not source.expressions,
        "占比层缺少唯一 FROM。",
    )
    return source.this


def _passthrough(projections, alias, names):
    _require(
        len(projections) == len(names)
        and all(_column(p, alias, name) for p, name in zip(projections, names, strict=True)),
        "派生层必须原样传递完整输出，不能改写金额或分组。",
    )


def _qualifier(column):
    return ".".join(p for p in (column.catalog, column.db, column.table) if p).casefold()


def _column(node, alias, name):
    return (
        isinstance(node, exp.Column)
        and _qualifier(node) == alias.casefold()
        and node.name.casefold() == name.casefold()
    )


def _predicate_column(node, alias, name=None):
    if not isinstance(node, (exp.EQ, exp.In)) or not isinstance(node.this, exp.Column):
        return False
    column = node.this
    if _qualifier(column) != alias.casefold() or (
        name is not None and column.name.casefold() != name.casefold()
    ):
        return False
    if isinstance(node, exp.In):
        return (
            bool(node.expressions)
            and not node.args.get("query")
            and all(isinstance(v, exp.Literal) for v in node.expressions)
        )
    return isinstance(node.expression, exp.Literal)


def _unwrap(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _terms(where):
    if where is None:
        return []
    node = _unwrap(where.this if isinstance(where, exp.Where) else where)
    if isinstance(node, exp.And):
        return [*_terms(node.this), *_terms(node.expression)]
    return [node]


def _canonical(node):
    def normalize(item):
        if isinstance(item, exp.Identifier):
            return exp.to_identifier(item.name.casefold())
        if isinstance(item, exp.Paren):
            return item.this
        return item

    return _unwrap(node).copy().transform(normalize).sql()


def _number(node, value):
    if not isinstance(node, exp.Literal) or node.is_string:
        return False
    try:
        return Decimal(node.this) == value
    except InvalidOperation:
        return False


def _false(node):
    return isinstance(node, exp.EQ) and _number(node.this, 1) and _number(node.expression, 0)


def _require(condition, message):
    if not condition:
        raise _ShapeError(message)


def _problem(code, message):
    return Problem(code=code, rule="R-RATIO-001", message=message)
