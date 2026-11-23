"""R-ORG-001：机构匹配只能使用原值 EQ/IN；不依赖 Intent 或 L3。"""

from sqlglot import exp
from sqlglot.errors import OptimizeError

from app.engine.validation.catalog import Catalog
from app.engine.validation.org_sources import Kind, OrgSources
from app.engine.validation.policy import ValidationPolicy
from app.engine.validation.problems import Problem


def _plain(node: exp.Expression) -> exp.Expression:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _string(node: exp.Expression) -> bool:
    return isinstance(node, exp.Literal) and node.is_string


def org_match_not_exact(
    statement: exp.Expression, catalog: Catalog, policy: ValidationPolicy
) -> list[Problem]:
    if not policy.org_columns:
        return []
    problems: list[Problem] = []
    seen: set[tuple[int, str]] = set()

    def report(node, kind):
        code = "ORG_MATCH_SOURCE_UNRESOLVED" if kind == Kind.UNKNOWN else "ORG_MATCH_NOT_EXACT"
        key = (id(node), code)
        if key not in seen:
            seen.add(key)
            problems.append(
                Problem(
                    code=code,
                    rule="R-ORG-001",
                    message=(
                        "机构匹配来源无法唯一确认，请改用来源明确的原始机构列。"
                        if kind == Kind.UNKNOWN
                        else "机构匹配只支持原始列与字符串 EQ/IN 或直接列等值关联，不允许模糊、否定或变形匹配。"
                    ),
                )
            )

    try:
        resolver = OrgSources(statement, catalog, policy)
        for scope in resolver.scopes:
            resolver.relation(scope)

            def check(node, *, negated=False, output=False, scope=scope):
                node = _plain(node)
                if isinstance(node, (exp.And, exp.Or)):
                    check(node.this, negated=negated, output=output)
                    check(node.expression, negated=negated, output=output)
                    return
                if isinstance(node, exp.Not):
                    check(node.this, negated=True, output=output)
                    return
                kind = resolver.kind(node, scope, output=output)
                if kind == Kind.CLEAN:
                    return
                if kind == Kind.UNKNOWN:
                    report(node, kind)
                    return
                if not negated and isinstance(node, exp.EQ):
                    a, b = _plain(node.this), _plain(node.expression)
                    ak, bk = resolver.kind(a, scope, output=output), resolver.kind(b, scope, output=output)
                    if all(k in {Kind.CLEAN, Kind.EXACT} for k in (ak, bk)) and (
                        (isinstance(a, exp.Column) and (_string(b) or isinstance(b, exp.Column)))
                        or (isinstance(b, exp.Column) and _string(a))
                    ):
                        return
                if not negated and isinstance(node, exp.In):
                    left = _plain(node.this)
                    if (
                        isinstance(left, exp.Column)
                        and resolver.kind(left, scope, output=output) == Kind.EXACT
                        and not node.args.get("query")
                        and not node.args.get("unnest")
                        and node.expressions
                        and all(_string(item) for item in node.expressions)
                    ):
                        return
                report(node, kind)

            for node in scope.walk():
                # 只在本查询的 HAVING/QUALIFY 中解析输出别名，不能穿过子查询或 FILTER WHERE。
                context = node.find_ancestor(exp.Where, exp.Join, exp.Having, exp.Qualify, exp.Query)
                output = isinstance(context, (exp.Having, exp.Qualify))
                if isinstance(node, (exp.Where, exp.Having, exp.Qualify)):
                    check(node.this, output=isinstance(node, (exp.Having, exp.Qualify)))
                elif isinstance(node, exp.Join) and node.args.get("on") is not None:
                    check(node.args["on"])
                elif isinstance(node, exp.If):
                    parent = node.parent
                    if isinstance(parent, exp.Case) and parent.this is not None:
                        # 简单 CASE 的隐式匹配同样按直接 EQ 验证；不修改原 AST。
                        check(exp.EQ(this=parent.this.copy(), expression=node.this.copy()))
                    else:
                        check(node.this, output=output)
                elif isinstance(node, exp.Predicate):
                    check(node, output=output)
    except OptimizeError:
        # 非法重复别名等无法构造作用域时，仅涉及独立保护表才补机构来源错误。
        protected_tables = {key.split(".")[1] for key, cols in policy.org_columns.items() if cols}
        if any(table.name.lower() in protected_tables for table in statement.find_all(exp.Table)):
            report(statement, Kind.UNKNOWN)
    return problems
