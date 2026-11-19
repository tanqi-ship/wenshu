"""编译追踪：把 ``compile_intent`` 的五步逐个打印出来。

这个脚本**不重写任何编译逻辑**。它做两件事：

1. 把编译器内部那 4 个构造函数（``parse_metric_formula`` / ``build_time_condition``
   / ``build_business_conditions`` / ``build_policy_conditions``）**单独调一遍**，
   打印它们的真实返回值 —— 这样你能看到"公式字符串怎么变成树"。
2. 再调一次 ``compile_intent``，把最终 ``Select`` 的 4 个空拆开打印。

如果第 1 步和第 2 步对不上，说明这里有 bug（或者脚本漏跟了编译器的改动）——
所以它同时是个自检工具。

用法::

    python -m scripts.trace_compile              # 默认跑「2026年4月末不良贷款余额」
    python -m scripts.trace_compile --trap       # 额外演示 from / from_ 那个坑
"""

from __future__ import annotations

from sqlglot import exp

from app.engine.compiler import compile_intent, month_end_range
from app.engine.compiler.conditions import build_business_conditions, build_policy_conditions
from app.engine.compiler.metrics import parse_metric_formula
from app.engine.compiler.time import build_time_condition
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.bank_mini import semantic_catalog
from app.engine.semantic.models import Filter, Intent

ALIAS = "t1"
DIALECT = "sqlite"


def line(title: str) -> None:
    print()
    print("─" * 78)
    print(f"  {title}")
    print("─" * 78)


def sql_of(node: exp.Expression) -> str:
    return node.sql(dialect=DIALECT, pretty=False)


def split_and(node: exp.Expression) -> list[exp.Expression]:
    """把 ``a AND b AND c`` 拆成 [a, b, c]。AND 是二叉树，所以要递归铺平。"""
    if isinstance(node, exp.And):
        return [*split_and(node.this), *split_and(node.expression)]
    return [node]


def show_tree(node: exp.Expression, depth: int = 0) -> None:
    """按缩进打印 AST。缩进 = 树的深度，这是看 AST 最直观的方式。"""
    label = node.key.upper()
    detail = ""
    if isinstance(node, exp.Identifier | exp.Column | exp.Literal | exp.Table):
        detail = f"  {node.sql(dialect=DIALECT, pretty=False)}"
    print(f"{'    ' * depth}{label}{detail}")
    for child in node.args.values():
        if isinstance(child, exp.Expression):
            show_tree(child, depth + 1)
        elif isinstance(child, list):
            for item in child:
                if isinstance(item, exp.Expression):
                    show_tree(item, depth + 1)


def _show_slots(select: exp.Select) -> None:
    """把一个 Select 的 4 个空拆开打印。"""
    print("  expressions（SELECT 投影，列表）:")
    for index, projection in enumerate(select.args.get("expressions") or [], 1):
        print(f"    [{index}] {type(projection).__name__:10} {sql_of(projection)}")

    from_ = select.args.get("from_")
    print(f"  from_      （★ 带下划线）: {sql_of(from_) if from_ else None}")

    where = select.args.get("where")
    print("  where      （业务 + 权限，用 AND 串起来；按顺序拆开）:")
    for condition in split_and(where.this) if where else []:
        print(f"    - {sql_of(condition)}")

    group = select.args.get("group")
    if group is None:
        print("  group      （无维度 → 整个 SELECT 只有一行）: None")
    else:
        print("  group      （只放编号列，绝不放名称列）:")
        for projection in group.expressions:
            print(f"    - {sql_of(projection)}")


