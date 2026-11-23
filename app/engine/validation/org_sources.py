"""R-ORG-001 的局部来源追踪：只传递保护状态，不重建业务语义。

按 sqlglot 查询作用域解析，独立 Catalog 确定物理身份。四态区分精确机构、
已变换、无法确认及普通值。未知保护来源不能退化成普通值；不修改输入 AST。
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, auto
from itertools import zip_longest

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.engine.validation.catalog import Catalog
from app.engine.validation.policy import ValidationPolicy


class Kind(Enum):
    CLEAN = auto()
    EXACT = auto()
    TRANSFORMED = auto()
    UNKNOWN = auto()


def transformed(kinds: Iterable[Kind]) -> Kind:
    kinds = set(kinds)
    if Kind.UNKNOWN in kinds:
        return Kind.UNKNOWN
    return Kind.TRANSFORMED if kinds - {Kind.CLEAN} else Kind.CLEAN


@dataclass(frozen=True, slots=True)
class Relation:
    columns: tuple[tuple[str, Kind], ...] = ()
    fallback: Kind = Kind.CLEAN
    positional: bool = True

    @property
    def protected(self) -> bool:
        return self.fallback != Kind.CLEAN or any(kind != Kind.CLEAN for _, kind in self.columns)

    def lookup(self, name: str) -> Kind | None:
        matches = [kind for column, kind in self.columns if column == name.lower()]
        if len(matches) > 1:
            return Kind.UNKNOWN if any(kind != Kind.CLEAN for kind in matches) else Kind.CLEAN
        return matches[0] if matches else None


class OrgSources:
    def __init__(self, statement: exp.Expression, catalog: Catalog, policy: ValidationPolicy):
        self.catalog = catalog
        self.policy = policy
        self.scopes = traverse_scope(statement)
        self.by_expression = {id(scope.expression): scope for scope in self.scopes}
        self.outputs: dict[Scope, Relation] = {}
        self.inputs: dict[Scope, dict[str, Relation]] = {}
        self.active: set[Scope] = set()

    def physical(self, table: exp.Table) -> Relation:
        identity = self.catalog.resolve(table.name, table.db or None)
        if identity is None:
            possible = any(
                key.split(".")[1] == table.name.lower()
                and (not table.db or key.split(".")[0] == table.db.lower())
                and columns
                for key, columns in self.policy.org_columns.items()
            )
            return Relation(fallback=Kind.UNKNOWN if possible else Kind.CLEAN, positional=False)
        protected = self.policy.org_columns.get(".".join(identity), frozenset())
        columns = (self.catalog.columns(identity[1], identity[0]) or set()) | protected
        result = Relation(
            tuple((col, Kind.EXACT if col in protected else Kind.CLEAN) for col in sorted(columns)),
            Kind.UNKNOWN if protected else Kind.CLEAN,
            False,  # Catalog 的列集合不能证明物理列顺序。
        )
        alias = table.args.get("alias")
        if alias is not None and alias.columns:
            kind = Kind.UNKNOWN if result.protected else Kind.CLEAN
            return Relation(tuple((col.name.lower(), kind) for col in alias.columns), kind, False)
        return result

    def sources(self, scope: Scope) -> dict[str, Relation]:
        if scope not in self.inputs:
            sources: dict[str, Relation] = {}
            for name, (node, source) in scope.selected_sources.items():
                relation = self.relation(source) if isinstance(source, Scope) else self.physical(source)
                if isinstance(source, Scope) and isinstance(node, exp.Table) and not source.outer_columns:
                    # sqlglot 为递归自引用提供一个锚点 Scope；其列别名需从 CTE 声明恢复。
                    cte = source.expression.find_ancestor(exp.CTE)
                    if cte is not None and cte.alias_or_name.lower() == node.name.lower():
                        if cte.alias_column_names:
                            relation = self._rename(relation, cte.alias_column_names)
                        if cte.parent.args.get("recursive") and relation.protected:
                            relation = Relation(
                                tuple((col, Kind.UNKNOWN) for col, _ in relation.columns), Kind.UNKNOWN
                            )
                alias = node.args.get("alias")
                if isinstance(source, Scope) and isinstance(node, exp.Table) and alias and alias.columns:
                    relation = self._rename(relation, [col.name for col in alias.columns])
                key = name.lower()
                if key in sources:
                    # 未加引号的大小写别名不能靠后一次字典覆盖洗掉保护。
                    old = sources[key]
                    kind = Kind.UNKNOWN if old.protected or relation.protected else Kind.CLEAN
                    relation = Relation(
                        tuple((col, kind) for col, _ in (*old.columns, *relation.columns)), kind
                    )
                sources[key] = relation
            self.inputs[scope] = sources
        return self.inputs[scope]

    def relation(self, scope: Scope) -> Relation:
        if scope in self.outputs:
            return self.outputs[scope]
        if scope in self.active:
            return Relation(fallback=Kind.UNKNOWN)
        self.active.add(scope)
        try:
            if isinstance(scope.expression, exp.SetOperation):
                result = self._union(scope)
            elif isinstance(scope.expression, exp.Select):
                result = self._select(scope)
            else:
                # 不扩展 LATERAL/VALUES/表值函数语义；只追踪实际参数是否涉及保护值。
                # 相关机构参数的输出不能因为不是 SELECT 而被当成普通列。
                kind = transformed(self.kind(child, scope) for child in scope.expression.iter_expressions())
                result = Relation(fallback=Kind.UNKNOWN if kind != Kind.CLEAN else Kind.CLEAN)
            cte = scope.expression.parent
            recursive = isinstance(cte, exp.CTE) and bool(cte.parent and cte.parent.args.get("recursive"))
            if recursive and result.protected:
                result = Relation(tuple((name, Kind.UNKNOWN) for name, _ in result.columns), Kind.UNKNOWN)
            if scope.outer_columns:
                result = self._rename(result, scope.outer_columns)
            self.outputs[scope] = result
            return result
        finally:
            self.active.remove(scope)

    @staticmethod
    def _rename(result: Relation, names: list[str]) -> Relation:
        bad_order = not result.positional or len(names) > len(result.columns)
        renamed = [
            (
                names[i].lower() if i < len(names) else name,
                Kind.UNKNOWN if bad_order and result.protected else kind,
            )
            for i, (name, kind) in enumerate(result.columns)
        ]
        renamed.extend(
            (name.lower(), Kind.UNKNOWN if result.protected else Kind.CLEAN)
            for name in names[len(result.columns) :]
        )
        return Relation(
            tuple(renamed),
            Kind.UNKNOWN if bad_order and result.protected else result.fallback,
            result.positional,
        )

    def _select(self, scope: Scope) -> Relation:
        sources = self.sources(scope)
        columns: list[tuple[str, Kind]] = []
        positional = True
        fallback = Kind.CLEAN
        for projection in scope.expression.expressions:
            if projection.is_star:
                star = projection.this if isinstance(projection, exp.Column) else projection
                chosen = (
                    [sources[projection.table.lower()]]
                    if isinstance(projection, exp.Column) and projection.table.lower() in sources
                    else list(sources.values())
                )
                complex_star = any(star.args.values())
                for source in chosen:
                    columns.extend(
                        (name, Kind.UNKNOWN if complex_star and kind != Kind.CLEAN else kind)
                        for name, kind in source.columns
                    )
                    if source.protected:
                        fallback = Kind.UNKNOWN
                    positional &= source.positional and not complex_star
            else:
                columns.append((projection.alias_or_name.lower(), self.kind(projection, scope)))
        return Relation(tuple(columns), fallback, positional)

    def _union(self, scope: Scope) -> Relation:
        branches = [self.relation(child) for child in scope.union_scopes]
        if len(branches) != 2:
            return Relation(fallback=Kind.UNKNOWN)
        left, right = branches
        columns = []
        bad_order = not left.positional or not right.positional or len(left.columns) != len(right.columns)
        for a, b in zip_longest(left.columns, right.columns, fillvalue=("", Kind.CLEAN)):
            kinds = {a[1], b[1]}
            if (bad_order and (left.protected or right.protected)) or Kind.UNKNOWN in kinds:
                kind = Kind.UNKNOWN
            elif kinds == {Kind.EXACT}:
                kind = Kind.EXACT
            else:
                kind = transformed(kinds)
            columns.append((a[0] or b[0], kind))
        return Relation(
            tuple(columns),
            Kind.UNKNOWN if left.fallback != Kind.CLEAN or right.fallback != Kind.CLEAN else Kind.CLEAN,
            not bad_order,
        )

    def column(self, column: exp.Column, scope: Scope, *, output: bool = False) -> Kind:
        name = column.name.lower()
        if output and not column.table:
            match = self.relation(scope).lookup(name)
            if match is not None:
                return match
        sources = self.sources(scope)
        if column.table and column.table.lower() in sources:
            source = sources[column.table.lower()]
            match = source.lookup(name)
            return match if match is not None else source.fallback
        if not column.table:
            matches = [match for source in sources.values() if (match := source.lookup(name)) is not None]
            if len(matches) == 1:
                return matches[0]
            if matches:
                return Kind.UNKNOWN if any(kind != Kind.CLEAN for kind in matches) else Kind.CLEAN
        # 相关作用域不视为已确认的直接机构值，也不能因不在本层而洗掉保护。
        parent = scope.parent
        while parent is not None:
            if parent not in self.active:
                match = self.column(column, parent)
                if match != Kind.CLEAN:
                    return Kind.UNKNOWN
            parent = parent.parent
        return Kind.UNKNOWN if any(source.protected for source in sources.values()) else Kind.CLEAN

    def kind(self, node: exp.Expression, scope: Scope, *, output: bool = False) -> Kind:
        if isinstance(node, (exp.Alias, exp.Paren)):
            return self.kind(node.this, scope, output=output)
        if isinstance(node, exp.Column):
            return self.column(node, scope, output=output)
        if isinstance(node, (exp.Window, exp.Filter)):
            # 窗口分区/排序与聚合 FILTER 条件是控制项；条件由规则另查，不作输出标识符来源。
            return self.kind(node.this, scope, output=output)
        if isinstance(node, (exp.Count, exp.Sum, exp.Avg)):
            return Kind.CLEAN  # 数值统计；内部条件由 org.py 单独扫描。
        if isinstance(node, (exp.Subquery, exp.Query)):
            query = node.this if isinstance(node, exp.Subquery) else node
            child = self.by_expression.get(id(query))
            if child is None:
                return Kind.UNKNOWN
            relation = self.relation(child)
            return transformed([relation.fallback, *(kind for _, kind in relation.columns)])
        return transformed(self.kind(child, scope, output=output) for child in node.iter_expressions())
