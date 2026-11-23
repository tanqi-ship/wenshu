"""校验策略。

所有银行特有的约定都放在这里，而不是散在代码里硬编码。
换一家银行只需要换一份 :class:`ValidationPolicy`，校验器本身不动。

:class:`Scope` 已经上移到 :mod:`app.engine.scope` —— 编译器和校验器都要用它，
放在校验包里会让依赖方向变成"编译器依赖校验器"，而 ADR-0005 要求两者独立。
这里保留 re-export，现有 import 不受影响。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from app.engine.scope import RowPolicy, Scope

SourceMode = Literal["plain", "report", "union"]

__all__ = ["RowPolicy", "Scope", "SourceMode", "ValidationPolicy"]


def _default_org_columns() -> Mapping[str, frozenset[str]]:
    return {
        "public.qa_loan_acc_detail": frozenset(
            {
                "org_no",
                "org_nm",
                "accounting_org_no",
                "higher_level_org_no",
                "higher_level_org_nm",
                "belong_to_org_no",
                "belong_to_org_nm",
            }
        ),
        "public.dim_org": frozenset({"org_no", "org_nm", "parent_org_no"}),
    }


_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


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

    #: 独立的 schema.table -> 机构保护列；不从业务意图或语义目录生成。
    org_columns: Mapping[str, frozenset[str]] = field(default_factory=_default_org_columns)

    def __post_init__(self) -> None:
        if not isinstance(self.org_columns, Mapping):
            raise ValueError("org_columns 必须是全限定表名到列集合的映射。")
        copied: dict[str, frozenset[str]] = {}
        for table, columns in self.org_columns.items():
            if (
                not isinstance(table, str)
                or len(table.split(".")) != 2
                or not all(_IDENTIFIER.fullmatch(part) for part in table.split("."))
                or not isinstance(columns, (set, frozenset, tuple, list))
                or not all(isinstance(col, str) and _IDENTIFIER.fullmatch(col) for col in columns)
            ):
                raise ValueError("org_columns 仅接受合法 schema.table 及列标识符集合。")
            key = table.lower()
            copied[key] = copied.get(key, frozenset()) | frozenset(col.lower() for col in columns)
        object.__setattr__(self, "org_columns", MappingProxyType(copied))