def main(argv: list[str]) -> int:
    semantic = semantic_catalog()

    # 场景：demo_ask 的第一条问题「截至2026年4月末不良贷款余额」
    intent = Intent(metric="不良贷款余额", time_range=month_end_range(2026, 4))
    scope = Scope.of(
        ["qa_loan_acc_detail"],
        row_policies=(RowPolicy(table="qa_loan_acc_detail", column="org_no", values=("B001",)),),
    )

    line("输入：三个参数")
    print(f"  intent.metric        = {intent.metric!r}")
    print(f"  intent.time_range    = grain={intent.time_range.grain} points={intent.time_range.points}")
    print(f"  intent.dimensions    = {list(intent.dimensions)}")
    print(f"  scope.tables          = {sorted(scope.tables)}")
    print(f"  scope.row_policies    = {[p.column + '=' + str(p.values) for p in scope.row_policies]}")

    # ------------------------------------------------------------------
    line("① resolve —— 符号表查找（业务词 → 语义层条目）")
    metric = semantic.metric(intent.metric or "")
    assert metric is not None
    print(f"  semantic.metric({intent.metric!r})")
    print(f"    code          = {metric.code}")
    print(f"    name          = {metric.name}")
    print(f"    unit          = {metric.unit}")
    print(f"    source_tables = {list(metric.source_tables)}")
    print(f"    formula       = {metric.formula}")
    print()
    print("  同义词也走同一个入口（「不良余额」→ 同一个 MetricDef）：")
    by_synonym = semantic.metric("不良余额")
    print(f"    semantic.metric('不良余额') is semantic.metric('不良贷款余额') = {by_synonym is metric}")

    # ------------------------------------------------------------------
    line("② check —— 类型/范围检查（越权在编译期就拒绝，不等执行）")
    tables = semantic.metric_tables(metric)
    print(f"  metric_tables(metric) = {list(tables)}")
    print(f"  scope.allows({tables[0]!r}) = {scope.allows(tables[0])}")
    print("  ↑ 这里为 False 就会抛 CompileError(stage='scope', code='METRIC_OUT_OF_SCOPE')")

    # ------------------------------------------------------------------
    line("③ build —— 造节点（4 个构造器，各自独立）")
    print("  [3.1] 指标公式 → AST 片段： metrics.parse_metric_formula")
    metric_expr = parse_metric_formula(metric, alias=ALIAS, dialect=DIALECT)
    print(f"        类型 = {type(metric_expr).__name__}")
    print(f"        SQL  = {sql_of(metric_expr)}")
    print("        AST 树：")
    show_tree(metric_expr, depth=5)

    print()
    print("  [3.2] 时间条件 → AST 片段： time.build_time_condition")
    time_condition = build_time_condition(intent.time_range, alias=ALIAS, dialect=DIALECT)
    print(f"        类型 = {type(time_condition).__name__}")
    print(f"        SQL  = {sql_of(time_condition)}")

    print()
    print("  [3.3] 业务过滤 → 列表： conditions.build_business_conditions")
    business = build_business_conditions(intent, semantic, alias=ALIAS)
    print(f"        条数 = {len(business)}（这条 Intent 没有 filters，所以是空的）")
    demo_filter = Intent(
        metric="不良贷款余额",
        time_range=intent.time_range,
        filters=(Filter(dimension="机构", value="渝北支行"),),
    )
    filtered = build_business_conditions(demo_filter, semantic, alias=ALIAS)
    for condition in filtered:
        print(f"        若带 filters=机构/渝北支行 → {sql_of(condition)}")

    print()
    print("  [3.4] 权限过滤 → 列表： conditions.build_policy_conditions")
    policy = build_policy_conditions(scope, alias=ALIAS)
    for condition in policy:
        print(f"        SQL  = {sql_of(condition)}")
    print("        ★ 与 [3.3] 是两个函数、两个来源：用户改不了这一段（ADR-0004）")
    no_value_scope = Scope.of(
        ["qa_loan_acc_detail"], row_policies=(RowPolicy(table="qa_loan_acc_detail", column="org_no"),)
    )
    fail_closed = build_policy_conditions(no_value_scope, alias=ALIAS)
    print(f"        策略没取值时（fail-closed）→ {[sql_of(c) for c in fail_closed]}")

    # ------------------------------------------------------------------
    line("④ assemble —— 填 4 个空（这一步在 compile_intent 里）")
    select = compile_intent(intent, semantic, scope=scope, dialect=DIALECT, alias=ALIAS)
    print(f"  select.args 里实际有的键 = {sorted(k for k, v in select.args.items() if v)}")
    _show_slots(select)

    print()
    print("  同一个指标，加上维度「机构」之后 4 个空怎么变：")
    with_dim = compile_intent(
        Intent(
            metric="不良贷款余额",
            time_range=intent.time_range,
            dimensions=("机构",),
        ),
        semantic,
        scope=scope,
        dialect=DIALECT,
        alias=ALIAS,
    )
    _show_slots(with_dim)

    # ------------------------------------------------------------------
    line("⑤ render —— 渲染成方言 SQL")
    print(select.sql(dialect=DIALECT, pretty=True))

    print()
    print("  紧凑版（黄金集比对时用的就是这种归一化后的形式）：")
    print(f"    {select.sql(dialect=DIALECT, pretty=False)}")

    # ------------------------------------------------------------------
    line("完整 AST 树（缩进 = 深度）")
    show_tree(select)

    # ------------------------------------------------------------------
    if "--trap" in argv:
        line("附：from / from_ 那个坑，现场演示")
        good = exp.Select(expressions=[exp.Literal.number(1)], **{"from_": exp.From(this=exp.to_table("t"))})
        bad = exp.Select(expressions=[exp.Literal.number(1)], **{"from": exp.From(this=exp.to_table("t"))})
        print(f"  用 from_ （正确）: {good.sql(dialect=DIALECT)}")
        print(f"  用 from  （错误）: {bad.sql(dialect=DIALECT)}")
        print()
        print("  ★ 两行都不报错。第二行只是把 FROM 整段丢掉了 ——")
        print("    而 'SELECT 1' 是合法 SQL，所以 L4 的 6 条规则也抓不到。")

    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
