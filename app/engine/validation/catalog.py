"""校验用的表结构目录。

只暴露名字层面的信息（表在哪个 schema、有哪些列），因为校验器只关心
"这个名字存在吗"。不关心类型、注释、码值 —— 那些属于语义层。

为什么单独抽出来：让校验器可以脱离数据库、脱离应用库单测。
测试里用 :meth:`Catalog.from_mapping` 直接构造。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


@dataclass(slots=True)
class Catalog:
    """``schema -> table -> {column, ...}`` 的三层索引。

    刻意保留 schema 这一层：同名表出现在不同 schema 时，一律要求 SQL
    写出 schema 限定名，而不是靠数据库的 search_path 猜。
    """

    #: schema 名 -> {表名小写 -> 列名集合（小写）}
    schemas: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    #: 无 schema 限定时使用的默认 schema
    default_schema: str = ""

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Mapping[str, Iterable[str]]]) -> Catalog:
        """从 ``{schema: {table: [columns]}}`` 构造。测试用。"""
        schemas: dict[str, dict[str, set[str]]] = {}
        for schema, tables in mapping.items():
            schemas[schema.lower()] = {
                table.lower(): {column.lower() for column in columns}
                for table, columns in tables.items()
            }
        default = next(iter(schemas), "")
        return cls(schemas=schemas, default_schema=default)

    # -- 查询 --------------------------------------------------------------
    def table_exists(self, table: str, schema: str | None = None) -> bool:
        owners = self.schemas_with(table, schema)
        return bool(owners)

    def schemas_with(self, table: str, schema: str | None = None) -> list[str]:
        """包含 *table* 的 schema 列表。给了 *schema* 就只看它。"""
        target = table.lower()
        wanted = (schema or "").lower()
        return [
            name
            for name, tables in self.schemas.items()
            if (not wanted or name == wanted) and target in tables
        ]

    def columns(self, table: str, schema: str | None = None) -> set[str] | None:
        """表的列名集合。表不存在返回 ``None``。

        未限定 schema 时，取所有同名表的列并集 —— 因为名字到底解析到哪张表
        由数据库的 search_path 决定，校验器不去猜。真要区分时由
        "必须写 schema 限定名" 这条规则来要求。
        """
        owners = self.schemas_with(table, schema)
        if not owners:
            return None
        merged: set[str] = set()
        target = table.lower()
        for owner in owners:
            merged |= self.schemas[owner][target]
        return merged

    def all_tables(self) -> set[tuple[str, str]]:
        return {
            (schema, table)
            for schema, tables in self.schemas.items()
            for table in tables
        }

    def resolve(self, table: str, schema: str | None) -> tuple[str, str] | None:
        """把 (schema?, table) 解析成唯一的 (schema, table)。

        解析不出唯一结果时返回 ``None``：要么表不存在，要么同名表在多个
        schema 里而调用方没写限定名。
        """
        owners = self.schemas_with(table, schema)
        if len(owners) != 1:
            return None
        return owners[0], table.lower()
