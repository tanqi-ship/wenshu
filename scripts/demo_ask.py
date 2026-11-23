"""临时桩：跑通「问题 -> Intent -> SQL -> 校验」的完整体验。

⚠️ **这不是 L1，不含任何模型调用。**

    「问题 → Intent」这一步在真实架构里属于 L1（M3 才实现，需要 LLM）。
    这里用一张**固定问题表**代替，只为让你现在就能看到整条链路长什么样、
    以及每一层分别做了什么。

用法：

    python -m scripts.demo_ask                      # 跑全部内置问题
    python -m scripts.demo_ask "截至2026年4月末不良贷款余额"
    python -m scripts.demo_ask --scope B001        # 带行级权限
    python -m scripts.demo_ask --bad               # 跑一组必然失败的例子

语义层来自 ``app/engine/semantic/bank_mini.py``（演示与测试共用的那份假数据）。
它以前是手写在本文件里的，后来抽出去统一了：手写副本必然漂移，
而黄金集是第四个消费者。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from app.engine.compiler import CompileError, compile_sql, month_end_range
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.bank_mini import TABLE, semantic_catalog, validation_catalog
from app.engine.semantic.models import Intent
from app.engine.validation import validate_sql

if TYPE_CHECKING:
    # 只用于类型注解：这两个类型在运行时不需要，沿用了本仓库
    # ``validation/__init__.py`` / ``problems.py`` 的写法，避免多拉一次导入。
    from app.engine.semantic.catalog import SemanticCatalog
    from app.engine.validation.catalog import Catalog


#: 固定问题表 —— 代替 M3 的「问题 → Intent」。
QUESTIONS: dict[str, Intent] = {
    "截至2026年4月末不良贷款余额": Intent(
        metric="不良贷款余额",
        time_range=month_end_range(2026, 4),
    ),
    "2026年4月末各机构的不良贷款余额": Intent(
        metric="不良贷款余额",
        time_range=month_end_range(2026, 4),
        dimensions=("机构",),
    ),
    "2026年4月末各机构各产品的贷款余额": Intent(
        metric="贷款余额",
        time_range=month_end_range(2026, 4),
        dimensions=("机构", "产品"),
    ),
    "2026年上半年不良贷款率": Intent(
        metric="不良贷款率",
        time_range=month_end_range(2026, 6),
    ),
}

#: 必然失败的例子 —— 用来演示错误分类。
BAD_CASES: dict[str, Intent] = {
    "贷款迁徙率（语义层没有这个指标）": Intent(
        metric="贷款迁徙率",
        time_range=month_end_range(2026, 4),
    ),
    "没给时间": Intent(metric="不良贷款余额"),
    "维度不存在": Intent(
        metric="不良贷款余额",
        time_range=month_end_range(2026, 4),
        dimensions=("客户经理",),
    ),
}


def run_one(
    question: str,
    intent: Intent,
    semantic: SemanticCatalog,
    catalog: Catalog,
    *,
    scope: Scope | None = None,
) -> bool:
    print("=" * 78)
    print(f"问题：{question}")
    print(
        f"Intent：metric={intent.metric!r} time={intent.time_range.grain if intent.time_range else None} "
        f"dims={list(intent.dimensions)}"
    )
    print("=" * 78)

    try:
        sql = compile_sql(intent, semantic, scope=scope)
    except CompileError as exc:
        # 编译期拦截 = 在花钱之前就拒绝，且原因精确到"谁该修"
        print(f"  ✗ 编译期拦截  stage={exc.stage}  code={exc.code}")
        print(f"    {exc.message}")
        print()
        return False

    print("--- 编译产物（0 次模型调用）---")
    print(sql)

    result = validate_sql(sql, catalog=catalog, scope=scope)
    print("--- L4 校验 ---")
    if result.ok:
        print("  ✓ 通过（适用业务规则 + 只读 + 名字 + 范围）")
    else:
        print("  ✗ 未通过：")
        for problem in result.problems:
            print("   ", problem.render())
    print()
    return result.ok


def main(argv: list[str]) -> int:
    semantic, catalog = semantic_catalog(), validation_catalog()

    scope: Scope | None = None
    if "--scope" in argv:
        index = argv.index("--scope")
        value = argv[index + 1] if index + 1 < len(argv) else ""
        if value:
            scope = Scope.of(
                [TABLE],
                row_policies=(RowPolicy(table=TABLE, column="org_no", values=(value,), operator="eq"),),
            )
            print(f"[行级权限] 只能看 org_no = {value}\n")

    if "--bad" in argv:
        for question, intent in BAD_CASES.items():
            run_one(question, intent, semantic, catalog, scope=scope)
        return 0

    wanted = [item for item in argv[1:] if not item.startswith("--")]
    if wanted:
        question = wanted[0]
        intent = QUESTIONS.get(question)
        if intent is None:
            print(f"内置问题表里没有「{question}」。可选：")
            for key in QUESTIONS:
                print(f"  - {key}")
            return 1
        return 0 if run_one(question, intent, semantic, catalog, scope=scope) else 1

    all_ok = True
    for question, intent in QUESTIONS.items():
        all_ok &= run_one(question, intent, semantic, catalog, scope=scope)
    print("=" * 78)
    print("全部通过" if all_ok else "有失败项")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
