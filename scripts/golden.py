"""黄金集比对：把「引擎产出的 SQL」与「手写的黄金 SQL」比成一句话。

这个模块只做两件事，**没有第三件**：

1. :func:`canonical_sql` —— 归一化：解析 → 去注释 → 未加引号的标识符小写 → 单行渲染
2. :func:`compare` —— 比较，并把差异渲染成能读的 unified diff

刻意**不做** ``--update`` / ``--bless``（一键用引擎产出覆盖黄金 SQL）。
理由：黄金 SQL 一旦能被引擎刷新，这个门禁就只能证明"引擎没变"，
不能证明"引擎算对了"。参见 ``eval/golden/AQ-001.sql`` 开头那段注释。

为什么比"归一化后的文本"而不是直接比 AST：

* sqlglot 的 ``Expression.__eq__`` 是 ``type 相同 and hash 相同``（实测），
  它对标识符大小写敏感 —— ``SUM(X) FROM T`` 与 ``sum(x) from t`` 判为不等，
  这会把无害的大小写差异报成失败。
* 文本比对还能直接出 diff，人能看懂哪一处变了；``==`` 只给一个 False。

归一化做三件事，每一件都有具体来由（都是实测过的）：

===============  ============================================================
注释             黄金 SQL 头部的 ``--`` 注释会被 sqlglot 挂到节点上、
                 渲染成 ``/* ... */`` 前缀，把比对搞脏 → 清空 ``node.comments``
大小写            未加引号的标识符在 SQLite/PostgreSQL 里本来就不区分大小写
                 → 统一小写（**加引号的标识符不动**，那时大小写是有意义的）
排版             空格、换行、结尾分号 → 交给 sqlglot 重新渲染
===============  ============================================================

刻意**不**归一化的东西：字面量的大小写（``'B001'`` ≠ ``'b001'``，业务上有意义）、
``IN`` 列表的顺序、``AND`` 两侧的顺序。这些都属于"形状"，是要被钉住的契约。
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.engine.compiler import CompileError, compile_sql
from app.engine.semantic.catalog import SemanticCatalog
from app.engine.semantic.models import Intent, intent_from_mapping
from app.engine.validation import validate_sql
from app.engine.validation.catalog import Catalog

__all__ = [
    "DEFAULT_DIALECT",
    "CaseOutcome",
    "Comparison",
    "GoldenCase",
    "GoldenConfigError",
    "SqlParseError",
    "canonical_sql",
    "cases_from_payload",
    "compare",
    "evaluate_case",
    "load_cases",
    "load_eval_case_ids",
]

#: 本地 fixture 是 SQLite（ADR-0006）。方言只影响解析与渲染，不影响语义。
DEFAULT_DIALECT = "sqlite"


class SqlParseError(ValueError):
    """SQL 无法解析，或者不是唯一的单条语句。

    单独一个异常类型是有意的：黄金集门禁里，"解析不了"和"对不上"必须分开报 ——
    前者说明黄金文件或引擎产出坏了，后者才是"引擎算错了"。
    """


def _canonical_tree(sql: str, *, dialect: str = DEFAULT_DIALECT) -> exp.Expression:
    """解析 + 归一化，返回一棵干净的树。

    比对用这棵树渲染出的单行文本；给人看的 diff 用同一棵树渲染出的多行文本。
    一次解析、两种渲染 —— 两边口径必然一致。
    """
    try:
        statements = [node for node in sqlglot.parse(sql, dialect=dialect) if node is not None]
    except ParseError as exc:
        raise SqlParseError(f"SQL 无法解析：{exc}") from exc

    if not statements:
        raise SqlParseError("没有解析出任何语句。")
    if len(statements) > 1:
        raise SqlParseError(f"期望 1 条语句，实际解析出 {len(statements)} 条。")

    tree = statements[0]
    for node in tree.walk():
        if getattr(node, "comments", None):
            node.comments = None
        if isinstance(node, exp.Identifier) and not node.quoted:
            node.set("this", node.this.lower())
    return tree


def canonical_sql(sql: str, *, dialect: str = DEFAULT_DIALECT) -> str:
    """把 SQL 归一化成可比对的形式（单行）。"""
    return _canonical_tree(sql, dialect=dialect).sql(dialect=dialect, pretty=False)


@dataclass(frozen=True, slots=True)
class Comparison:
    """一次比对的结果。"""

    #: 两者是否结构等价。
    matches: bool
    #: 归一化后的黄金 SQL（单行）。
    golden: str
    #: 归一化后的引擎产出（单行）。
    produced: str
    #: 逐行差异（unified diff），``matches`` 为真时是空元组。
    diff: tuple[str, ...]

    def render(self) -> str:
        """给人看的一句话结论。"""
        if self.matches:
            return "一致"
        return "\n".join(self.diff)


def compare(
    golden_sql: str,
    produced_sql: str,
    *,
    dialect: str = DEFAULT_DIALECT,
) -> Comparison:
    """比较两条 SQL 是否结构等价，并给出可读差异。

    两侧都解析失败时抛 :class:`SqlParseError`；只有一侧失败时也抛，
    因为"黄金 SQL 解析不了"和"引擎产出解析不了"都是必须先修的硬错误，
    不该混进"对不上"里统计。
    """
    golden_tree = _canonical_tree(golden_sql, dialect=dialect)
    produced_tree = _canonical_tree(produced_sql, dialect=dialect)

    golden = golden_tree.sql(dialect=dialect, pretty=False)
    produced = produced_tree.sql(dialect=dialect, pretty=False)
    matches = golden == produced

    diff: tuple[str, ...] = ()
    if not matches:
        diff = tuple(
            difflib.unified_diff(
                golden_tree.sql(dialect=dialect, pretty=True).splitlines(),
                produced_tree.sql(dialect=dialect, pretty=True).splitlines(),
                fromfile="golden.sql",
                tofile="produced.sql",
                lineterm="",
            )
        )
    return Comparison(matches=matches, golden=golden, produced=produced, diff=diff)


# ---------------------------------------------------------------------------
# 用例：加载与执行
# ---------------------------------------------------------------------------

#: 用例编号格式。``G-`` 前缀是**强制的**：与评测集（``AQ-``）分开编号。
#: 两者曾经撞号：同一个 ``AQ-001`` 在黄金集里是不良贷款余额、在评测集里是贷款余额。
_CASE_ID_PATTERN = re.compile(r"^G-\d{3}$")

#: 规则编号格式：``C-11``（业务规则）/ ``R-NAME-001``（规则码）/ ``D-07``（fixture 缺陷）
_COVER_PATTERN = re.compile(r"^(C-\d{2}|R-[A-Z]+-\d{3}|D-\d{2})$")

_CASE_KEYS = frozenset({"id", "shape", "intent", "golden", "covers", "eval_case", "why_hard"})
_REQUIRED_CASE_KEYS = ("id", "shape", "intent", "golden")


class GoldenConfigError(ValueError):
    """用例配置本身有问题（缺字段、编号重复、黄金文件不存在……）。

    与 :class:`SqlParseError` 分开：这个错是**写用例的人**要修的，
    不是引擎算错了，也不是 SQL 坏了。
    """


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """一条黄金用例。"""

    id: str
    #: 形状标签（如 ``month_end_single_point``），用于看覆盖了哪些形状。
    shape: str
    #: 编译器的输入。
    intent: Intent
    #: 手写黄金 SQL 的路径。
    sql_path: Path
    #: 覆盖的业务规则编号。
    covers: tuple[str, ...] = ()
    #: 对应的评测用例编号（可空）。
    eval_case: str = ""
    #: 为什么容易错。写不出来，说明这条用例测不出东西。
    why_hard: str = ""

    @property
    def golden_sql(self) -> str:
        return self.sql_path.read_text(encoding="utf-8")


def _load_yaml(path: Path) -> object:
    try:
        import yaml
    except ModuleNotFoundError as exc:  # pragma: no cover - 取决于本地环境
        raise GoldenConfigError("缺少 PyYAML，请先 `uv sync --group dev`。") from exc

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GoldenConfigError(f"{path} 不是合法的 YAML：{exc}") from exc


def load_cases(golden_dir: str | Path) -> list[GoldenCase]:
    """读取 ``<golden_dir>/cases.yaml``，返回用例列表。

    只是 IO 外壳；真正的校验在 :func:`cases_from_payload` 里（那个是纯函数，好测）。

    加载阶段就把配置错误全部挑出来（fail-closed）：

    * 缺必需字段 / 有不认识的键
    * ``id`` 重复
    * 指向的 ``golden`` 文件不存在（**加了 .sql 却没写进 cases.yaml 也会被报出来**）
    * ``covers`` 里的编号格式不对
    * 有没有 ``cases.yaml`` 里没登记的孤儿 ``.sql`` 文件
    """
    directory = Path(golden_dir)
    manifest = directory / "cases.yaml"
    if not manifest.exists():
        raise GoldenConfigError(f"找不到用例清单：{manifest}")

    payload = _load_yaml(manifest)
    return cases_from_payload(payload, directory=directory, source=str(manifest))


def cases_from_payload(
    payload: object,
    *,
    directory: str | Path,
    source: str = "cases.yaml",
) -> list[GoldenCase]:
    """把已解析的 YAML 内容校验成 :class:`GoldenCase` 列表。

    纯函数（除了检查文件是否存在），所以单测可以直接喂字典，
    不必为每种错误造一个临时目录。
    """
    if not isinstance(payload, list):
        raise GoldenConfigError(f"{source} 应该是一个用例列表（YAML 顶层是 '-'）。")

    base = Path(directory)
    cases: list[GoldenCase] = []
    seen: set[str] = set()
    for index, item in enumerate(payload, 1):
        if not isinstance(item, dict):
            raise GoldenConfigError(f"{source} 第 {index} 项不是映射。")

        unknown = sorted(set(item) - _CASE_KEYS)
        if unknown:
            raise GoldenConfigError(f"{source} 第 {index} 项有不认识的键：{', '.join(unknown)}")
        missing = [key for key in _REQUIRED_CASE_KEYS if not item.get(key)]
        if missing:
            raise GoldenConfigError(f"{source} 第 {index} 项缺少字段：{', '.join(missing)}")

        case_id = str(item["id"]).strip()
        if not _CASE_ID_PATTERN.match(case_id):
            raise GoldenConfigError(f"{source} 第 {index} 项的 id 格式不对：{case_id!r}（应为 G-001 这类）")
        if case_id in seen:
            raise GoldenConfigError(f"{source} 里 id 重复：{case_id}")
        seen.add(case_id)

        covers = tuple(str(code).strip() for code in item.get("covers") or ())
        for code in covers:
            if not _COVER_PATTERN.match(code):
                raise GoldenConfigError(f"{case_id} 的 covers 里有无法识别的编号：{code!r}")

        sql_path = base / str(item["golden"]).strip()
        if not sql_path.exists():
            raise GoldenConfigError(f"{case_id} 指向的黄金 SQL 不存在：{sql_path}")

        try:
            intent = intent_from_mapping(item["intent"])
        except ValueError as exc:
            raise GoldenConfigError(f"{case_id} 的 intent 写错了：{exc}") from exc

        cases.append(
            GoldenCase(
                id=case_id,
                shape=str(item["shape"]).strip(),
                intent=intent,
                sql_path=sql_path,
                covers=covers,
                eval_case=str(item.get("eval_case") or "").strip(),
                why_hard=str(item.get("why_hard") or "").strip(),
            )
        )

    # 孤儿文件：有 .sql 但没登记。否则"加了一条黄金 SQL 却没人跑"会静默发生。
    registered = {case.sql_path.name for case in cases}
    orphans = sorted(path.name for path in base.glob("*.sql") if path.name not in registered)
    if orphans:
        raise GoldenConfigError(f"{source} 没有登记这些黄金 SQL：{', '.join(orphans)}")

    return cases


def load_eval_case_ids(cases_dir: str | Path) -> list[str]:
    """读取评测集（``eval/cases/*.yaml``）里的用例编号，用于覆盖率交叉检查。"""
    directory = Path(cases_dir)
    if not directory.is_dir():
        raise GoldenConfigError(f"评测集目录不存在：{directory}")

    ids: list[str] = []
    for path in sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml")):
        payload = _load_yaml(path)
        items = payload if isinstance(payload, list) else (payload or {}).get("cases") or []
        ids.extend(str(item["id"]) for item in items if isinstance(item, dict) and item.get("id"))
    return ids


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """一条用例跑完的结果。"""

    case: GoldenCase
    #: 与黄金 SQL 是否结构等价。
    matches: bool
    #: 差异明细（``matches`` 为真时为空）。
    comparison: Comparison | None = None
    #: L4 校验是否通过。
    validation_ok: bool = True
    #: L4 报出的问题码。
    validation_codes: tuple[str, ...] = ()
    #: 编译期失败时记成 ``"stage/code"``；成功为空串。
    compile_error: str = ""

    @property
    def ok(self) -> bool:
        return not self.compile_error and self.matches and self.validation_ok

    @property
    def failure_kind(self) -> str:
        """失败归类。四类对应四个不同的责任人（见 compiler/errors.py 的思路）。"""
        if self.compile_error:
            return "编译失败"
        if not self.matches:
            return "与黄金 SQL 不一致"
        if not self.validation_ok:
            return "L4 校验未通过"
        return ""


def evaluate_case(
    case: GoldenCase,
    semantic: SemanticCatalog,
    catalog: Catalog,
    *,
    dialect: str = DEFAULT_DIALECT,
) -> CaseOutcome:
    """编译 → 比对 → 校验，三步都记下来。

    顺序刻意是"先编译后比对，再校验"：

    * 编译失败时不再比对（没有产出可比）
    * 校验永远跑（即使比对失败，也想知道这条 SQL 是否合规 —— 两个结论互相独立）
    """
    try:
        produced = compile_sql(case.intent, semantic, dialect=dialect)
    except CompileError as exc:
        return CaseOutcome(
            case=case,
            matches=False,
            compile_error=f"{exc.stage}/{exc.code}",
        )

    comparison = compare(case.golden_sql, produced, dialect=dialect)
    result = validate_sql(produced, catalog=catalog, dialect=dialect)
    return CaseOutcome(
        case=case,
        matches=comparison.matches,
        comparison=comparison,
        validation_ok=result.ok,
        validation_codes=tuple(result.codes),
    )
