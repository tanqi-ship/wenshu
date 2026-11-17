"""校验策略。

所有银行特有的约定都放在这里，而不是散在代码里硬编码。
换一家银行只需要换一份 :class:`ValidationPolicy`，校验器本身不动。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SourceMode = Literal["plain", "report", "union"]


@dataclass(frozen=True, slots=True)
class Scope:
    """本次提问允许使用的表。

    来自检索阶段实际返回给模型的表 —— 不是"整个数据源"。
    模型只允许用检索到的东西，由 ``tables_out_of_scope`` 强制。
    """

    tables: frozenset[str] = frozenset()

    @classmethod
    def of(cls, names: list[str] | tuple[str, ...] | set[str]) -> Scope:
        return cls(tables=frozenset(name.lower() for name in names if name))

    def allows(self, table: str, schema: str | None = None) -> bool:
        lowered = table.lower()
        if lowered in self.tables:
            return True
        if schema:
            return f"{schema.lower()}.{lowered}" in self.tables
        return False

    def __bool__(self) -> bool:
        return bool(self.tables)


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    """校验规则的可配置项。"""

    #: 时点快照列名。时点对比规则 R-COMP-001 依赖它。
    snapshot_column: str = "etl_dt"

    #: 名称类字段的后缀。这些字段禁止出现在 GROUP BY 里（规则 R-NAME-001）。
    name_field_suffixes: tuple[str, ...] = ("_nm", "_name")

    #: ETL 明细分支最多用几张表（规则 R-TBL-001）。
    max_tables_plain: int = 2

    #: 报表/UNION 分支禁止 JOIN（规则 R-JOIN-001）。
    forbid_join_modes: frozenset[str] = frozenset({"report", "union"})

    #: 读查询里禁止出现的函数。默认值是"能读文件 / 能连外网 / 能让数据库睡着"的那类。
    forbidden_functions: frozenset[str] = frozenset(
        {
            # PostgreSQL
            "pg_read_file",
            "pg_read_binary_file",
            "pg_ls_dir",
            "pg_sleep",
            "pg_sleep_for",
            "pg_sleep_until",
            "dblink",
            "dblink_exec",
            "lo_import",
            "lo_export",
            "pg_terminate_backend",
            "pg_cancel_backend",
            "set_config",
            # MySQL
            "load_file",
            "sleep",
            "benchmark",
            "get_lock",
            # Oracle
            "utl_http",
            "utl_file",
            "utl_tcp",
            "utl_smtp",
            "dbms_lock",
            "dbms_pipe",
            "dbms_scheduler",
        }
    )

    #: 序列的隐式写操作。读查询里出现即拒绝。
    forbidden_sequence_functions: frozenset[str] = frozenset(
        {"nextval", "setval", "currval", "next_value_for"}
    )

    #: 一次最多报几个问题。太多会把模型淹掉，也会把反馈文本撑爆。
    max_problems: int = 8
