"""M3.4 严格离线问数演示，不连接真实模型或外部业务库。"""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from app.engine.clarify.session import ClarificationService, SessionError, SQLiteSessionStore
from app.engine.compiler import CompileError
from app.engine.execution import ExecutionError, SQLiteExecutor
from app.engine.query import QueryService, QueryValidationError
from app.engine.scope import RowPolicy, Scope
from app.engine.semantic.bank_mini import TABLE, TABLE_COLUMNS, semantic_catalog
from app.engine.understanding import ExtractionError
from app.engine.validation.catalog import Catalog
from app.llm.protocol import LLMError
from app.llm.replay import ReplayLLM
from scripts.demo_session import DEFAULT_CASES_DIR
from scripts.record_cassette import find_case
from scripts.seed_sqlite_business import DEFAULT_DB

AQ_001_QUESTION = "2026年4月末全行贷款余额是多少？"
AQ_001_CASSETTE = Path("tests/cassettes/query_execution/AQ-001.json")
EXTRACTION_CASES_DIR = Path("tests/fixtures/intent_extraction")


def demo_scope(value: str) -> Scope:
    if value == "none":
        return Scope()
    if value == "all":
        return Scope.of([TABLE])
    return Scope.of([TABLE], row_policies=(RowPolicy(table=TABLE, column="org_no", values=(value,)),))


async def run(args: argparse.Namespace):
    business = args.business_db.resolve()
    session = args.session_db.resolve()
    if business == session or (session.exists() and business.exists() and session.samefile(business)):
        raise ExecutionError("EXECUTION_INVALID_CONFIG", "业务库和会话库必须使用不同的文件。")
    if args.command == "reply" and not session.is_file():
        raise SessionError("SESSION_NOT_FOUND", "本地会话数据库不存在。")
    if args.case == "AQ-001":
        text, cassette = AQ_001_QUESTION, AQ_001_CASSETTE
    elif args.case == "EX-009":
        case = find_case(args.case, EXTRACTION_CASES_DIR)
        text, cassette = case["question"], case["cassette"]
    else:
        case = find_case(args.case, DEFAULT_CASES_DIR)
        if args.command == "start":
            text, cassette = case["question"], case["start_cassette"]
        else:
            step = case["replies"][0]
            text, cassette = step["answer"], step["cassette"]

    executor = SQLiteExecutor(business, max_rows=args.max_rows)
    session.parent.mkdir(parents=True, exist_ok=True)
    llm = ReplayLLM(cassette, inner=None, strict=True)
    service = QueryService(
        ClarificationService(store=SQLiteSessionStore(session), llm=llm),
        semantic_catalog(),
        Catalog.from_mapping({"main": {TABLE: TABLE_COLUMNS}}),
        executor,
    )
    scope = demo_scope(args.scope)
    if args.command == "start":
        return await service.start(
            text, owner_key="local-query", catalog_revision="bank-mini-v1", scope=scope
        )
    return await service.reply(
        args.session_id,
        text,
        owner_key="local-query",
        catalog_revision="bank-mini-v1",
        expected_version=args.expected_version,
        scope=scope,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("start", "reply"):
        part = sub.add_parser(command)
        part.add_argument("--business-db", type=Path, default=DEFAULT_DB)
        part.add_argument("--session-db", type=Path, default=Path("build/query_sessions.db"))
        part.add_argument("--scope", choices=("all", "B001", "B002", "none"), default="all")
        part.add_argument("--max-rows", type=int, default=100)
        if command == "start":
            part.add_argument("--case", choices=("AQ-001", "EX-009", "SS-002"), default="AQ-001")
        else:
            part.add_argument("--case", choices=("SS-002",), default="SS-002")
            part.add_argument("--session-id", required=True)
            part.add_argument("--expected-version", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except (
        CompileError,
        ExecutionError,
        ExtractionError,
        LLMError,
        QueryValidationError,
        SessionError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, RuntimeError):
        print("无法打开本地演示所需的文件。", file=sys.stderr)
        return 1
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
