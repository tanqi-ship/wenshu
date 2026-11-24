"""M3.3严格离线会话演示；应用SQLite状态不是业务查询执行。

python -m scripts.demo_session start --case SS-001
python -m scripts.demo_session reply --case SS-001 --session-id <ID> --expected-version 1
python -m scripts.demo_session show --session-id <ID>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from app.engine.clarify.session import ClarificationService, SessionError, SQLiteSessionStore
from app.engine.semantic.bank_mini import semantic_catalog
from app.engine.understanding import ExtractionError
from app.llm.fake import FakeLLM
from app.llm.protocol import LLMError
from app.llm.replay import ReplayLLM
from scripts.record_cassette import find_case

DEFAULT_CASES_DIR = Path("tests/fixtures/clarification_session")
DEMO_OWNER = "local-demo"


async def run(args):
    path = Path(args.db)
    if args.command != "start" and not path.is_file():
        raise SessionError("SESSION_NOT_FOUND", "本地会话数据库不存在。")
    if args.command == "start":
        path.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteSessionStore(path)
    if args.command == "show":
        # 不构造回放客户端；Fake只是满足注入接口，show不会访问它。
        service = ClarificationService(store=store, llm=FakeLLM([]))
        return service.show(args.session_id, owner_key=DEMO_OWNER)
    case = find_case(args.case, Path(args.cases_dir))
    if args.command == "start":
        llm = ReplayLLM(case["start_cassette"], inner=None, strict=True)
        service = ClarificationService(store=store, llm=llm, max_rounds=args.max_rounds)
        return await service.start(
            case["question"], semantic_catalog(), owner_key=DEMO_OWNER, catalog_revision=args.catalog_revision
        )
    if args.step < 1 or args.step > len(case["replies"]):
        raise SessionError("SESSION_INPUT_INVALID", "该样本没有对应补答步骤。")
    step = case["replies"][args.step - 1]
    llm = ReplayLLM(step["cassette"], inner=None, strict=True)
    service = ClarificationService(store=store, llm=llm)
    return await service.reply(
        args.session_id,
        step["answer"],
        semantic_catalog(),
        owner_key=DEMO_OWNER,
        catalog_revision=args.catalog_revision,
        expected_version=args.expected_version,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线跨请求澄清演示，不执行业务SQL")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("start", "reply", "show"):
        part = sub.add_parser(command)
        part.add_argument("--db", default="build/clarification_sessions.db")
        if command != "start":
            part.add_argument("--session-id", required=True)
        if command != "show":
            part.add_argument("--case", default="SS-001")
            part.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
            part.add_argument("--catalog-revision", default="bank-mini-v1")
        if command == "start":
            part.add_argument("--max-rounds", type=int, default=2)
        if command == "reply":
            part.add_argument("--expected-version", type=int, required=True)
            part.add_argument("--step", type=int, default=1)
    args = parser.parse_args(argv)
    print("离线手写假响应会话演示：只读写应用状态，不执行业务SQL；slots_complete不代表可答或授权。")
    try:
        result = asyncio.run(run(args))
    except (SessionError, ExtractionError, LLMError, OSError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
