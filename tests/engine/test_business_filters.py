"""业务过滤（``Intent.filters`` → WHERE）的测试。

为什么需要这个文件：``filters`` 在此之前的整个测试套件里**一次都没被用过**，
而它是"用户能看见、能改"的那一半 WHERE（另一半是权限，见 ADR-0004）。
没有测试的行为等于没有约定。

这里钉住四件事：

1. **码值翻译**：用户说「渝北支行」，SQL 里必须是 ``'B001'``
2. **fail-closed**：语义层登了码值表，却没这个值 → 报 ``UNKNOWN_CODE``，
   绝不原样透传（透传 = 悄悄查出一片 0，而且不报错）
3. **幂等**：传进来的已经是存储值 ``'B001'`` 时，不能再翻一次
4. **顺序**：WHERE 里必须是 时间 → 业务 → 权限 的固定顺序
"""

from __future__ import annotations

import pytest
from sqlglot import exp

from app.engine.compiler import CompileError, compile_intent, month_end_range
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.bank_mini import TABLE, semantic_catalog
from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.dimension import DimensionDef, LevelDef
from app.engine.semantic.models import Filter, Intent, MetricDef


def intent_with_org(value: object, operator: str = "eq") -> Intent:
    return Intent(
        metric="不良贷款余额",
        time_range=month_end_range(2026, 4),
        filters=(Filter(dimension="机构", value=value, operator=operator),),  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# 1. 码值翻译
# ---------------------------------------------------------------------------


def test_registered_display_value_is_translated_to_stored_value() -> None:
    """「渝北支行」→ ``org_no = 'B001'``。

    fixture 里渝北支行的机构号就是 B001（见 fixtures/sql/bank_fixture.sql），
    所以说人话和查数据之间的这一步翻译**必须发生**，否则查出来是 0 行。
    """
    sql = compile_intent(intent_with_org("渝北支行"), semantic_catalog()).sql(dialect="sqlite")
    assert "t1.org_no = 'B001'" in sql
    assert "渝北支行" not in sql


def test_stored_value_passes_through_idempotently() -> None:
    """传进来的已经是 ``'B001'`` 时不能再翻一次（ADR-0002：编码必须精确等值）。"""
    sql = compile_intent(intent_with_org("B001"), semantic_catalog()).sql(dialect="sqlite")
    assert "t1.org_no = 'B001'" in sql


def test_in_operator_translates_each_value() -> None:
    sql = compile_intent(
        intent_with_org(["渝北支行", "两江分行"], operator="in"),
        semantic_catalog(),
    ).sql(dialect="sqlite")
    assert "t1.org_no IN ('B001', 'O003')" in sql


# ---------------------------------------------------------------------------
# 2. fail-closed
# ---------------------------------------------------------------------------


def test_unknown_display_value_raises_instead_of_passing_through() -> None:
    """语义层登了码值表 → 不认识的值必须报错，不能原样进 SQL。

    原样透传的后果是 ``org_no = '某某支行'`` 永远匹配不到行，
    结果是 0 而不是错误 —— 用户会以为"这个机构没有不良贷款"。
    """
    with pytest.raises(CompileError) as excinfo:
        compile_intent(intent_with_org("某某支行"), semantic_catalog())

    assert excinfo.value.code == "UNKNOWN_CODE"
    assert excinfo.value.stage == "symbol_table"  # 语义层没覆盖，该由配置维护者修


def test_no_code_map_means_passthrough() -> None:
    """维度**没有**配码值表时，值是原样透传的。

    这是刻意的取舍：契约上 ``Filter.value`` 就该是数据库存储值
    （见 ``semantic/models.py`` 的 :class:`Filter`），不再有第二个翻译层。
    代价是"没配码值表就没人验证"——所以真正的语义层必须把码值配全。
    """
    catalog = SemanticCatalog.build(
        metrics=[
            MetricDef(
                code="npl_balance",
                name="不良贷款余额",
                formula="SUM({tbl}.current_balnc_cny)",
                source_tables=(TABLE,),
            )
        ],
        dimensions=[
            DimensionDef(
                code="org",
                name="机构",
                levels={"financial": LevelDef(group_by="org_no", display="org_nm")},
                default_level="financial",
            )
        ],
        # code_map 故意留空
    )
    sql = compile_intent(intent_with_org("渝北支行"), catalog).sql(dialect="sqlite")
    assert "t1.org_no = '渝北支行'" in sql


# ---------------------------------------------------------------------------
# 3. 顺序：时间 → 业务 → 权限（ADR-0004）
# ---------------------------------------------------------------------------


def _where_operands(sql: str) -> list[str]:
    tree = exp.maybe_parse(sql, dialect="sqlite")
    where = tree.args["where"].this

    def flatten(node: exp.Expression) -> list[str]:
        if isinstance(node, exp.And):
            return [*flatten(node.this), *flatten(node.expression)]
        return [node.sql(dialect="sqlite")]

    return flatten(where)


def test_where_order_is_time_business_policy() -> None:
    """三段 WHERE 的顺序固定，权限永远在最后一段。

    顺序固定是为了让"哪一段是权限"有唯一答案：改条件重跑时只能动前面几段，
    最后一段必须原样保留（askdb 曾在这里泄漏过权限，见 ADR-0004）。
    """
    scope = Scope.of(
        [TABLE],
        row_policies=(RowPolicy(table=TABLE, column="org_no", values=("B001",), operator="eq"),),
    )
    sql = compile_intent(intent_with_org("两江分行"), semantic_catalog(), scope=scope).sql(dialect="sqlite")

    operands = _where_operands(sql)
    assert len(operands) == 3, operands
    assert operands[0] == "t1.etl_dt = '2026-04-30'"  # 时间
    assert operands[1] == "t1.org_no = 'O003'"  # 业务（两江分行）
    assert operands[2] == "t1.org_no = 'B001'"  # 权限
