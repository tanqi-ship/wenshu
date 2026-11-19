"""校验评测集的结构。

评测集是项目的核心资产，所以它自己也要被检查：

* 每条必须有 ``id`` / ``question`` / ``why_hard``
* ``id`` 必须唯一
* ``why_hard`` 不能少于 20 字 —— 写不出"为什么容易错"，这条用例就测不出东西
* ``covers`` 里的编号必须能在已知清单里找到
* 有 ``standard_sql`` 的用例必须有 ``expected.tables``

用法::

    python -m scripts.validate_cases eval/cases
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

MIN_WHY_HARD_CHARS = 20

#: fixture 里声明的缺陷编号（见 fixtures/sql/bank_fixture.sql 末尾）。
KNOWN_DEFECT_CODES = {f"D-{index:02d}" for index in range(1, 12)}

#: 业务规则编号（见 app/engine/validation/rules.py）。
KNOWN_RULE_CODES = {"C-03", "C-04", "C-05", "C-06", "C-07", "C-09", "C-11", "C-12"}

REQUIRED_KEYS = ("id", "question", "why_hard")


def _load_yaml(path: Path) -> Any:
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise SystemExit("缺少 PyYAML。请先 `uv sync` 或 `pip install PyYAML`。") from exc
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate_case(case: dict[str, Any], *, seen: set[str]) -> list[str]:
    problems: list[str] = []
    case_id = str(case.get("id") or "")

    for key in REQUIRED_KEYS:
        if not case.get(key):
            problems.append(f"[{case_id or '?'}] 缺少必填字段 {key}")

    if case_id in seen:
        problems.append(f"[{case_id}] id 重复")
    else:
        seen.add(case_id)

    why_hard = str(case.get("why_hard") or "").strip()
    if why_hard and len(why_hard) < MIN_WHY_HARD_CHARS:
        problems.append(
            f"[{case_id}] why_hard 太短（{len(why_hard)} 字，至少 {MIN_WHY_HARD_CHARS} 字）。"
            "写不出为什么容易错，说明这条用例测不出东西。"
        )

    for code in case.get("covers") or []:
        code = str(code)
        if code.startswith("D-") and code not in KNOWN_DEFECT_CODES:
            problems.append(f"[{case_id}] covers 引用了未知缺陷编号 {code}")
        elif code.startswith("C-") and code not in KNOWN_RULE_CODES:
            problems.append(f"[{case_id}] covers 引用了未知规则编号 {code}")

    has_sql = bool(str(case.get("standard_sql") or "").strip())
    expected = case.get("expected") or {}
    if has_sql and not expected.get("tables"):
        problems.append(f"[{case_id}] 有 standard_sql 但没有 expected.tables")
    if not has_sql and not (expected.get("behavior") or expected.get("result")):
        problems.append(f"[{case_id}] 没有 standard_sql 时，expected 必须给出 behavior 或 result")

    return problems


def validate_directory(directory: Path) -> list[str]:
    problems: list[str] = []
    files = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
    if not files:
        return [f"{directory} 下没有 yaml 用例文件"]

    seen: set[str] = set()
    total = 0
    for path in files:
        payload = _load_yaml(path)
        cases = payload if isinstance(payload, list) else payload.get("cases") or []
        if not isinstance(cases, list):
            problems.append(f"{path.name}: 顶层结构必须是列表或含 cases 列表")
            continue
        for case in cases:
            if not isinstance(case, dict):
                problems.append(f"{path.name}: 用例项不是映射")
                continue
            total += 1
            problems.extend(f"{path.name}: {p}" for p in validate_case(case, seen=seen))

    print(f"检查了 {len(files)} 个文件、{total} 条用例。")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验评测集结构")
    parser.add_argument("directory", nargs="?", default="eval/cases")
    args = parser.parse_args(argv)

    problems = validate_directory(Path(args.directory))
    if problems:
        print(f"\n发现 {len(problems)} 个问题：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print("全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
